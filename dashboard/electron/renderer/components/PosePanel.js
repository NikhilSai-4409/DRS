/**
 * Camera Pose tab — the 9-point solvePnP capture workflow.
 *
 * This is the calibration the review pipeline PREFERS (resolve_projection):
 * a non-degenerate 9-point target spanning both creases and the striker's
 * stump tops, solved against the camera's ChArUco intrinsics. The 5-marker
 * homography remains only as a logged fallback — its geometry cannot
 * determine a projection (see assess_marker_geometry).
 *
 * Workflow: pick a camera → drag each numbered point onto its landmark on the
 * live frame → Solve. The backend's plausibility gate decides adoption; this
 * panel renders whatever it reports, including rejection reasons. A rejected
 * pose is SAVED but never used — the panel must say so, not celebrate.
 *
 * The point labels and world coordinates come from /api/calibration/pose/target,
 * never a local copy — the UI is a client of the contract, not a second source.
 */

const API_BASE = "http://localhost:8765";

// Display groups: indices into the target's ordered points. Colours only group
// the handles visually; the ORDER is the contract and comes from the backend.
const GROUPS = [
  { name: "Bowling crease", idx: [0, 1, 2], color: "#60a5fa" },
  { name: "Popping crease", idx: [3, 4, 5], color: "#f2b134" },
  { name: "Stump tops", idx: [6, 7, 8], color: "#ff4fa3" },
];

const groupColor = (i) => (GROUPS.find((g) => g.idx.includes(i)) || GROUPS[0]).color;

export class PosePanel {
  constructor(host) {
    this.host = host;
    this.active = false;
    this.cameras = [];
    this.cameraId = null;
    this.target = null;       // /pose/target payload
    this.status = null;       // /pose/{id} payload
    this.points = null;       // 9 [x, y] in FRAME pixels, or null until templated
    this.selected = 0;
    this.busy = false;
    this.message = "";
    this.liveTimer = null;
    this.natural = null;      // {w, h} of the live frame
    this.drag = null;
  }

  async activate() {
    this.active = true;
    if (!this.target) {
      try { this.target = await fetch(`${API_BASE}/api/calibration/pose/target`).then((r) => (r.ok ? r.json() : null)); }
      catch { this.target = null; }
      // Backend still warming up (camera init takes a few seconds after launch):
      // retry rather than leaving a labels-less panel until the next tab switch.
      if (!this.target && this.active) {
        clearTimeout(this.targetRetry);
        this.targetRetry = setTimeout(() => { if (this.active) this.activate(); }, 2000);
      }
    }
    try { this.cameras = (await fetch(`${API_BASE}/api/cameras/fps`).then((r) => r.json())).cameras || []; }
    catch { this.cameras = []; }
    if (this.cameraId == null && this.cameras.length) {
      const first = this.cameras.find((c) => c.connected) || this.cameras[0];
      this.cameraId = first.id;
    }
    await this.refreshStatus();
    this.render();
  }

  deactivate() { this.active = false; this.stopLive(); clearTimeout(this.targetRetry); }

  async refreshStatus() {
    if (this.cameraId == null) { this.status = null; return; }
    try { this.status = await fetch(`${API_BASE}/api/calibration/pose/${this.cameraId}`).then((r) => r.json()); }
    catch { this.status = null; }
  }

  async selectCamera(id) {
    if (id === this.cameraId) return;
    this.cameraId = id; this.points = null; this.natural = null; this.message = "";
    await this.refreshStatus();
    this.render();
  }

  /* ---------- template: sensible starting positions the operator corrects ---------- */
  templatePoints() {
    const { w, h } = this.natural;
    const row = (y, xs) => xs.map((fx) => [Math.round(w * fx), Math.round(h * y)]);
    return [
      ...row(0.86, [0.22, 0.5, 0.78]),   // bowling crease l/c/r (near, wide)
      ...row(0.72, [0.28, 0.5, 0.72]),   // popping crease l/c/r
      ...row(0.30, [0.46, 0.5, 0.54]),   // striker stump tops (far, narrow)
    ];
  }

  /* ---------- solve / clear ---------- */
  async solve() {
    if (this.busy || !this.points || this.cameraId == null) return;
    this.busy = true; this.message = "Solving pose…"; this.refreshFooter();
    try {
      const res = await fetch(`${API_BASE}/api/calibration/pose/${this.cameraId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          image_points: this.points,
          image_size: [this.natural.w, this.natural.h],
        }),
      });
      const data = await res.json();
      if (!res.ok) this.message = data.detail || "Solve failed.";
      else if (data.acceptable) this.message = `Pose accepted — reprojection ${data.reproj_error_px} px. The review pipeline is now using it.`;
      else this.message = "Pose saved but REJECTED by the plausibility gate — it is not being used. See the reasons on the right.";
      await this.refreshStatus();
    } catch { this.message = "Solve failed — is the backend running?"; }
    this.busy = false; this.render();
  }

  async clearPose() {
    if (this.busy || this.cameraId == null) return;
    if (!window.confirm(`Remove the saved pose for camera ${this.cameraId}? The review pipeline falls back to the ground homography.`)) return;
    this.busy = true; this.refreshFooter();
    try { await fetch(`${API_BASE}/api/calibration/pose/${this.cameraId}`, { method: "DELETE" }); } catch { /* ignore */ }
    this.message = "Pose removed.";
    await this.refreshStatus();
    this.busy = false; this.render();
  }

  /* ---------- render ---------- */
  render() {
    if (!this.active) return;
    this.stopLive();
    const st = this.status || {};
    const labels = (this.target && this.target.labels) || [];
    const needsIntrinsics = st.has_intrinsics === false;

    this.host.innerHTML = `
      <div class="intr-panel">
        <aside class="intr-cams">
          <h4>Camera</h4>
          <div class="intr-cam-list">${this.camListHtml()}</div>
          ${needsIntrinsics ? `
            <div class="pose-warn">No ChArUco intrinsics for this camera. The pose will be
            solved with an estimated lens and will usually be <strong>rejected</strong> —
            calibrate on the Camera Intrinsics tab first.</div>` : ""}
          <p class="adv-note muted">Drag each numbered point onto its landmark, then Solve.
          The order matters; the colours only group the rows.</p>
        </aside>

        <section class="intr-stage">
          <div class="intr-live-wrap pose-stage" id="pose-stage">
            <img id="pose-live" class="intr-live dead" alt="Live camera" draggable="false" />
            <div id="pose-overlay" class="pose-overlay"></div>
            <div class="intr-live-cap"><span id="pose-live-status" class="cal-live-status">Connecting to camera ${this.cameraId ?? "--"}…</span></div>
          </div>
          <div class="intr-actions">
            <button id="pose-solve" class="btn primary" type="button" ${this.points ? "" : "disabled"}>Solve pose</button>
            <button id="pose-reset" class="btn" type="button" ${this.points ? "" : "disabled"}>Reset points</button>
            <button id="pose-clear" class="btn ghost" type="button" ${st.has_pose ? "" : "disabled"}>Remove saved pose</button>
          </div>
        </section>

        <aside class="intr-side">
          <h4>Points (in order)</h4>
          <div class="pose-labels">${labels.map((label, i) => `
            <button type="button" class="pose-label ${i === this.selected ? "active" : ""}" data-i="${i}">
              <span class="pose-num" style="background:${groupColor(i)}">${i + 1}</span>${label}
            </button>`).join("")}</div>
          <h4 style="margin-top:16px">Result</h4>
          ${this.resultHtml()}
        </aside>
      </div>
      <div class="intr-foot"><span id="pose-msg" class="muted">${this.message || "Position the 9 points, then Solve. A pose that fails its plausibility checks is stored but never used."}</span></div>`;

    this.bind();
    this.startLive();
  }

  camListHtml() {
    if (!this.cameras.length) return `<span class="muted">No cameras.</span>`;
    return this.cameras.map((cam) => {
      const active = cam.id === this.cameraId ? "active" : "";
      let badge = "";
      if (cam.id === this.cameraId && this.status) {
        if (this.status.in_use) badge = `<span class="pill ok"><span class="d"></span>Pose in use</span>`;
        else if (this.status.has_pose) badge = `<span class="pill warn"><span class="d"></span>Rejected</span>`;
      }
      const stream = cam.connected ? `<span class="cr-stream"><span class="dot"></span></span>` : "";
      return `<button type="button" class="intr-cam ${active}" data-cam="${cam.id}">
        <span class="cp-name">Camera ${cam.id}</span>${stream}${badge}
      </button>`;
    }).join("");
  }

  resultHtml() {
    const st = this.status;
    if (!st || !st.has_pose) {
      return `<div class="adv-note muted">No pose saved for this camera. The review pipeline is using
        ${st && st.projection_source === "homography" ? "the ground-homography fallback" : "no projection"} —
        distances stay unmeasured until a pose is accepted.</div>`;
    }
    const ok = Boolean(st.acceptable);
    const reasons = st.rejection_reasons || [];
    const warnings = st.warnings || [];
    return `<div class="readout">
      <div class="rd"><div class="k">Status</div><div class="v ${ok ? "good" : "bad"}">${ok ? "ACCEPTED — in use" : "REJECTED — not used"}</div></div>
      <div class="rd"><div class="k">Reprojection</div><div class="v">${st.reproj_error_px != null ? st.reproj_error_px + " px" : "—"}</div></div>
      <div class="rd"><div class="k">Intrinsics</div><div class="v">${st.intrinsics_source || "—"}</div></div>
      <div class="rd"><div class="k">Pipeline source</div><div class="v">${st.projection_source || "—"}</div></div>
    </div>
    ${reasons.length ? `<div class="pose-warn">Rejected: ${reasons.join("; ")}</div>` : ""}
    ${warnings.length ? `<div class="adv-note muted">${warnings.join(" ")}</div>` : ""}`;
  }

  bind() {
    const q = (sel) => this.host.querySelector(sel);
    this.host.querySelectorAll(".intr-cam").forEach((el) => el.addEventListener("click", () => this.selectCamera(Number(el.dataset.cam))));
    this.host.querySelectorAll(".pose-label").forEach((el) => el.addEventListener("click", () => {
      this.selected = Number(el.dataset.i);
      this.host.querySelectorAll(".pose-label").forEach((b) => b.classList.toggle("active", Number(b.dataset.i) === this.selected));
      this.paintPoints();
    }));
    q("#pose-solve")?.addEventListener("click", () => this.solve());
    q("#pose-reset")?.addEventListener("click", () => { this.points = this.templatePoints(); this.paintPoints(); });
    q("#pose-clear")?.addEventListener("click", () => this.clearPose());

    const overlay = q("#pose-overlay");
    overlay?.addEventListener("pointerdown", (ev) => {
      const handle = ev.target.closest(".pose-pt");
      if (!handle) return;
      this.selected = Number(handle.dataset.i);
      this.drag = this.selected;
      overlay.setPointerCapture(ev.pointerId);
      ev.preventDefault();
    });
    overlay?.addEventListener("pointermove", (ev) => {
      if (this.drag == null || !this.points || !this.natural) return;
      const img = this.host.querySelector("#pose-live");
      const box = img.getBoundingClientRect();
      // display → frame pixels; clamp inside the frame
      const fx = ((ev.clientX - box.left) / box.width) * this.natural.w;
      const fy = ((ev.clientY - box.top) / box.height) * this.natural.h;
      this.points[this.drag] = [
        Math.max(0, Math.min(this.natural.w, Math.round(fx * 10) / 10)),
        Math.max(0, Math.min(this.natural.h, Math.round(fy * 10) / 10)),
      ];
      this.paintPoints();
    });
    overlay?.addEventListener("pointerup", () => { this.drag = null; });
  }

  paintPoints() {
    const overlay = this.host.querySelector("#pose-overlay");
    const img = this.host.querySelector("#pose-live");
    if (!overlay || !img || !this.points || !this.natural) return;
    const box = img.getBoundingClientRect();
    const stageBox = overlay.getBoundingClientRect();
    overlay.innerHTML = this.points.map(([fx, fy], i) => {
      const x = (fx / this.natural.w) * box.width + (box.left - stageBox.left);
      const y = (fy / this.natural.h) * box.height + (box.top - stageBox.top);
      return `<div class="pose-pt ${i === this.selected ? "active" : ""}" data-i="${i}"
        style="left:${x}px;top:${y}px;--pc:${groupColor(i)}">${i + 1}</div>`;
    }).join("");
  }

  refreshFooter() {
    const el = this.host.querySelector("#pose-msg");
    if (el) el.textContent = this.message;
  }

  /* ---------- live preview ---------- */
  startLive() {
    this.stopLive();
    const img = this.host.querySelector("#pose-live");
    if (!img || this.cameraId == null) return;
    img.onload = () => {
      const el = this.host.querySelector("#pose-live-status");
      if (img.naturalWidth > 0) {
        img.classList.remove("dead");
        if (el) { el.textContent = `LIVE — Camera ${this.cameraId}`; el.classList.remove("warn"); }
        if (!this.natural || this.natural.w !== img.naturalWidth) {
          this.natural = { w: img.naturalWidth, h: img.naturalHeight };
          if (!this.points) {
            this.points = this.templatePoints();
            const solveBtn = this.host.querySelector("#pose-solve");
            const resetBtn = this.host.querySelector("#pose-reset");
            if (solveBtn) solveBtn.disabled = false;
            if (resetBtn) resetBtn.disabled = false;
          }
        }
        this.paintPoints();
      }
    };
    img.onerror = () => {
      img.classList.add("dead");
      const el = this.host.querySelector("#pose-live-status");
      if (el) { el.textContent = `Camera ${this.cameraId} has no live feed.`; el.classList.add("warn"); }
    };
    const tick = () => { img.src = `${API_BASE}/api/live/${this.cameraId}.jpg?t=${Date.now()}`; };
    tick();
    this.liveTimer = setInterval(tick, 500);
    // The stage auto-fits the window, so a resize moves the displayed frame;
    // repaint immediately instead of waiting for the next 500 ms frame tick.
    this.resizeObs?.disconnect();
    const stage = this.host.querySelector("#pose-stage");
    if (stage && typeof ResizeObserver !== "undefined") {
      this.resizeObs = new ResizeObserver(() => this.paintPoints());
      this.resizeObs.observe(stage);
    }
  }
  stopLive() {
    if (this.liveTimer) { clearInterval(this.liveTimer); this.liveTimer = null; }
    this.resizeObs?.disconnect();
    this.resizeObs = null;
  }
}
