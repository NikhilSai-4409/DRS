import { watchAnalysisJob, pollAnalysisJob } from "../hooks/useAnalysisJob.js";
import { loadCalibrationProfiles } from "../hooks/useCalibrationProfiles.js";
import { ObservedTrajectoryRenderer } from "./ObservedTrajectoryRenderer.js";

const API_BASE = "http://localhost:8765";
const STEPS = ["Extracting frames...", "Detecting ball...", "Tracking...", "Predicting trajectory...", "Running LBW analysis...", "Complete"];

// Review types shown in the wizard. Only `ready` types are selectable and actually
// run through the backend analyze pipeline (LBW today). The rest are shown with a
// status + reason so the page scales as each becomes testable — it never pretends
// a review type works before its pipeline is wired.
const REVIEW_TYPES = [
  { key: "lbw", label: "LBW", state: "ready", reason: "Ball tracking → pitching / impact / wickets (full pipeline + replay)" },
  { key: "wide", label: "Wide", state: "ready", reason: "Wide-line geometry via the shared engine — needs a calibration profile for measurements" },
  { key: "noball", label: "No Ball", state: "ready", reason: "Front-foot overstep via the shared engine — needs front-foot footage + calibration" },
  { key: "edge", label: "Edge", state: "ready", reason: "Frame-timing proxy only until a stump mic exists — results carry that warning" },
  { key: "runout", label: "Run Out", state: "ready", reason: "Crease/bat geometry via the shared engine — bails detection pending, verify on replay" },
  { key: "stumping", label: "Stumping", state: "ready", reason: "Crease/bat geometry via the shared engine — gloves/collection manual-check" },
];
const STATE_ICON = { ready: "🟢", soon: "🟡", locked: "🔒" };

export class TestingPanel {
  constructor(root) {
    this.root = root;
    this.videoFile = null;
    this.videoInfo = null;
    this.reviewType = "lbw";
    this.calibrationMode = "heuristic";   // "heuristic" | "calibrated"
    this.profileId = "";
    this.profiles = [];
    this.models = [];
    this.modelPath = "";
    this.frameStride = 1;   // High-FPS sampling: process every Nth frame
    this.imgsz = 640;       // detection resolution (match the model's training size)
    this.jobWatcher = null;
    this.pollStop = null;
  }

  async render() {
    this.root.innerHTML = this.template();
    this.bind();
    await Promise.all([this.loadProfiles(), this.loadModels()]);
    this.syncUi();
  }

  template() {
    return `
      <article class="card testing-wizard">
        <header class="card-h">
          <div><strong>Testing</strong><small>Upload a delivery clip and run a review against the DRS engine</small></div>
          <span class="chip-quiet">Engineer tool</span>
        </header>
        <div class="tw-body">
          <div class="tw-config">
          <section class="tw-step">
            <div class="tw-step-h"><span class="tw-num">1</span><strong>Upload Delivery</strong></div>
            <label class="drop-zone">
              <input id="tw-video" type="file" accept=".mp4,.avi,.mov,.MTS,.mts" />
              <strong>Upload Video</strong>
              <span>Drop or select .mp4, .avi, .mov, .MTS</span>
            </label>
            <section class="uploaded-video" hidden>
              <video muted playsinline controls></video>
              <div><strong class="video-name"></strong><span class="video-meta"></span></div>
            </section>
          </section>

          <section class="tw-step">
            <div class="tw-step-h"><span class="tw-num">2</span><strong>Review Type</strong></div>
            <div class="tw-review-types" id="tw-review-types"></div>
          </section>

          <section class="tw-step">
            <div class="tw-step-h"><span class="tw-num">3</span><strong>Calibration</strong></div>
            <div class="tw-calib">
              <label class="tw-radio"><input type="radio" name="tw-calib" value="heuristic" checked /> <span><strong>Heuristic</strong><small>no calibration · approx ±15 cm</small></span></label>
              <label class="tw-radio"><input type="radio" name="tw-calib" value="calibrated" /> <span><strong>Use Calibration</strong><small>true 3D from a saved pitch profile</small></span></label>
              <select id="tw-profile" disabled></select>
            </div>
          </section>

          <section class="tw-step">
            <div class="tw-step-h"><span class="tw-num">4</span><strong>Model</strong></div>
            <select id="tw-model"><option value="">Loading models…</option></select>
            <small class="tw-hint">The model you pick here is the one actually loaded for analysis.</small>
          </section>

          <section class="tw-step">
            <div class="tw-step-h"><span class="tw-num">5</span><strong>Analyze</strong></div>
            <label class="tw-stride">
              <span>High-FPS sampling</span>
              <select id="tw-stride">
                <option value="1">Every frame (full rate)</option>
                <option value="2">Every 2nd frame</option>
                <option value="4">Every 4th frame (≈120→30 fps)</option>
                <option value="8">Every 8th frame (≈240→30 fps)</option>
              </select>
            </label>
            <label class="tw-stride">
              <span>Detection resolution</span>
              <select id="tw-imgsz">
                <option value="640" selected>640 (recommended)</option>
                <option value="960">960</option>
                <option value="1280">1280 (wide shots)</option>
                <option value="1536">1536 (tiny / fast ball)</option>
              </select>
            </label>
            <button id="tw-analyze" type="button" class="primary-action" disabled>Analyze</button>
            <div class="analysis-progress"><i style="width:0%"></i></div>
            <span class="analysis-status" id="tw-status">Upload a delivery to begin</span>
          </section>
          </div>

          <section class="tw-step tw-results-step">
            <div class="tw-step-h"><span class="tw-num">✓</span><strong>Results</strong></div>
            <div id="tw-results" class="inline-results"><span class="muted">No analysis yet.</span></div>
          </section>
        </div>
      </article>
    `;
  }

  bind() {
    this.root.querySelector("#tw-video").addEventListener("change", (event) => this.loadVideo(event));
    this.root.querySelector("#tw-analyze").addEventListener("click", () => this.analyze());
    this.root.querySelectorAll('input[name="tw-calib"]').forEach((radio) => {
      radio.addEventListener("change", () => { this.calibrationMode = radio.value; this.syncUi(); });
    });
    this.root.querySelector("#tw-profile").addEventListener("change", (event) => { this.profileId = event.target.value; this.syncUi(); });
    this.root.querySelector("#tw-model").addEventListener("change", (event) => this.onModelChange(event.target.value));
    this.root.querySelector("#tw-stride").addEventListener("change", (event) => { this.frameStride = Number(event.target.value) || 1; });
    this.root.querySelector("#tw-imgsz").addEventListener("change", (event) => { this.imgsz = Number(event.target.value) || 1280; });
    this.renderReviewTypes();
  }

  renderReviewTypes() {
    const host = this.root.querySelector("#tw-review-types");
    host.innerHTML = REVIEW_TYPES.map((rt) => `
      <button type="button" class="tw-rt ${rt.state} ${rt.key === this.reviewType ? "active" : ""}"
        data-rt="${rt.key}" ${rt.state === "ready" ? "" : "disabled"} title="${rt.reason}">
        <span class="tw-rt-ic">${STATE_ICON[rt.state]}</span>
        <span class="tw-rt-main"><strong>${rt.label}</strong><small>${rt.state === "ready" ? "Ready" : rt.reason}</small></span>
      </button>
    `).join("");
    host.querySelectorAll(".tw-rt:not([disabled])").forEach((button) => {
      button.addEventListener("click", () => { this.reviewType = button.dataset.rt; this.renderReviewTypes(); this.syncUi(); });
    });
  }

  async loadProfiles() {
    const select = this.root.querySelector("#tw-profile");
    try {
      this.profiles = await loadCalibrationProfiles();
      select.innerHTML = this.profiles.length
        ? this.profiles.map((profile) => `<option value="${profile.id}">${profile.name} · ${Number(profile.rms_error_px || 0).toFixed(1)}px</option>`).join("")
        : `<option value="">No calibration profiles — create one in Calibration</option>`;
    } catch {
      this.profiles = [];
      select.innerHTML = `<option value="">Backend offline</option>`;
    }
  }

  // Populate the Model dropdown from the model REGISTRY (single source of truth),
  // grouped by type, plus a Browse option. The selected path is sent with Analyze so
  // it's the model that runs; the registry id is used for promotion.
  async loadModels() {
    const select = this.root.querySelector("#tw-model");
    const LABEL = { production: "Production", candidate: "Candidates", experiment: "Experiments", other: "Models", previous: "Previous", archive: "Archived" };
    const ORDER = ["production", "candidate", "experiment", "other", "previous", "archive"];
    try {
      const data = await (await fetch(`${API_BASE}/api/models`)).json();
      this.registryModels = data.models || [];
      const groups = {};
      for (const m of this.registryModels) (groups[m.type] ||= []).push(m);
      const optgroups = ORDER.filter((t) => groups[t]).map((t) => `<optgroup label="${LABEL[t] || t}">${
        groups[t].map((m) => `<option value="${m.path}">${m.name}${m.map50 != null ? ` · mAP50 ${Number(m.map50).toFixed(2)}` : ""}${m.size_mb != null ? ` · ${m.size_mb} MB` : ""}</option>`).join("")
      }</optgroup>`).join("");
      select.innerHTML = `${optgroups || `<option value="">No models found</option>`}<optgroup label="Custom"><option value="__browse__">Browse for a .pt…</option></optgroup>`;
      const prod = this.registryModels.find((m) => m.is_production);
      this.modelPath = prod?.path || this.registryModels[0]?.path || "";
      if (this.modelPath) select.value = this.modelPath;
    } catch {
      select.innerHTML = `<option value="">Backend offline</option>`;
      this.registryModels = [];
      this.modelPath = "";
    }
    this.syncUi();
  }

  async onModelChange(value) {
    const select = this.root.querySelector("#tw-model");
    if (value === "__browse__") {
      const picked = window.drs?.pickModelFile ? await window.drs.pickModelFile() : null;
      if (picked) {
        const option = document.createElement("option");
        option.value = picked;
        option.textContent = `${picked.split(/[\\/]/).pop()} (custom)`;
        select.querySelector('optgroup[label="Custom"]')?.prepend(option);
        select.value = picked;
        this.modelPath = picked;
      } else {
        select.value = this.modelPath;   // cancelled → keep previous selection
      }
    } else {
      this.modelPath = value;
    }
    this.syncUi();
  }

  loadVideo(event) {
    const file = event.target.files?.[0];
    if (!file) return;
    this.videoFile = file;
    const url = URL.createObjectURL(file);
    const video = this.root.querySelector(".uploaded-video video");
    const holder = this.root.querySelector(".uploaded-video");
    video.src = url;
    video.addEventListener("loadedmetadata", () => {
      this.videoInfo = { name: file.name, duration: video.duration, resolution: `${video.videoWidth || "--"}x${video.videoHeight || "--"}` };
      this.root.querySelector(".video-name").textContent = file.name;
      this.root.querySelector(".video-meta").textContent = `${formatDuration(video.duration)} · ${this.videoInfo.resolution}`;
      holder.hidden = false;
      this.syncUi();
    }, { once: true });
  }

  // Enable/disable controls from the current selections; keep the status line honest.
  syncUi() {
    const calibrated = this.calibrationMode === "calibrated";
    const profileSelect = this.root.querySelector("#tw-profile");
    profileSelect.disabled = !calibrated || !this.profiles.length;
    if (calibrated && this.profiles.length && !this.profileId) this.profileId = this.profiles[0].id;
    const typeReady = REVIEW_TYPES.find((r) => r.key === this.reviewType)?.state === "ready";
    // Only the full LBW pipeline needs an explicit model pick; per-type module runs
    // use the shared detector loaded by the engine.
    const needsModel = this.reviewType === "lbw";
    const ready = Boolean(this.videoFile) && typeReady && (!needsModel || Boolean(this.modelPath)) && (!calibrated || Boolean(this.profileId));
    this.root.querySelector("#tw-analyze").disabled = !ready;
    const status = this.root.querySelector("#tw-status");
    if (!this.videoFile) status.textContent = "Upload a delivery to begin";
    else if (calibrated && !this.profiles.length) status.textContent = "No calibration profile — switch to Heuristic or create one in Calibration";
    else status.textContent = "Ready to analyze";
  }

  async analyze() {
    if (!this.videoFile) return;
    // Non-LBW types run ONLY their own module: upload-only endpoint (no pipeline),
    // then /api/testing/jobs/{id}/review/{type} through the shared ReviewEngine.
    // Selecting Wide never executes LBW code.
    if (this.reviewType !== "lbw") { await this.runTypedReview(); return; }
    const calibrated = this.calibrationMode === "calibrated";
    this.setProgress({ step: STEPS[0], percent: 5 });
    this.root.querySelector("#tw-analyze").disabled = true;
    const form = new FormData();
    form.append("video", this.videoFile);
    // edge_detection on: for LBW we also want the UltraEdge (proxy) trace so we can
    // see whether the ball touched the bat. replay_generation renders the DRS animation.
    const analysisOptions = {
      edge_detection: true,
      replay_generation: true,
      // honor the wizard's calibration radio: Heuristic must NOT be silently overridden by
      // a stale camera-0 profile (its speed gate can invalidate good tracks)
      use_calibration: this.calibrationMode === "calibrated",
    };
    if (this.modelPath) analysisOptions.model_path = this.modelPath;   // load the picked model
    if (this.frameStride > 1) analysisOptions.frame_stride = this.frameStride;   // high-FPS sampling
    analysisOptions.imgsz = this.imgsz;   // detection resolution
    form.append("options_json", JSON.stringify(analysisOptions));
    let route = "/api/analyze";
    if (calibrated) { route = "/api/analyze/calibrated"; form.append("calibration_profile_id", this.profileId); }
    try {
      const response = await fetch(`${API_BASE}${route}`, { method: "POST", body: form });
      if (!response.ok) { this.setProgress({ step: `Analysis failed: HTTP ${response.status}`, percent: 0 }); this.syncUi(); return; }
      const payload = await response.json();
      await this.watchJob(payload.job_id);
    } catch (error) {
      this.setProgress({ step: `Analysis failed: ${error.message}`, percent: 0 });
      this.syncUi();
    }
  }

  // Upload → run exactly one review module → render its typed verdict/measurements.
  // The whole path is the shared ReviewEngine (identical to live Request Review),
  // just fed from the uploaded clip. No LBW pipeline, no replay render.
  async runTypedReview() {
    const typeLabel = REVIEW_TYPES.find((r) => r.key === this.reviewType)?.label || this.reviewType;
    const analyzeBtn = this.root.querySelector("#tw-analyze");
    if (analyzeBtn) analyzeBtn.disabled = true;
    this.setProgress({ step: `Uploading for ${typeLabel} review…`, percent: 15 });
    try {
      const form = new FormData();
      form.append("video", this.videoFile);
      const up = await fetch(`${API_BASE}/api/testing/uploads`, { method: "POST", body: form });
      if (!up.ok) { this.setProgress({ step: `Upload failed: HTTP ${up.status}`, percent: 0 }); this.syncUi(); return; }
      const { job_id: jobId } = await up.json();
      this.setProgress({ step: `Running ${typeLabel} analysis…`, percent: 55 });
      const run = await fetch(`${API_BASE}/api/testing/jobs/${jobId}/review/${this.reviewType}`, { method: "POST" });
      if (!run.ok) {
        const detail = (await run.json().catch(() => ({}))).detail || `HTTP ${run.status}`;
        this.setProgress({ step: `${typeLabel} review failed: ${detail}`, percent: 0 });
        this.syncUi();
        return;
      }
      const payload = await run.json();
      this.setProgress({ step: "Complete", percent: 100 });
      this.renderTypedReview(typeLabel, payload);
    } catch (error) {
      this.setProgress({ step: `${typeLabel} review failed: ${error.message}`, percent: 0 });
    }
    this.syncUi();
  }

  renderTypedReview(typeLabel, payload) {
    const analysis = payload.analysis || {};
    const rr = analysis.review_result || {};
    const summary = analysis.summary || rr.summary || {};
    const verdict = rr.verdict || summary.headline || "AWAITING";
    const cls = /OUT|WIDE|NO BALL|EDGE/.test(verdict) && !/NOT /.test(verdict) ? "out" : "not_out";
    const measurements = rr.measurements || summary.measurements || [];
    const warnings = rr.warnings || summary.warnings || [];
    const host = this.root.querySelector("#tw-results");
    if (!host) return;
    host.innerHTML = `
      <section class="result-card ${cls === "out" ? "out" : "umpires_call"}">
        <strong>${typeLabel.toUpperCase()}: ${verdict.replace(/_/g, " ")}</strong>
        <span>Video: ${payload.video || "--"}</span>
        ${rr.confidence != null ? `<span>Confidence: ${Math.round(rr.confidence * 100)}%</span>` : ""}
        <hr />
        ${measurements.map((m) => `<span>${m.label}: <b ${m.flag ? 'style="color:#ff6b6b"' : ""}>${m.value}</b></span>`).join("")}
        ${analysis.explanation ? `<span class="tw-typed-expl">${analysis.explanation}</span>` : ""}
      </section>
      ${warnings.length ? `<div class="tw-noreplay">${warnings.map((w) => `⚠ ${w}`).join("<br/>")}</div>` : ""}
      <small class="tw-hint">Ran ONLY the ${typeLabel} module through the shared ReviewEngine — the same code the live Request Review executes for this type.</small>`;
  }

  async watchJob(jobId) {
    this.jobWatcher?.close?.();
    this.pollStop?.();
    this.jobWatcher = watchAnalysisJob(jobId, {
      onProgress: (payload) => this.setProgress(payload),
      onDecision: () => this.loadResults(jobId),
      onAnimation: () => this.loadResults(jobId),
      onError: (message) => { this.setProgress({ step: message, percent: 0 }); this.syncUi(); },
    });
    this.pollStop = await pollAnalysisJob(jobId, {
      onProgress: (payload) => this.setProgress(payload),
      onComplete: () => this.loadResults(jobId),
      onError: (message) => { this.setProgress({ step: message, percent: 0 }); this.syncUi(); },
    });
  }

  async loadResults(jobId) {
    try {
      const response = await fetch(`${API_BASE}/api/analyze/${jobId}/results`);
      if (response.ok) this.renderResults(await response.json(), jobId);
    } catch {}
    this.syncUi();
  }

  setProgress(progress) {
    const percent = Number(progress.percent ?? progress.progress ?? 0);
    this.root.querySelectorAll(".analysis-progress i").forEach((bar) => { bar.style.width = `${percent}%`; });
    const status = this.root.querySelector("#tw-status");
    if (status) status.textContent = progress.step || progress.current_step || "Processing";
  }

  renderResults(results, jobId) {
    // Tear down any previous observed-trajectory overlay (its rAF sync loop + listeners).
    cancelAnimationFrame(this._obsRaf);
    this.obsRenderer?.destroy?.();
    this.obsRenderer = null;

    const verdict = results.decision?.verdict || "UMPIRES_CALL";
    const gates = results.lbw_gates || {};
    const edge = results.edge_analysis || {};
    const host = this.root.querySelector("#tw-results");
    host.innerHTML = `
      <section class="result-card ${verdict.toLowerCase()}">
        <strong>DECISION: ${verdict.replace(/_/g, " ")}</strong>
        <span>Model: ${this.modelPath ? this.modelPath.split(/[\\/]/).pop() : "default"}</span>
        <span>Confidence: ${pct(results.decision?.confidence)}</span>
        <span>Ball speed: ${results.summary?.ball_speed_kmh ?? "--"} km/h</span>
        <span>Bounce: ${point(results.trajectory?.bounce_point)}</span>
        <span>Impact height: ${gates.impact?.height_m ?? "--"} m</span>
        <hr />
        <span>Pitching: ${gates.pitching?.result || "--"} (${pct(gates.pitching?.confidence)})</span>
        <span>Impact: ${gates.impact?.result || "--"} (${pct(gates.impact?.confidence)})</span>
        <span>Wickets: ${gates.wickets?.result || "--"} (${pct(gates.wickets?.confidence)})</span>
        ${edge.edge_probability != null ? `<span>Edge (proxy): ${pct(edge.edge_probability)}</span>` : ""}
      </section>
      <section class="tw-obs" hidden>
        <div class="tw-obs-h"><strong>Observed Trajectory</strong><small>With players · real footage + tracked overlay</small></div>
        <div id="tw-replay1" class="tw-replay" hidden>
          <video class="tw-replay-video" muted playsinline controls autoplay loop></video>
        </div>
        <div id="tw-noreplay" class="tw-noreplay" hidden></div>
        <details class="tw-obs-eng" open>
          <summary>Engineering inspector (image space · raw video is master)</summary>
          <div class="tw-obs-stage">
            <video class="tw-obs-video" muted playsinline controls></video>
            <canvas class="tw-obs-canvas"></canvas>
          </div>
          <div class="tw-obs-ctl">
            <label><input type="checkbox" data-layer="centres" checked/> Ball centres</label>
            <label><input type="checkbox" data-layer="trajectory" checked/> Trajectory</label>
            <label><input type="checkbox" data-layer="confidence"/> Confidence colour</label>
            <label><input type="checkbox" data-layer="impact"/> Impact marker</label>
            <label><input type="checkbox" data-inspect-toggle/> Inspect (click to seek)</label>
            <span class="tw-obs-inspect" data-inspect>Enable inspect, then click a point.</span>
          </div>
        </details>
      </section>
      <section id="tw-review" class="tw-replay-sec" hidden>
        <div class="tw-obs-h"><strong>DRS REVIEW</strong><small>Clean broadcast replay · pitch corridor · gates · verdict</small></div>
        <video class="tw-replay-video" muted playsinline controls autoplay loop></video>
      </section>
      <section id="tw-diag" class="tw-diag"></section>
      <a class="tw-legacy-anim" href="${API_BASE}/api/analyze/${jobId}/animation" target="_blank" rel="noopener">Download DRS review MP4</a>
      <div class="tw-result-actions">
        <button id="tw-promote" type="button" class="tw-promote-btn">Promote model to Production</button>
        <small class="tw-hint">Copies the model above into models/production (the live DRS model); the current production is archived for rollback.</small>
      </div>
      <style>
        .tw-obs{margin:14px 0;border:1px solid #24352c;border-radius:12px;padding:14px;background:#0f1813}
        .tw-replay video,.tw-replay-sec video{width:100%;height:auto;display:block;border-radius:8px;background:#000}
        .tw-noreplay{margin:8px 0;padding:10px 12px;border:1px solid #7a5b1e;border-radius:8px;background:#221a08;color:#f2b134;font-size:13px}
        .tw-replay-sec{margin:14px 0;border:1px solid #24352c;border-radius:12px;padding:14px;background:#0f1813}
        .tw-obs-eng{margin-top:10px}
        .tw-obs-eng summary{cursor:pointer;color:#86a091;font-size:13px;margin-bottom:8px}
        .tw-obs-h{display:flex;justify-content:space-between;align-items:baseline;gap:12px;margin-bottom:10px}
        .tw-obs-h small{color:#86a091}
        .tw-obs-stage{position:relative;line-height:0;background:#000;border-radius:8px;overflow:hidden}
        .tw-obs-video{width:100%;height:auto;display:block}
        .tw-obs-canvas{position:absolute;inset:0;pointer-events:none}
        .tw-obs-canvas.inspect{pointer-events:auto;cursor:crosshair}
        .tw-obs-ctl{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:center;margin-top:10px;font-size:13px;color:#cfe6d6}
        .tw-obs-ctl label{display:inline-flex;gap:6px;align-items:center;cursor:pointer}
        .tw-obs-inspect{color:#86a091;font-family:ui-monospace,Menlo,monospace;font-size:12px;margin-left:auto}
        .tw-diag-head{display:flex;justify-content:space-between;align-items:baseline;gap:10px;margin:2px 0 8px}
        .tw-diag-head .tw-stars{color:#f2b134;letter-spacing:1px;font-size:15px}
        .tw-endreason{color:#5ce65a;font:600 12px ui-monospace,Menlo,monospace}
      </style>
    `;
    // Broadcast replay package: two pipeline-rendered videos (with players / clean pitch),
    // both generated from the same reconstruction so they always show the identical delivery.
    const exports = results.exports || {};
    const r1 = this.root.querySelector("#tw-replay1");
    if (r1 && exports.replay_players) {
      r1.hidden = false;
      r1.querySelector("video").src = `${API_BASE}/api/testing/jobs/${jobId}/exports/replay_players`;
    }
    const r2 = this.root.querySelector("#tw-review");
    if (r2 && exports.replay_review) {
      r2.hidden = false;
      r2.querySelector("video").src = `${API_BASE}/api/testing/jobs/${jobId}/exports/replay_review`;
    }
    // NEVER be silent about a missing replay — say exactly why the pipeline skipped it.
    const noR = this.root.querySelector("#tw-noreplay");
    if (noR && !exports.replay_players) {
      const t = results.trajectory || {};
      const why = t.valid === false
        ? `trajectory rejected: ${(t.reasons || []).join("; ") || (t.observed?.end_reason || "invalid")}`
        : "replay generation failed (see backend log)";
      noR.hidden = false;
      noR.textContent = `No broadcast replay for this delivery — ${why}. ` +
        `If this was gated by an implausible calibrated speed, re-run with Calibration set to Heuristic.`;
    }
    this.renderDiagnostics(results);
    this.mountObservedTrajectory(results);
    this.root.querySelector("#tw-promote")?.addEventListener("click", () => this.promote());
  }

  // Overlay the observed trajectory on the RAW uploaded video (not the annotated export,
  // so nothing is baked). The video is master; the canvas passively mirrors currentTime.
  mountObservedTrajectory(results) {
    const sec = this.root.querySelector(".tw-obs");
    const obs = results.trajectory?.observed;
    if (!sec || !obs || !Array.isArray(obs.points) || obs.points.length < 2 || !this.videoFile) return;
    const video = sec.querySelector(".tw-obs-video");
    const canvas = sec.querySelector(".tw-obs-canvas");
    const inspect = sec.querySelector("[data-inspect]");
    const summary = results.diagnostics?.observation || {};
    const fps = Number(results.video_info?.fps || obs.fps || 30) || 30;
    const impactFrame = summary.end_reason === "Impact confirmed" ? summary.end_frame : null;

    const renderer = new ObservedTrajectoryRenderer(canvas);
    this.obsRenderer = renderer;
    renderer.onPick = (p) => {
      video.currentTime = p.frame_id / fps;
      inspect.textContent = `Frame ${p.frame_id} · conf ${Number(p.confidence).toFixed(2)} · x=${Math.round(p.x_px)} y=${Math.round(p.y_px)} · ${p.real ? "observed" : "gap-fill"}`;
    };

    video.src = URL.createObjectURL(this.videoFile);
    video.addEventListener("loadedmetadata", () => {
      renderer.setObservation({
        points: obs.points, display_end_frame: obs.display_end_frame,
        videoW: video.videoWidth, videoH: video.videoHeight, impactFrame,
      });
      renderer.resize(video.clientWidth, video.clientHeight);
      sec.hidden = false;
    }, { once: true });

    // Passive sync loop: mirror the video's currentTime → frame → draw. No timing of its own.
    const loop = () => {
      if (this.obsRenderer !== renderer) return;
      if (video.clientWidth && (video.clientWidth !== renderer.cssW || video.clientHeight !== renderer.cssH)) {
        renderer.resize(video.clientWidth, video.clientHeight);
      }
      renderer.draw(Math.round(video.currentTime * fps));
      this._obsRaf = requestAnimationFrame(loop);
    };
    this._obsRaf = requestAnimationFrame(loop);

    sec.querySelectorAll("[data-layer]").forEach((cb) =>
      cb.addEventListener("change", () => renderer.setLayers({ [cb.dataset.layer]: cb.checked })));
    const inspectToggle = sec.querySelector("[data-inspect-toggle]");
    inspectToggle?.addEventListener("change", () => {
      canvas.classList.toggle("inspect", inspectToggle.checked);
      inspect.textContent = inspectToggle.checked ? "Click a point to seek the video there." : "Enable inspect, then click a point.";
    });
  }

  // Self-declaring analysis diagnostics — most importantly, whether the broadcast
  // animation is rendering the REAL analysed trajectory or the fallback template.
  // This turns "every clip looks the same" from something to infer into a stated fact.
  renderDiagnostics(results) {
    const host = this.root.querySelector("#tw-diag");
    if (!host) return;
    // The replay package is pipeline-rendered now: "real" = the reconstruction produced
    // both videos; the reason strings mirror what the pipeline actually emitted.
    const ex = results.exports || {};
    const src = ex.replay_players
      ? { real: true, lowConfidence: false, reason: "" }
      : { real: false, reason: results.trajectory?.valid ? "replay generation failed" : "trajectory invalid" };

    // Preferred: the per-stage pipeline status the backend now emits. A failure names
    // exactly which stage broke instead of collapsing to a single "fallback" flag.
    const diag = results.diagnostics;
    if (diag && Array.isArray(diag.stages)) {
      const rows = diag.stages.map((s) => {
        // The animation row reflects what actually rendered (real path vs template),
        // which the renderer decides — so read it from the live animationSource.
        const isAnim = s.key === "animation";
        const good = isAnim ? src.real : !!s.ok;
        const detail = isAnim
          ? (src.real ? (src.lowConfidence ? "real (low confidence)" : "real trajectory") : `fallback — ${src.reason}`)
          : (s.detail || "");
        return `<span>${s.label}</span><b class="tw-src ${good ? "ok" : "bad"}">${good ? "✓" : "✗"} ${detail}</b>`;
      }).join("");
      const conf = diag.overall_confidence != null ? `${Math.round(diag.overall_confidence * 100)}%` : "--";
      // Physical measurements are only trustworthy with calibration — surface their
      // status (Unavailable / Approximate / Heuristic) rather than a fabricated number.
      const m = diag.measurements || {};
      const mRows = m.speed ? `
          <span>Speed</span><b>${m.speed}</b>
          <span>Bounce</span><b>${m.bounce}</b>
          <span>Prediction</span><b>${m.prediction}</b>` : "";
      // Observation summary — the trajectory-debugger line: where the usable track
      // starts/ends, how clean it is, and WHY it stopped (impact vs. drifted to clip end).
      // Trajectory trustworthiness FIRST — the question an engineer asks before decision
      // details: "can I trust this path?" Then the per-stage pipeline status below.
      const o = diag.observation || {};
      const prod = diag.producer || {};
      const producerCell = prod.calibrated
        ? `<b class="tw-src ok">✓ Calibrated</b>`
        : `<b style="color:#f2b134">⚠ ${prod.label || "Heuristic (no profile)"}</b>`;
      const stars = o.quality_stars != null ? "★".repeat(o.quality_stars) + "☆".repeat(5 - o.quality_stars) : "";
      const topBlock = o.end_reason ? `
        <div class="tw-diag-head">
          <strong>Trajectory <span class="tw-stars">${stars}</span></strong>
          <span class="tw-endreason">${o.end_reason}</span>
        </div>
        <div class="tw-diag-grid">
          <span>Trajectory producer</span>${producerCell}
          <span>Observed frames</span><b>${o.start_frame}–${o.end_frame} (${o.length_frames}f)</b>
          <span>Displayed / dropped</span><b>${o.displayed_points} / ${o.dropped_points}</b>
          <span>Tracked (full clip)</span><b>${o.tracked_points}</b>
          <span>Mean confidence</span><b>${o.mean_confidence}</b>
          <span>Longest gap</span><b>${o.longest_gap_frames}f</b>
        </div>` : "";
      host.innerHTML = `
        ${topBlock}
        <strong>Pipeline Status</strong>
        <div class="tw-diag-grid">
          ${rows}${mRows}
          <span>Overall confidence</span><b>${conf}</b>
        </div>
        <small>${src.real
          ? "Rendering the analysed ball trajectory — validity and confidence are reported separately, so a low-confidence path is still shown rather than hidden."
          : `Rendering the placeholder path — ${src.reason}.`}</small>`;
      return;
    }

    // Legacy fallback for older jobs that predate the canonical ReviewResult.
    const traj = results.trajectory || {};
    const pts = Array.isArray(traj.points) ? traj.points.length
      : Array.isArray(traj) ? traj.length
      : (traj.point_count ?? "--");
    const bounce = traj.bounce_point ? "YES" : "NO";
    const impact = (results.lbw_gates?.impact || traj.impact_point) ? "YES" : "NO";
    const speed = results.summary?.ball_speed_kmh != null ? `${Number(results.summary.ball_speed_kmh).toFixed(1)} km/h` : "--";
    const conf = results.decision?.confidence != null ? `${Math.round(results.decision.confidence * 100)}%` : "--";
    host.innerHTML = `
      <strong>Analysis Diagnostics</strong>
      <div class="tw-diag-grid">
        <span>Trajectory points</span><b>${pts}</b>
        <span>Bounce detected</span><b>${bounce}</b>
        <span>Impact detected</span><b>${impact}</b>
        <span>Ball speed</span><b>${speed}</b>
        <span>Prediction confidence</span><b>${conf}</b>
        <span>Animation source</span><b class="tw-src ${src.real ? "ok" : "bad"}">${src.real ? "✓ Real trajectory" : "✗ Fallback template"}</b>
      </div>
      <small>${src.real ? "Rendering from the analysed ball trajectory." : `Rendering the placeholder path — ${src.reason}. Every clip renders the same curve until a real fitted trajectory is supplied.`}</small>`;
  }

  async promote() {
    if (!this.modelPath) return;
    const name = this.modelPath.split(/[\\/]/).pop();
    if (!window.confirm(`Promote ${name} to production?\n\nThe current production model will be archived so you can roll back.`)) return;
    const btn = this.root.querySelector("#tw-promote");
    if (btn) { btn.disabled = true; btn.textContent = "Promoting…"; }
    // Registry models promote by id; a browsed/external .pt promotes by path — both
    // land in the same registry promotion path on the backend.
    const rec = (this.registryModels || []).find((m) => m.path === this.modelPath);
    const request = rec
      ? { url: "/api/models/promote", body: { id: rec.id, by: "operator" } }
      : { url: "/api/testing/promote", body: { model_path: this.modelPath, by: "operator" } };
    try {
      const res = await fetch(`${API_BASE}${request.url}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request.body),
      });
      const data = await res.json();
      if (res.ok && data.ok) {
        if (btn) btn.textContent = "✓ Promoted to production";
      } else if (btn) { btn.disabled = false; btn.textContent = `Promote failed: ${data.detail || "error"}`; }
    } catch {
      if (btn) { btn.disabled = false; btn.textContent = "Promote failed — backend offline?"; }
    }
  }
}

function pct(value) { return value == null ? "--" : `${Math.round(Number(value) * 100)}%`; }
function point(p) { return p ? `${Number(p.x || 0).toFixed(1)}, ${Number(p.y || 0).toFixed(1)}, ${Number(p.z || 0).toFixed(1)}` : "--"; }
function formatDuration(seconds) {
  if (!seconds || Number.isNaN(seconds)) return "--";
  const mins = Math.floor(seconds / 60);
  const secs = Math.round(seconds % 60).toString().padStart(2, "0");
  return `${mins}:${secs}`;
}
