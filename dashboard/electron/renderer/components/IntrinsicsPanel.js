/**
 * Camera Intrinsics tab (Slice 2) — the real ChArUco lens-calibration workflow.
 *
 * Collect many board views → coverage map → Compute → save intrinsics_<id>.json →
 * inspect / replace. Drives the /api/calibration/intrinsics/* endpoints; the panel is
 * near-stateless and renders whatever the backend's status object reports.
 *
 * Distinct from the Pitch tab: intrinsics needs MANY views of one board from varied
 * angles, not a single frame with clicked landmarks.
 */

const API_BASE = "http://localhost:8765";

const COVERAGE = [
  ["left", "Left of frame"],
  ["center", "Centre"],
  ["right", "Right of frame"],
  ["near", "Near / large"],
  ["far", "Far / small"],
  ["tilted", "Tilted / angled"],
];

export class IntrinsicsPanel {
  constructor(host) {
    this.host = host;
    this.active = false;
    this.cameras = [];
    this.cameraId = null;
    this.status = null;       // last /status payload for the selected camera
    this.saved = null;        // last /intrinsics/{id} payload (inspect)
    this.liveTimer = null;
    this.liveOk = false;
    this.busy = false;
    this.message = "";
  }

  async activate() {
    this.active = true;
    await this.fetchCameras();
    if (this.cameraId == null && this.cameras.length) {
      const first = this.cameras.find((c) => c.connected) || this.cameras[0];
      this.cameraId = first.id;
    }
    await this.refreshStatus();
    this.render();
  }

  deactivate() {
    this.active = false;
    this.stopLive();
  }

  /* ---------- data ---------- */
  async fetchCameras() {
    try { this.cameras = (await fetch(`${API_BASE}/api/cameras/fps`).then((r) => r.json())).cameras || []; }
    catch { this.cameras = []; }
  }

  async refreshStatus() {
    if (this.cameraId == null) { this.status = null; this.saved = null; return; }
    try { this.status = await fetch(`${API_BASE}/api/calibration/intrinsics/${this.cameraId}/status`).then((r) => r.json()); }
    catch { this.status = null; }
    this.saved = null;
    if (this.status && this.status.calibrated) {
      try { this.saved = await fetch(`${API_BASE}/api/calibration/intrinsics/${this.cameraId}`).then((r) => (r.ok ? r.json() : null)); }
      catch { this.saved = null; }
    }
  }

  /* ---------- actions ---------- */
  async selectCamera(id) {
    if (id === this.cameraId) return;
    this.cameraId = id; this.message = "";
    await this.refreshStatus();
    this.render();
  }

  async capture() {
    if (this.cameraId == null || this.busy) return;
    this.busy = true; this.message = "Capturing…"; this.refreshFooter();
    try {
      const res = await fetch(`${API_BASE}/api/calibration/intrinsics/${this.cameraId}/capture`, { method: "POST" });
      if (res.status === 409) { this.message = (await res.json()).detail || "No live frame."; }
      else {
        const data = await res.json();
        this.message = data.accepted
          ? `Captured — ${data.captures} view${data.captures === 1 ? "" : "s"} (${data.corners} corners).`
          : `Not captured: ${data.reason}`;
        await this.refreshStatus();
      }
    } catch { this.message = "Capture failed — is the backend running?"; }
    this.busy = false; this.render();
  }

  async compute() {
    if (this.busy || !this.status || !this.status.ready) return;
    this.busy = true; this.message = "Computing intrinsics…"; this.refreshFooter();
    try {
      const res = await fetch(`${API_BASE}/api/calibration/intrinsics/${this.cameraId}/compute`, { method: "POST" });
      if (!res.ok) { this.message = (await res.json()).detail || "Compute failed."; }
      else { const data = await res.json(); this.message = `Calibrated — RMS ${data.rms_error} px over ${data.views_used} views.`; await this.refreshStatus(); }
    } catch { this.message = "Compute failed."; }
    this.busy = false; this.render();
  }

  async clear() {
    if (this.busy || this.cameraId == null) return;
    if (!window.confirm(`Discard the captured views for camera ${this.cameraId}?`)) return;
    this.busy = true; this.refreshFooter();
    try { await fetch(`${API_BASE}/api/calibration/intrinsics/${this.cameraId}/clear`, { method: "POST" }); }
    catch { /* ignore */ }
    this.message = "Captured views cleared."; await this.refreshStatus();
    this.busy = false; this.render();
  }

  /* ---------- render ---------- */
  render() {
    if (!this.active) return;
    this.stopLive();
    const s = this.status;
    const captures = s ? s.captures : 0;
    const minViews = s ? s.min_views : 8;
    const ready = Boolean(s && s.ready);
    const calibrated = Boolean(s && s.calibrated);
    const pct = Math.min(100, Math.round((captures / Math.max(minViews, 1)) * 100));

    this.host.innerHTML = `
      <div class="intr-panel">
        <aside class="intr-cams">
          <h4>Camera</h4>
          <div class="intr-cam-list">${this.camListHtml()}</div>
          <p class="adv-note muted">Lens calibration is per camera, done once. It's reused by every pitch calibration.</p>
        </aside>

        <section class="intr-stage">
          <div class="intr-live-wrap">
            <img id="intr-live" class="intr-live dead" alt="Live camera" />
            <div class="intr-live-cap"><span id="intr-live-status" class="cal-live-status">Connecting to camera ${this.cameraId ?? "--"}…</span></div>
          </div>
          <div class="intr-actions">
            <button id="intr-capture" class="btn primary" type="button" ${this.cameraId == null ? "disabled" : ""}>Capture board view</button>
            <button id="intr-compute" class="btn" type="button" ${ready ? "" : "disabled"}>Compute intrinsics</button>
            <button id="intr-clear" class="btn ghost" type="button" ${captures ? "" : "disabled"}>${calibrated ? "Replace (recapture)" : "Clear"}</button>
          </div>
        </section>

        <aside class="intr-side">
          <h4>Board views collected</h4>
          <div class="intr-collected"><span class="n">${captures}</span><span class="lbl">collected · compute at ${minViews}+</span></div>
          <div class="progress"><i style="width:${pct}%"></i></div>

          <h4 style="margin-top:16px">Coverage</h4>
          <div class="intr-cov">${this.coverageHtml()}</div>

          <h4 style="margin-top:16px">Result</h4>
          ${this.resultHtml()}
        </aside>
      </div>
      <div class="intr-foot"><span id="intr-msg" class="muted">${this.message || (calibrated ? "This camera has saved intrinsics." : "Move the board around the frame and capture varied views.")}</span></div>`;

    this.bind();
    this.startLive();
  }

  camListHtml() {
    if (!this.cameras.length) return `<span class="muted">No cameras.</span>`;
    return this.cameras.map((cam) => {
      const active = cam.id === this.cameraId ? "active" : "";
      const done = this.cameraId === cam.id && this.status ? this.status.calibrated : null;
      const badge = done === true ? `<span class="pill ok"><span class="d"></span>Calibrated</span>` : "";
      const stream = cam.connected ? `<span class="cr-stream"><span class="dot"></span></span>` : "";
      return `<button type="button" class="intr-cam ${active}" data-cam="${cam.id}">
        <span class="cp-name">Camera ${cam.id}</span>${stream}${badge}
      </button>`;
    }).join("");
  }

  coverageHtml() {
    const cov = (this.status && this.status.coverage) || {};
    const pos = (k, label) => `<span class="cov-pos ${cov[k] ? "done" : "miss"}" title="${label} of frame">${cov[k] ? "✓" : "○"}<i>${label}</i></span>`;
    const attr = (k, label) => `<div class="cov-attr ${cov[k] ? "done" : "miss"}"><span>${cov[k] ? "✓" : "○"}</span>${label}</div>`;
    // A little frame diagram: position across the frame (top row) + distance/angle (below),
    // so the operator sees at a glance WHERE the board still needs to go.
    return `
      <div class="cov-frame" title="Where the board has been seen">
        <div class="cov-row">${pos("left", "Left")}${pos("center", "Centre")}${pos("right", "Right")}</div>
        <div class="cov-attrs">${attr("near", "Near")}${attr("far", "Far")}${attr("tilted", "Tilted")}</div>
      </div>`;
  }

  resultHtml() {
    const src = this.saved;
    if (!src || !src.camera_matrix) {
      return `<div class="adv-note muted">Not calibrated yet — collect views and Compute. Distortion is measured from the board, never guessed.</div>`;
    }
    const m = src.camera_matrix, d = (src.distortion_coeffs && src.distortion_coeffs[0]) || [];
    const rms = src.rms_error;
    return `<div class="readout">
      <div class="rd"><div class="k">RMS reproj.</div><div class="v good">${rms != null ? Number(rms).toFixed(2) + " px" : "—"}</div></div>
      <div class="rd"><div class="k">Focal fx / fy</div><div class="v">${Math.round(m[0][0])} / ${Math.round(m[1][1])}</div></div>
      <div class="rd"><div class="k">Principal pt</div><div class="v">${Math.round(m[0][2])}, ${Math.round(m[1][2])}</div></div>
      <div class="rd"><div class="k">Distortion k1</div><div class="v">${d.length ? Number(d[0]).toFixed(3) : "—"}</div></div>
    </div>
    <div class="adv-note muted">Saved to intrinsics_${this.cameraId}.json${src.created_at ? " · " + src.created_at.slice(0, 10) : ""}.</div>`;
  }

  bind() {
    const q = (sel) => this.host.querySelector(sel);
    this.host.querySelectorAll(".intr-cam").forEach((el) => el.addEventListener("click", () => this.selectCamera(Number(el.dataset.cam))));
    q("#intr-capture")?.addEventListener("click", () => this.capture());
    q("#intr-compute")?.addEventListener("click", () => this.compute());
    q("#intr-clear")?.addEventListener("click", () => this.clear());
  }

  refreshFooter() {
    const el = this.host.querySelector("#intr-msg");
    if (el) el.textContent = this.message;
  }

  /* ---------- live preview ---------- */
  startLive() {
    this.stopLive();
    this.liveOk = false;
    const img = this.host.querySelector("#intr-live");
    if (!img || this.cameraId == null) return;
    img.onload = () => { this.liveOk = img.naturalWidth > 0; this.setLiveStatus(); };
    img.onerror = () => { this.liveOk = false; this.setLiveStatus(); };
    const tick = () => { img.src = `${API_BASE}/api/live/${this.cameraId}.jpg?t=${Date.now()}`; };
    tick();
    this.liveTimer = setInterval(tick, 250);
  }
  stopLive() { if (this.liveTimer) { clearInterval(this.liveTimer); this.liveTimer = null; } }

  setLiveStatus() {
    this.host.querySelector("#intr-live")?.classList.toggle("dead", !this.liveOk);
    const el = this.host.querySelector("#intr-live-status");
    if (!el) return;
    if (this.liveOk) { el.textContent = `LIVE — Camera ${this.cameraId}`; el.classList.remove("warn"); }
    else { el.textContent = `Camera ${this.cameraId} has no live feed — check it is connected and streaming.`; el.classList.add("warn"); }
  }
}
