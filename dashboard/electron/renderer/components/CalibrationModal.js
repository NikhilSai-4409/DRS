/**
 * Full-screen Calibration Workspace (rendered into the Calibration view, not a modal).
 *
 * Three columns: cameras + readiness + role assignment (left), a dominant live /
 * captured preview with draggable numbered markers, cursor-following zoom and world
 * coordinates (center), and a calibration score breakdown + camera sync + Engineer
 * Mode diagnostics (right). Drives the real 5-marker pitch calibration endpoints.
 *
 * The manual pitch calibration is homography-only, so lens distortion / intrinsics /
 * extrinsics are surfaced honestly as "Not modeled" under Engineer Mode rather than
 * faked.
 */

const API_BASE = "http://localhost:8765";

const MARKERS = [
  { key: "off_stump", label: "Off stump base" },
  { key: "middle_stump", label: "Middle stump base" },
  { key: "leg_stump", label: "Leg stump base" },
  { key: "bowling_crease", label: "Bowling crease line" },
  { key: "popping_crease", label: "Popping crease line" },
];
const STEPS = ["Camera", "Capture", "Mark points", "Verify", "Save"];
const CAMERA_ROLES = ["Ball Tracking", "Front Foot", "Wide Camera", "Replay Camera", "Stump Camera", "Broadcast Camera", "Reserve"];

export class CalibrationWorkspace {
  constructor(host, opts = {}) {
    this.host = host;
    this.onRoleChange = opts.onRoleChange || (() => {});
    this.getRole = opts.getRole || (() => "Reserve");
    this.active = false;
    this.boardSetup = localStorage.getItem("drs.calibrationBoardSetup") === "dual" ? "dual" : "single";
    this.showAllCams = false;
    this.resetState();
  }

  resetState() {
    this.cameraId = null;
    this.cameras = [];
    this.profiles = [];
    this.sync = null;
    this.liveTimer = null;
    this.solveTimer = null;
    this.capturedUrl = "";
    this.capturedImage = null;
    this.captured = false;
    this.imageW = 1280;
    this.imageH = 720;
    this.markers = {};
    this.proposed = false;       // markers came from auto-detect (uncertain/yellow)
    this.selected = null;
    this.homography = null;
    this.errorCm = null;
    this.quality = null;
    this.score = null;
    this.saved = false;
    this.engineer = false;
    this.advanced = { grid: false, axes: false, reproj: false, world: false };
    this.detecting = false;
    this.dragKey = null;
    this.cursor = null;          // {x,y} image coords for zoom-follow
  }

  /* ---------- lifecycle (called by the router) ---------- */
  async activate() {
    this.active = true;
    await Promise.all([this.fetchCameras(), this.fetchProfiles(), this.fetchSync()]);
    if (this.cameraId == null && this.cameras.length) {
      const first = this.cameras.find((c) => c.connected) || this.cameras.find((c) => this.profileFor(c.id)) || this.cameras[0];
      this.cameraId = first.id;
    }
    this.render();
  }

  deactivate() {
    this.active = false;
    this.stopLive();
    if (this.solveTimer) clearTimeout(this.solveTimer);
  }

  // legacy entry point (sidebar used to open a modal) — now just ensures render
  open() { return this.activate(); }
  close() { this.deactivate(); }

  /* ---------- data ---------- */
  async fetchCameras() {
    try { this.cameras = (await fetch(`${API_BASE}/api/cameras/fps`).then((r) => r.json())).cameras || []; }
    catch { this.cameras = []; }
  }
  async fetchProfiles() {
    try {
      const data = await fetch(`${API_BASE}/api/calibration/profiles`).then((r) => r.json());
      this.profiles = data.profiles || [];
      if (!this.cameras.length && Array.isArray(data.configured_cameras)) {
        this.cameras = data.configured_cameras.map((id) => ({ id, connected: false }));
      }
    } catch { this.profiles = []; }
  }
  async fetchSync() {
    try { this.sync = (await fetch(`${API_BASE}/api/health`).then((r) => r.json())).sync || null; }
    catch { this.sync = null; }
  }
  profileFor(id) { return this.profiles.find((p) => p.camera_id === id) || null; }

  readiness(id) {
    const p = this.profileFor(id);
    if (!p) return { level: "missing", label: "Not calibrated", icon: "✕" };
    const lvl = p.quality.level;
    if (lvl === "excellent" || lvl === "good") return { level: "ready", label: "Ready", icon: "✔" };
    return { level: "recapture", label: "Needs recapture", icon: "⚠" };
  }

  currentStep() {
    if (this.saved) return 4;
    if (this.homography) return 3;
    if (Object.keys(this.markers).length >= 5) return 2;
    if (this.captured) return 2;
    if (this.cameraId != null) return 1;
    return 0;
  }

  /* ---------- render ---------- */
  render() {
    if (!this.active) return;
    this.stopLive();
    const step = this.currentStep();
    this.host.innerHTML = `
      <div class="cal-workspace">
        <header class="cws-head">
          <div class="cws-title"><strong>Camera Calibration</strong><small>You only need one ChArUco board. The counter below shows pitch reference points, not boards.</small></div>
          <ol class="wiz-steps">
            ${STEPS.map((label, i) => `<li class="wiz-step ${i === step ? "active" : ""} ${i < step ? "done" : ""}"><i>${i < step ? "✓" : i + 1}</i><span>${label}</span></li>`).join("")}
          </ol>
          <label class="eng-toggle"><input type="checkbox" id="cws-eng" ${this.engineer ? "checked" : ""}/> Engineer Mode</label>
        </header>
        <div class="cws-body">
          <aside class="cws-left">${this.leftRail()}</aside>
          <section class="cws-stage">${this.stage()}</section>
          <aside class="cws-right">${this.rightRail()}</aside>
        </div>
        <footer class="cws-foot">
          <span class="wiz-hint">${this.hint()}</span>
          <div class="wiz-foot-actions">${this.actions()}</div>
        </footer>
      </div>`;
    this.bind();
    if (!this.captured) this.startLive();
    else this.afterStageRender();
  }

  leftRail() {
    // Config lists every possible camera; only show the ones that matter (connected,
    // calibrated, or currently selected) unless the operator asks for the full list.
    const active = this.cameras.filter((c) => c.connected || this.profileFor(c.id) || c.id === this.cameraId);
    const shown = this.showAllCams || !active.length ? this.cameras : active;
    const hiddenCount = this.cameras.length - shown.length;
    const cams = shown.map((cam) => {
      const r = this.readiness(cam.id);
      const role = this.getRole(cam.id);
      const options = CAMERA_ROLES.map((ro) => `<option ${ro === role ? "selected" : ""}>${ro}</option>`).join("");
      return `
        <div class="cws-cam ${cam.id === this.cameraId ? "active" : ""}">
          <button type="button" class="cws-cam-pick" data-cam="${cam.id}">
            <span class="ready-dot ${r.level}">${r.icon}</span>
            <span class="cp-name">Camera ${cam.id}</span>
            <span class="cp-q ${r.level}">${r.label}</span>
          </button>
          <label class="cws-role">Role
            <select class="cws-role-select" data-cam="${cam.id}">${options}</select>
          </label>
        </div>`;
    }).join("") || `<span class="muted">No cameras.</span>`;
    const camSummary = this.cameras.length
      ? `<div class="adv-note muted">${active.length} of ${this.cameras.length} configured cameras active (connected or calibrated).</div>`
      : "";
    const camToggle = this.cameras.length && (hiddenCount > 0 || this.showAllCams)
      ? `<button type="button" id="cws-showall" class="cws-showall">${this.showAllCams ? "Show active cameras only" : `Show all ${this.cameras.length} cameras`}</button>`
      : "";
    return `
      <div class="rail-block">
        <h4>Calibration setup</h4>
        <label class="adv-toggle"><input type="radio" name="cws-board" value="single" ${this.boardSetup === "single" ? "checked" : ""}/> I have one ChArUco board</label>
        <label class="adv-toggle"><input type="radio" name="cws-board" value="dual" ${this.boardSetup === "dual" ? "checked" : ""}/> I have two ChArUco boards</label>
        <div class="adv-note muted">This only changes the capture instructions. Calibration quality depends on your captured images, not this setting.</div>
      </div>
      <div class="rail-block"><h4>Cameras &amp; roles</h4><div class="cws-cam-list">${cams}</div>${camSummary}${camToggle}</div>
      <div class="rail-block"><h4>Saved profiles</h4><div class="profile-cards">${this.profileCards()}</div></div>`;
  }

  stage() {
    if (!this.captured) {
      return `
        <div class="stage-live">
          <img id="cal-live" class="cal-frame" alt="Live camera" />
          <div class="cal-scanline"></div>
          <div class="stage-caption">
            <span id="cal-live-status" class="cal-live-status">Connecting to camera ${this.cameraId ?? "--"}…</span>
            Step 1: capture ONE image ${this.boardSetup === "dual" ? "with both boards visible" : "containing your calibration board"}
          </div>
        </div>`;
    }
    const placed = Object.keys(this.markers).length;
    return `
      <div class="stage-place">
        <div class="cal-canvas" id="cal-canvas">
          <div class="cal-imgwrap" id="cal-imgwrap">
            <img id="cal-still" class="cal-frame" src="${this.capturedUrl}" alt="Captured frame" />
            <canvas id="cal-overlay" class="cal-overlay"></canvas>
            <div id="cal-markers" class="cal-markers">${this.markersHtml()}</div>
            <div id="cal-cross" class="cal-cross" hidden></div>
          </div>
          ${this.detecting ? `<div class="detect-bar"><div class="detect-fill" id="detect-fill"></div><span id="detect-label">Detecting reference points…</span></div>` : ""}
        </div>
        <div class="stage-caption" id="cal-caption">Pitch reference points: ${placed} / 5 — click to drop the next, drag to adjust</div>
      </div>`;
  }

  markersHtml() {
    return MARKERS.map((m, i) => {
      const pt = this.markers[m.key];
      if (!pt) return "";
      const left = (pt.x / this.imageW) * 100, top = (pt.y / this.imageH) * 100;
      const cls = this.proposed ? "proposed" : "placed";
      return `<i class="cal-pin ${cls} ${this.selected === m.key ? "sel" : ""}" data-key="${m.key}" style="left:${left}%;top:${top}%" title="${m.label}">${i + 1}</i>`;
    }).join("");
  }

  rightRail() {
    const s = this.score;
    const overall = s ? s.overall : 0;
    const stars = s ? s.stars : 0;
    const starHtml = [1, 2, 3, 4, 5].map((n) => `<span class="${n <= stars ? "on" : ""}">★</span>`).join("");
    const row = (label, val, level) => `<div class="bd-row"><span>${label}</span><strong class="${level || ""}">${val}</strong></div>`;
    const sub = (v) => (v >= 0.85 ? "Excellent" : v >= 0.65 ? "Good" : v >= 0.45 ? "Fair" : "Poor");
    const lvl = (v) => (v >= 0.85 ? "excellent" : v >= 0.65 ? "good" : v >= 0.45 ? "fair" : "poor");
    return `
      <div class="rail-block">
        <h4>Calibration score</h4>
        <div class="score-big ${s ? lvl(overall / 100) : ""}">${s ? overall : "--"}<small>/ 100</small></div>
        <div class="quality-stars ${s ? lvl(overall / 100) : ""}">${starHtml}</div>
        <div class="score-breakdown">
          ${row("RMS error", this.errorCm != null ? `${Number(this.errorCm).toFixed(2)} cm` : "--", s ? lvl(s.rms) : "")}
          ${row("RMS", s ? sub(s.rms) : "--", s ? lvl(s.rms) : "")}
          ${row("Coverage", s ? sub(s.coverage) : "--", s ? lvl(s.coverage) : "")}
          ${row("Visibility", s ? sub(s.visibility) : "--", s ? lvl(s.visibility) : "")}
          ${row("Distortion", "Not modeled", "muted")}
        </div>
      </div>
      <div class="rail-block">
        <h4>Inspection</h4>
        <canvas id="cal-zoom" class="cal-zoom" width="240" height="170"></canvas>
        <div id="cal-world" class="cal-world muted">Hover the image to read world coordinates.</div>
      </div>
      <div class="rail-block">
        <h4>Camera sync</h4>
        <div class="sync-list">${this.syncRows()}</div>
      </div>
      ${this.engineer ? `
      <div class="rail-block">
        <h4>Engineer diagnostics</h4>
        <label class="adv-toggle"><input type="checkbox" data-adv="grid" ${this.advanced.grid ? "checked" : ""}/> Homography grid</label>
        <label class="adv-toggle"><input type="checkbox" data-adv="axes" ${this.advanced.axes ? "checked" : ""}/> Coordinate axes</label>
        <label class="adv-toggle"><input type="checkbox" data-adv="reproj" ${this.advanced.reproj ? "checked" : ""}/> Reprojection points</label>
        <label class="adv-toggle"><input type="checkbox" data-adv="world" ${this.advanced.world ? "checked" : ""}/> World coordinates</label>
        <div class="adv-note muted">Lens distortion · intrinsics · extrinsics: not modeled (homography calibration).</div>
      </div>` : ""}`;
  }

  syncRows() {
    const conn = this.cameras.filter((c) => c.connected);
    if (!conn.length) return `<span class="muted">No connected cameras.</span>`;
    const spread = this.sync && this.sync.spread_ms != null ? Number(this.sync.spread_ms) : null;
    const within = this.sync ? this.sync.within_tolerance : null;
    const head = spread != null ? `<div class="sync-head"><span>Cross-camera spread</span><strong class="${within ? "ok" : "warn"}">${spread.toFixed(1)} ms ${within ? "✓" : "⚠"}</strong></div>` : "";
    const rows = conn.map((c) => {
      const ms = c.last_frame_age_ms != null ? Number(c.last_frame_age_ms) : (c.latency_ms != null ? Number(c.latency_ms) : null);
      const ok = ms == null ? true : ms < 60;
      return `<div class="sync-row"><span class="ready-dot ${ok ? "ready" : "recapture"}">${ok ? "✓" : "⚠"}</span><strong>Cam ${c.id}</strong><span class="muted">${ms != null ? ms.toFixed(1) + " ms" : "--"}</span></div>`;
    }).join("");
    return head + rows;
  }

  profileCards() {
    if (!this.profiles.length) return `<span class="muted">No saved profiles yet.</span>`;
    return this.profiles.map((p) => `
      <div class="profile-card ${p.quality.level}">
        <div class="pc-top"><strong>Camera ${p.camera_id}</strong><span class="ready-dot ${this.readiness(p.camera_id).level}">${this.readiness(p.camera_id).icon}</span></div>
        <div class="pc-q">${p.quality.label}</div>
        <div class="pc-meta"><span>${Number(p.homography_error_cm).toFixed(2)} cm</span><span>${p.marker_count} ref points</span></div>
        <button type="button" class="pc-remove" data-remove-profile="${p.camera_id}">Remove</button>
      </div>`).join("");
  }

  hint() {
    if (!this.captured) return this.boardSetup === "dual"
      ? "Step 1: Select a camera and capture one image with both boards visible."
      : "Step 1: Select a camera and capture ONE image containing your calibration board — one board is enough.";
    if (Object.keys(this.markers).length < 5) return "Step 2: Click the 5 pitch reference points on that image — the count is clicked points, not boards.";
    if (!this.saved) return "Drag markers to refine — the score updates live. Save when you're happy.";
    return "Saved. The camera is match-ready.";
  }

  actions() {
    const remove = this.profileFor(this.cameraId) ? `<button id="cws-delete" class="danger" type="button">Remove Saved Profile</button>` : "";
    if (!this.captured) return `${remove}<button id="cws-capture" class="primary" type="button" ${this.cameraId == null ? "disabled" : ""}>Capture Frame</button>`;
    const placed = Object.keys(this.markers).length;
    const recap = `<button id="cws-recapture" type="button">Recapture</button>`;
    const auto = `<button id="cws-auto" type="button">Auto Detect</button>`;
    if (placed < 5) return `${remove}${recap}${auto}`;
    const save = `<button id="cws-save" class="primary success" type="button" ${this.homography ? "" : "disabled"}>Save Calibration</button>`;
    return `${remove}${recap}${auto}${save}`;
  }

  /* ---------- bind ---------- */
  bind() {
    const q = (s) => this.host.querySelector(s);
    q("#cws-eng")?.addEventListener("change", (e) => { this.engineer = e.target.checked; if (!this.engineer) this.advanced = { grid: false, axes: false, reproj: false, world: false }; this.refreshRight(); this.drawOverlays(); });
    this.host.querySelectorAll('[name="cws-board"]').forEach((el) => el.addEventListener("change", () => {
      this.boardSetup = el.value === "dual" ? "dual" : "single";
      localStorage.setItem("drs.calibrationBoardSetup", this.boardSetup);
      this.render();
    }));
    q("#cws-showall")?.addEventListener("click", () => { this.showAllCams = !this.showAllCams; this.render(); });
    this.host.querySelectorAll(".cws-cam-pick").forEach((el) => el.addEventListener("click", () => this.selectCamera(Number(el.dataset.cam))));
    this.host.querySelectorAll(".cws-role-select").forEach((el) => el.addEventListener("change", () => this.onRoleChange(Number(el.dataset.cam), el.value)));
    q("#cws-capture")?.addEventListener("click", () => this.captureFrame());
    q("#cws-recapture")?.addEventListener("click", () => { this.captured = false; this.markers = {}; this.homography = null; this.quality = null; this.score = null; this.errorCm = null; this.render(); });
    q("#cws-auto")?.addEventListener("click", () => this.autoDetect());
    q("#cws-save")?.addEventListener("click", () => this.save());
    q("#cws-delete")?.addEventListener("click", () => this.deleteProfile(this.cameraId));
    this.host.querySelectorAll("[data-remove-profile]").forEach((el) => el.addEventListener("click", (event) => {
      event.stopPropagation();
      this.deleteProfile(Number(el.dataset.removeProfile));
    }));
    this.host.querySelectorAll("[data-adv]").forEach((el) => el.addEventListener("change", () => { this.advanced[el.dataset.adv] = el.checked; this.drawOverlays(); }));
    const canvas = q("#cal-canvas");
    if (canvas) {
      canvas.addEventListener("pointerdown", (e) => this.onPointerDown(e));
      canvas.addEventListener("pointermove", (e) => this.onPointerMove(e));
      canvas.addEventListener("pointerleave", () => { this.cursor = null; const cr = this.host.querySelector("#cal-cross"); if (cr) cr.hidden = true; });
      window.removeEventListener("pointerup", this._up);
      this._up = () => { this.dragKey = null; };
      window.addEventListener("pointerup", this._up);
    }
  }

  refreshRight() {
    const rail = this.host.querySelector(".cws-right");
    if (!rail) return;
    rail.innerHTML = this.rightRail();
    rail.querySelectorAll("[data-adv]").forEach((el) => el.addEventListener("change", () => { this.advanced[el.dataset.adv] = el.checked; this.drawOverlays(); }));
    this.updateZoom();
  }

  refreshFooter() {
    const foot = this.host.querySelector(".wiz-foot-actions");
    if (foot) { foot.innerHTML = this.actions(); const q = (s) => this.host.querySelector(s); q("#cws-auto")?.addEventListener("click", () => this.autoDetect()); q("#cws-save")?.addEventListener("click", () => this.save()); q("#cws-delete")?.addEventListener("click", () => this.deleteProfile(this.cameraId)); q("#cws-recapture")?.addEventListener("click", () => { this.captured = false; this.markers = {}; this.render(); }); }
    const hint = this.host.querySelector(".wiz-hint");
    if (hint) hint.textContent = this.hint();
  }

  /* ---------- actions ---------- */
  selectCamera(id) {
    this.cameraId = id;
    this.captured = false; this.markers = {}; this.proposed = false;
    this.homography = null; this.errorCm = null; this.quality = null; this.score = null; this.saved = false;
    const p = this.profileFor(id);
    if (p && p.markers) { for (const m of MARKERS) { const s = p.markers[m.key]; if (s) this.markers[m.key] = { x: Number(s.x), y: Number(s.y) }; } if (p.image_size) { this.imageW = p.image_size[0] || this.imageW; this.imageH = p.image_size[1] || this.imageH; } }
    this.render();
  }

  startLive() {
    this.stopLive();
    this.liveOk = false;
    const img = this.host.querySelector("#cal-live");
    if (!img || this.cameraId == null) return;
    // A dead feed (camera not streaming) used to leave a silent black frame that reads
    // like broken camera selection — surface the real state instead.
    img.onload = () => { this.liveOk = img.naturalWidth > 0; this.setLiveStatus(); };
    img.onerror = () => { this.liveOk = false; this.setLiveStatus(); };
    const tick = () => { img.src = `${API_BASE}/api/live/${this.cameraId}.jpg?t=${Date.now()}`; };
    tick();
    this.liveTimer = setInterval(tick, 250);
  }
  stopLive() { if (this.liveTimer) { clearInterval(this.liveTimer); this.liveTimer = null; } }

  setLiveStatus() {
    const el = this.host.querySelector("#cal-live-status");
    if (!el) return;
    if (this.liveOk) { el.textContent = `LIVE — Camera ${this.cameraId}`; el.classList.remove("warn"); }
    else { el.textContent = `Camera ${this.cameraId} has no live feed — check that it is connected and streaming.`; el.classList.add("warn"); }
  }

  captureFrame() {
    const img = this.host.querySelector("#cal-live");
    // Don't manufacture a blank 1280x720 frame from a dead feed — that produced an
    // all-black "captured" image that looked like the capture button was stuck.
    if (!img || !img.naturalWidth || !this.liveOk) {
      this.setLiveStatus();
      const el = this.host.querySelector("#cal-live-status");
      if (el) { el.textContent = `No live frame from camera ${this.cameraId} yet — can't capture. Check that the camera is connected and streaming.`; el.classList.add("warn"); }
      return;
    }
    const w = img.naturalWidth, h = img.naturalHeight;
    this.imageW = w; this.imageH = h;
    try { const c = document.createElement("canvas"); c.width = w; c.height = h; c.getContext("2d").drawImage(img, 0, 0, w, h); this.capturedUrl = c.toDataURL("image/jpeg", 0.9); }
    catch { this.capturedUrl = `${API_BASE}/api/live/${this.cameraId}.jpg?t=${Date.now()}`; }
    this.stopLive();
    this.captured = true;
    this.render();
  }

  afterStageRender() {
    this.capturedImage = new Image();
    this.capturedImage.onload = () => { this.drawOverlays(); this.updateZoom(); };
    this.capturedImage.src = this.capturedUrl;
    requestAnimationFrame(() => { this.drawOverlays(); this.updateZoom(); });
  }

  onPointerDown(e) {
    const pin = e.target.closest(".cal-pin");
    if (pin) { this.dragKey = pin.dataset.key; this.selected = pin.dataset.key; this.proposed = false; this.markMarkers(); this.updateZoom(); return; }
    const next = MARKERS.find((m) => !this.markers[m.key]);
    if (!next) return;
    this.markers[next.key] = this.toImage(e);
    this.selected = next.key; this.proposed = false;
    this.markMarkers(); this.updateZoom(); this.refreshFooter(); this.scheduleSolve();
  }

  onPointerMove(e) {
    this.cursor = this.toImage(e);
    this.updateZoom(e);
    if (this.advanced.world) this.showWorld();
    const cross = this.host.querySelector("#cal-cross");
    const wrap = this.host.querySelector("#cal-imgwrap");
    if (cross && wrap) { const r = wrap.getBoundingClientRect(); cross.hidden = false; cross.style.left = `${((e.clientX - r.left) / r.width) * 100}%`; cross.style.top = `${((e.clientY - r.top) / r.height) * 100}%`; }
    if (!this.dragKey) return;
    this.markers[this.dragKey] = this.toImage(e);
    this.proposed = false;
    this.markMarkers(); this.scheduleSolve();
  }

  toImage(e) {
    const wrap = this.host.querySelector("#cal-imgwrap") || this.host.querySelector("#cal-canvas");
    const r = wrap.getBoundingClientRect();
    return { x: Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * this.imageW, y: Math.max(0, Math.min(1, (e.clientY - r.top) / r.height)) * this.imageH };
  }

  async autoDetect() {
    this.detecting = true; this.render();
    const fill = this.host.querySelector("#detect-fill"), label = this.host.querySelector("#detect-label");
    let n = 0;
    const iv = setInterval(() => { n += 1; if (fill) fill.style.width = `${n * 20}%`; if (label) label.textContent = `Detecting reference points… ${n}/5`; }, 120);
    try {
      const data = await fetch(`${API_BASE}/api/calibration/auto-detect`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ camera_id: this.cameraId, image_size: [this.imageW, this.imageH] }) }).then((r) => r.json());
      await new Promise((r) => setTimeout(r, 650));
      clearInterval(iv);
      this.markers = {};
      for (const m of MARKERS) { const p = data.markers?.[m.key]; if (p) this.markers[m.key] = { x: p.x, y: p.y }; }
      this.proposed = true;
    } catch { clearInterval(iv); }
    this.detecting = false;
    this.render();
    this.scheduleSolve();
  }

  scheduleSolve() {
    if (Object.keys(this.markers).length < 5) { this.homography = null; this.score = null; this.quality = null; this.errorCm = null; this.refreshRight(); this.refreshFooter(); return; }
    if (this.solveTimer) clearTimeout(this.solveTimer);
    this.solveTimer = setTimeout(() => this.solve(), 280);
  }

  async solve() {
    if (Object.keys(this.markers).length < 5) return;
    const markers = {}; for (const m of MARKERS) markers[m.key] = { x: this.markers[m.key].x, y: this.markers[m.key].y };
    try {
      const data = await fetch(`${API_BASE}/api/calibration/compute`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ markers }) }).then((r) => r.json());
      this.homography = data.homography || null;
      this.errorCm = data.homography_error_cm;
      this.quality = data.quality;
      this.score = this.computeScore();
      this.refreshRight(); this.drawOverlays(); this.refreshFooter();
    } catch { const cap = this.host.querySelector("#cal-caption"); if (cap) cap.textContent = "Could not solve — check the markers."; }
  }

  computeScore() {
    const pts = Object.values(this.markers);
    if (pts.length < 5) return null;
    const xs = pts.map((p) => p.x), ys = pts.map((p) => p.y);
    const spread = ((Math.max(...xs) - Math.min(...xs)) / this.imageW + (Math.max(...ys) - Math.min(...ys)) / this.imageH);
    const coverage = Math.max(0, Math.min(1, spread / 0.7));
    const visibility = pts.filter((p) => p.x >= 0 && p.x <= this.imageW && p.y >= 0 && p.y <= this.imageH).length / 5;
    const rms = this.errorCm == null ? 0 : Math.max(0, Math.min(1, 1 - this.errorCm / 5));
    const overall = Math.round(100 * (0.5 * rms + 0.3 * coverage + 0.2 * visibility));
    const stars = overall >= 90 ? 5 : overall >= 75 ? 4 : overall >= 60 ? 3 : overall >= 40 ? 2 : 1;
    return { overall, rms, coverage, visibility, stars };
  }

  async save() {
    if (!this.homography) return;
    const markers = {}; for (const m of MARKERS) markers[m.key] = { x: this.markers[m.key].x, y: this.markers[m.key].y };
    try {
      const data = await fetch(`${API_BASE}/api/calibration/save`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ camera_id: this.cameraId, markers, image_size: [this.imageW, this.imageH] }) }).then((r) => r.json());
      this.saved = true; this.errorCm = data.homography_error_cm ?? this.errorCm; this.proposed = false;
      await this.fetchProfiles();
      this.render();
    } catch { const cap = this.host.querySelector("#cal-caption"); if (cap) cap.textContent = "Save failed."; }
  }

  async deleteProfile(cameraId) {
    if (cameraId == null) return;
    if (!window.confirm(`Remove saved calibration for camera ${cameraId}?`)) return;
    try {
      await fetch(`${API_BASE}/api/calibration/cameras/${cameraId}`, { method: "DELETE" }).then((r) => {
        if (!r.ok) throw new Error("Delete failed");
        return r.json();
      });
      await this.fetchProfiles();
      if (cameraId === this.cameraId) {
        this.captured = false;
        this.markers = {};
        this.proposed = false;
        this.homography = null;
        this.errorCm = null;
        this.quality = null;
        this.score = null;
        this.saved = false;
      }
      this.render();
    } catch {
      const cap = this.host.querySelector("#cal-caption");
      if (cap) cap.textContent = "Could not remove saved profile.";
    }
  }

  /* ---------- light updaters ---------- */
  markMarkers() {
    const host = this.host.querySelector("#cal-markers");
    if (host) host.innerHTML = this.markersHtml();
    const placed = Object.keys(this.markers).length;
    const cap = this.host.querySelector("#cal-caption");
    if (cap) cap.textContent = `Pitch reference points: ${placed} / 5 — click to drop the next, drag to adjust`;
    this.drawOverlays();
  }

  updateZoom(e) {
    const canvas = this.host.querySelector("#cal-zoom");
    if (!canvas || !this.capturedImage) return;
    const at = this.cursor || (this.selected ? this.markers[this.selected] : null);
    if (!at) return;
    const ctx = canvas.getContext("2d");
    const zoom = 6, sw = canvas.width / zoom, sh = canvas.height / zoom;
    const sx = Math.max(0, Math.min(this.imageW - sw, at.x - sw / 2));
    const sy = Math.max(0, Math.min(this.imageH - sh, at.y - sh / 2));
    ctx.imageSmoothingEnabled = false;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    try { ctx.drawImage(this.capturedImage, sx, sy, sw, sh, 0, 0, canvas.width, canvas.height); } catch {}
    ctx.strokeStyle = "#3b82f6"; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(canvas.width / 2, 0); ctx.lineTo(canvas.width / 2, canvas.height); ctx.moveTo(0, canvas.height / 2); ctx.lineTo(canvas.width, canvas.height / 2); ctx.stroke();
  }

  drawOverlays() {
    const canvas = this.host.querySelector("#cal-overlay");
    const wrap = this.host.querySelector("#cal-imgwrap");
    if (!canvas || !wrap) return;
    const w = wrap.clientWidth, h = wrap.clientHeight;
    canvas.width = w; canvas.height = h;
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, w, h);
    if (!this.homography) return;
    const sx = w / this.imageW, sy = h / this.imageH;
    if (this.advanced.reproj) {
      ctx.fillStyle = "rgba(34,197,94,0.9)";
      for (const m of MARKERS) { const p = this.markers[m.key]; if (p) { ctx.beginPath(); ctx.arc(p.x * sx, p.y * sy, 3, 0, 7); ctx.fill(); } }
    }
    if (!this.advanced.grid && !this.advanced.axes) return;
    const inv = mat3inv(this.homography);
    if (!inv) return;
    const toC = (lat, along) => { const [px, py] = applyH(inv, lat + 0.1143, along); return [px * sx, py * sy]; };
    if (this.advanced.grid) {
      ctx.strokeStyle = "rgba(59,130,246,0.5)"; ctx.lineWidth = 1;
      for (let lat = -1.0; lat <= 1.0001; lat += 0.5) { ctx.beginPath(); for (let a = 0.2; a >= -1.6; a -= 0.1) { const [x, y] = toC(lat, a); a === 0.2 ? ctx.moveTo(x, y) : ctx.lineTo(x, y); } ctx.stroke(); }
      for (let a = 0.0; a >= -1.5001; a -= 0.5) { ctx.beginPath(); for (let lat = -1.0; lat <= 1.0; lat += 0.1) { const [x, y] = toC(lat, a); lat === -1.0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y); } ctx.stroke(); }
    }
    if (this.advanced.axes) {
      const [ox, oy] = toC(0, 0), [xx, xy] = toC(0.5, 0), [zx, zy] = toC(0, -0.7);
      ctx.lineWidth = 2;
      ctx.strokeStyle = "#22c55e"; ctx.beginPath(); ctx.moveTo(ox, oy); ctx.lineTo(xx, xy); ctx.stroke();
      ctx.strokeStyle = "#ef4444"; ctx.beginPath(); ctx.moveTo(ox, oy); ctx.lineTo(zx, zy); ctx.stroke();
    }
  }

  showWorld() {
    const out = this.host.querySelector("#cal-world");
    if (!out || !this.cursor) return;
    if (!this.homography) { out.textContent = "Solve first to read world coordinates."; return; }
    const [wx, wy] = applyH(this.homography, this.cursor.x, this.cursor.y);
    out.textContent = `lateral ${((wx - 0.1143) * 1000).toFixed(0)} mm · along ${(wy * 1000).toFixed(0)} mm`;
  }
}

// Backwards-compatible alias (older import name).
export const CalibrationModal = CalibrationWorkspace;

function applyH(h, x, y) {
  const wx = h[0][0] * x + h[0][1] * y + h[0][2];
  const wy = h[1][0] * x + h[1][1] * y + h[1][2];
  const ww = h[2][0] * x + h[2][1] * y + h[2][2];
  return [wx / ww, wy / ww];
}

function mat3inv(m) {
  const [a, b, c] = m[0], [d, e, f] = m[1], [g, h, i] = m[2];
  const A = e * i - f * h, B = -(d * i - f * g), C = d * h - e * g;
  const det = a * A + b * B + c * C;
  if (Math.abs(det) < 1e-12) return null;
  const id = 1 / det;
  return [
    [A * id, (c * h - b * i) * id, (b * f - c * e) * id],
    [B * id, (a * i - c * g) * id, (c * d - a * f) * id],
    [C * id, (b * g - a * h) * id, (a * e - b * d) * id],
  ];
}
