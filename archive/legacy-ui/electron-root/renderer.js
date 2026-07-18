const API_BASE = "http://127.0.0.1:8765";
const WS_BASE = "ws://127.0.0.1:8765";

const el = {
  status: document.getElementById("status"),
  backendStatus: document.getElementById("backend-status"),
  bigCamera: document.getElementById("big-camera"),
  smallCamera: document.getElementById("small-camera"),
  bigFeed: document.getElementById("big-feed"),
  smallFeed: document.getElementById("small-feed"),
  frameIndex: document.getElementById("frame-index"),
  bigTitle: document.getElementById("big-camera-title"),
  smallTitle: document.getElementById("small-camera-title"),
  reviewType: document.getElementById("review-type"),
  reviewClock: document.getElementById("review-clock"),
  syncChip: document.getElementById("sync-chip"),
  fpsMetric: document.getElementById("fps-metric"),
  latencyMetric: document.getElementById("latency-metric"),
  inferenceMetric: document.getElementById("inference-metric"),
  networkMetric: document.getElementById("network-metric"),
  cameraCount: document.getElementById("camera-count"),
  ballSpeedReadout: document.getElementById("ball-speed-readout"),
  spinReadout: document.getElementById("spin-readout"),
  swingReadout: document.getElementById("swing-readout"),
  cameraStatusGrid: document.getElementById("camera-status-grid"),
  liveLogs: document.getElementById("live-logs"),
  predictionFeed: document.getElementById("prediction-feed"),
  confidenceChip: document.getElementById("confidence-chip"),
  reviewCount: document.getElementById("review-count"),
  decisionAccuracy: document.getElementById("decision-accuracy"),
  crrLine: document.getElementById("crr-line"),
  crrLineCopy: document.getElementById("crr-line-copy"),
  inningsLine: document.getElementById("innings-line"),
  sessionDuration: document.getElementById("session-duration"),
  reviewHistory: document.getElementById("review-history"),
  tournamentName: document.getElementById("tournament-name"),
  venueName: document.getElementById("venue-name"),
  tournamentLogo: document.getElementById("tournament-logo"),
  matchName: document.getElementById("match-name"),
  teamALabel: document.getElementById("team-a-label"),
  teamBLabel: document.getElementById("team-b-label"),
  scoreLine: document.getElementById("score-line"),
  overBall: document.getElementById("over-ball"),
  targetLine: document.getElementById("target-line"),
  rrrLine: document.getElementById("rrr-line"),
  matchStatus: document.getElementById("match-status"),
  settingsOpen: document.getElementById("settings-open"),
  settingsClose: document.getElementById("settings-close"),
  settingsModal: document.getElementById("settings-modal"),
  settingsForm: document.getElementById("settings-form"),
  resetSettings: document.getElementById("reset-settings"),
  addCamera: document.getElementById("add-camera"),
  cameraSettingsList: document.getElementById("camera-settings-list"),
  operatorOpen: document.getElementById("operator-open"),
  operatorClose: document.getElementById("operator-close"),
  operatorDrawer: document.getElementById("operator-drawer"),
  preflightOpen: document.getElementById("preflight-open"),
  preflightClose: document.getElementById("preflight-close"),
  preflightModal: document.getElementById("preflight-modal"),
  preflightList: document.getElementById("preflight-list"),
  preflightCameraSelect: document.getElementById("preflight-camera-select"),
  preflightBanner: document.getElementById("preflight-banner"),
  preflightRun: document.getElementById("preflight-run"),
  pfRequireAudio: document.getElementById("pf-require-audio"),
  pfRequireGpu: document.getElementById("pf-require-gpu"),
  pfAutorefresh: document.getElementById("pf-autorefresh"),
};

const canvases = {
  trajectory: document.getElementById("trajectory-canvas"),
  pitchMap: document.getElementById("pitch-map"),
  pitch3d: document.getElementById("pitch-3d"),
  speed: document.getElementById("speed-chart"),
  swing: document.getElementById("swing-chart"),
  bounce: document.getElementById("bounce-chart"),
  accuracy: document.getElementById("accuracy-chart"),
  timeline: document.getElementById("timeline-chart"),
};

let replayMode = false;
let replayMeta = null;
let refreshTimer = null;
let socket = null;
let requestInFlight = false;
let latestHealth = null;
let animationTick = 0;
let config = null;
let detectedCameras = [];
let preflightTimer = null;
const preflightAck = new Set();

const defaultConfig = {
  tournament: "State Cricket Championship 2026",
  tournamentLogo: "",
  venue: "ACA International Stadium",
  matchDate: "",
  umpire: "On-field Umpire",
  thirdUmpire: "Third Umpire",
  teamA: "Andhra XI",
  teamB: "Karnataka XI",
  teamALogo: "",
  teamBLogo: "",
  matchType: "T20",
  status: "LIVE REVIEW",
  innings: 2,
  over: "17.4",
  ball: 4,
  runs: 156,
  wickets: 4,
  target: 178,
  requiredRunRate: "8.25",
  currentRunRate: "9.00",
  reviewType: "LBW",
  decision: "Not Out",
  confidence: 87,
  cameras: [],
  selectedCameras: null,
  requireAudio: false,
  requireGpu: false,
  reviewHistory: [
    { type: "LBW", decision: "Not Out", confidence: 87 },
    { type: "Edge", decision: "Clear", confidence: 92 },
    { type: "No Ball", decision: "Legal", confidence: 89 },
  ],
  sessionStartedAt: Date.now(),
};

function loadConfig() {
  const saved = localStorage.getItem("drsCommandCenterConfig");
  config = saved ? { ...defaultConfig, ...JSON.parse(saved) } : { ...defaultConfig };
  applyConfig();
  renderCameraSettings();
  populateSettingsForm();
}

function saveConfig() {
  localStorage.setItem("drsCommandCenterConfig", JSON.stringify(config));
  applyConfig();
}

function applyConfig() {
  el.tournamentName.textContent = config.tournament;
  el.venueName.textContent = config.venue;
  if (el.matchName) el.matchName.textContent = `${config.teamA} vs ${config.teamB}`;
  el.teamALabel.textContent = config.teamA;
  el.teamBLabel.textContent = config.teamB;
  const shortName = config.teamA.split(/\s+/)[0].slice(0, 3).toUpperCase();
  el.scoreLine.textContent = `${shortName} ${config.runs}/${config.wickets}`;
  el.overBall.textContent = config.over;
  el.targetLine.textContent = config.target;
  el.rrrLine.textContent = config.requiredRunRate;
  el.matchStatus.textContent = config.status;
  el.reviewType.textContent = `${config.reviewType.toUpperCase()} REVIEW`;
  document.getElementById("decision-result").textContent = config.decision.toUpperCase();
  el.confidenceChip.textContent = `${config.confidence}%`;
  document.querySelector(".decision-meter span").style.width = `${config.confidence}%`;
  document.getElementById("umpire-call").textContent = config.decision;
  document.getElementById("pitching-location").textContent = config.reviewType === "LBW" ? "In line" : "Not required";
  document.getElementById("impact-location").textContent = config.reviewType === "LBW" ? "Umpire call" : "Frame aligned";
  document.getElementById("wicket-probability").textContent = config.reviewType === "LBW" ? `${Math.max(0, config.confidence - 18)}%` : "Not active";
  document.getElementById("edge-score").textContent = config.reviewType === "Edge" ? `${config.confidence}%` : "0%";
  el.crrLine.textContent = config.currentRunRate;
  el.crrLineCopy.textContent = config.currentRunRate;
  el.inningsLine.textContent = String(config.innings);
  el.reviewCount.textContent = String(config.reviewHistory.length);
  el.decisionAccuracy.textContent = config.reviewHistory.length
    ? `${Math.round(config.reviewHistory.reduce((sum, item) => sum + Number(item.confidence || 0), 0) / config.reviewHistory.length)}%`
    : "--";
  renderReviewHistory();
  if (config.tournamentLogo) {
    el.tournamentLogo.src = config.tournamentLogo;
    el.tournamentLogo.parentElement.classList.add("has-logo");
  } else {
    el.tournamentLogo.removeAttribute("src");
    el.tournamentLogo.parentElement.classList.remove("has-logo");
  }
}

function populateSettingsForm() {
  const form = el.settingsForm;
  Object.entries(config).forEach(([key, value]) => {
    const input = form.elements[key];
    if (!input || input.type === "file") return;
    input.value = value;
  });
}

function updateConfigFromForm() {
  const form = el.settingsForm;
  const fields = [
    "tournament", "venue", "matchDate", "umpire", "thirdUmpire", "teamA", "teamB", "matchType",
    "status", "innings", "over", "ball", "runs", "wickets", "target", "requiredRunRate",
    "currentRunRate", "reviewType", "decision", "confidence",
  ];
  fields.forEach((field) => {
    const input = form.elements[field];
    if (!input) return;
    const numeric = ["innings", "ball", "runs", "wickets", "target", "confidence"].includes(field);
    config[field] = numeric ? Number(input.value || 0) : input.value;
  });
  saveConfig();
}

function pushReview(type, decision, confidence) {
  config.reviewHistory.unshift({ type, decision, confidence });
  config.reviewHistory = config.reviewHistory.slice(0, 12);
  saveConfig();
}

function readFileAsDataUrl(file) {
  return new Promise((resolve) => {
    if (!file) return resolve("");
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.readAsDataURL(file);
  });
}

function cameraIdFromValue(value) {
  return Number(value.replace("CAM ", ""));
}

function cameraLabel(cameraId) {
  return `CAM ${cameraId}`;
}

function setBackendState(online, text) {
  el.backendStatus.textContent = text;
  el.backendStatus.classList.toggle("online", online);
  el.backendStatus.classList.toggle("offline", !online);
  el.networkMetric.textContent = online ? "OK" : "OFF";
}

async function apiJson(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, options);
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

function connectStatusSocket() {
  if (socket) socket.close();
  socket = new WebSocket(`${WS_BASE}/ws/status`);

  socket.addEventListener("open", () => {
    setBackendState(true, "Backend connected");
    log("WebSocket connected to Python replay server");
  });

  socket.addEventListener("message", (event) => {
    handleSocketEvent(JSON.parse(event.data));
  });

  socket.addEventListener("close", () => {
    setBackendState(false, "Backend offline");
    log("Backend socket closed, retrying");
    setTimeout(connectStatusSocket, 1500);
  });

  socket.addEventListener("error", () => {
    setBackendState(false, "Backend offline");
  });
}

function handleSocketEvent(payload) {
  const type = payload.type || "camera_health";
  if (type === "camera_health") {
    latestHealth = payload;
    updateHealth(payload);
  } else if (type === "sync_report") {
    const sync = payload.sync || {};
    const spread = Number(sync.spread_ms || 0);
    el.syncChip.textContent = `SYNC ${spread.toFixed(1)} ms`;
  } else if (type === "decision_ready") {
    if (payload.decision) {
      config.decision = payload.decision;
      config.confidence = Math.round(Number(payload.confidence || config.confidence));
      saveConfig();
      revealDecision(config.decision);
    }
  } else if (type === "appeal_started") {
    log(`Appeal started: ${payload.appeal_type || "review"}`);
  } else if (type === "ball_detected") {
    el.inferenceMetric.textContent = `${Number(payload.inference_ms || 0).toFixed(1)} ms`;
  } else if (type === "replay_ready") {
    log("Replay ready from backend");
  } else if (type === "no_ball_alert") {
    revealDecision("NO BALL");
  }
}

function revealDecision(decision) {
  const overlay = document.createElement("div");
  overlay.className = `decision-reveal ${String(decision).toLowerCase().replace(/[^a-z]+/g, "-")}`;
  overlay.textContent = decision.toUpperCase();
  document.body.appendChild(overlay);
  window.setTimeout(() => overlay.classList.add("show"), 20);
  window.setTimeout(() => overlay.remove(), 3200);
}

async function loadCameras() {
  try {
    const data = await apiJson("/api/cameras");
    const ids = data.camera_ids || [];
    detectedCameras = ids.slice();
    const options = ids.map((id) => `<option>${cameraLabel(id)}</option>`).join("");
    el.bigCamera.innerHTML = options;
    el.smallCamera.innerHTML = options;
    if (ids.length > 1) el.smallCamera.value = cameraLabel(ids[1]);
    renderCameraCards(ids, data.health || {});
    mergeBackendCameras(ids);
    el.status.value = `Loaded ${ids.length} backend cameras`;
    updateCameraLabels();
    return true;
  } catch {
    el.status.value = "Start Python backend: python drs_app.py --api --cameras 0,1,2,3,4,5 --record";
    return false;
  }
}

function updateCameraLabels() {
  const big = el.bigCamera.value || "CAM ?";
  const small = el.smallCamera.value || "CAM ?";
  const heroTitle = document.querySelector(".hero-video .panel-head strong");
  if (heroTitle) heroTitle.textContent = big;
  el.bigTitle.textContent = big;
  el.smallTitle.textContent = small;
}

function liveFrameUrl(cameraId) {
  return `${API_BASE}/api/live/${cameraId}.jpg?t=${Date.now()}`;
}

async function refreshReplayFeeds(bigId, smallId) {
  if (requestInFlight) return;
  requestInFlight = true;
  try {
    const payload = {
      camera_ids: [bigId, smallId],
      frame_index: Number(el.frameIndex.value || 0),
    };
    const data = await apiJson("/api/replay/request", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const big = data.frames[String(bigId)];
    const small = data.frames[String(smallId)];
    if (big) el.bigFeed.src = `${API_BASE}${big.image_url}&t=${Date.now()}`;
    if (small) el.smallFeed.src = `${API_BASE}${small.image_url}&t=${Date.now()}`;
    el.status.value = `Replay frame ${el.frameIndex.value} | ref ${Number(data.reference_timestamp_ms || 0).toFixed(1)} ms`;
  } catch (error) {
    el.status.value = `Replay request failed: ${error.message}`;
  } finally {
    requestInFlight = false;
  }
}

function refreshLiveFeeds(bigId, smallId) {
  el.bigFeed.src = liveFrameUrl(bigId);
  el.smallFeed.src = liveFrameUrl(smallId);
}

function refreshFeeds() {
  if (!el.bigCamera.value || !el.smallCamera.value) return;
  const bigId = cameraIdFromValue(el.bigCamera.value);
  const smallId = cameraIdFromValue(el.smallCamera.value);
  updateCameraLabels();
  if (replayMode) refreshReplayFeeds(bigId, smallId);
  else refreshLiveFeeds(bigId, smallId);
}

async function applyAppealMode(mode) {
  try {
    const preset = await apiJson(`/api/presets/${mode}`);
    if (preset.big_camera_id !== null) el.bigCamera.value = cameraLabel(preset.big_camera_id);
    if (preset.small_camera_ids && preset.small_camera_ids.length) {
      el.smallCamera.value = cameraLabel(preset.small_camera_ids[0]);
    }
    el.reviewType.textContent = `${preset.label.toUpperCase()} REVIEW`;
    config.reviewType = preset.label;
    config.decision = mode === "EDGE" ? "Clear" : "Not Out";
    saveConfig();
    updateCameraLabels();
    refreshFeeds();
    log(`${preset.label} review preset loaded: ${el.bigCamera.value} / ${el.smallCamera.value}`);
  } catch (error) {
    const fallback = mode.replace("_", " ").toLowerCase().replace(/\b\w/g, (letter) => letter.toUpperCase());
    config.reviewType = fallback;
    config.decision = "Umpire Call";
    saveConfig();
    refreshFeeds();
    log(`${fallback} local review mode loaded`);
  }
}

async function captureReplay() {
  try {
    replayMeta = await apiJson("/api/replay/create", { method: "POST" });
    replayMode = true;
    el.frameIndex.max = Math.max(0, (replayMeta.total_frames || 1) - 1);
    el.frameIndex.value = Math.max(0, (replayMeta.total_frames || 1) - 120);
    refreshFeeds();
    pushReview(config.reviewType, config.decision, config.confidence);
    log(`Replay captured with ${replayMeta.total_frames} frames`);
  } catch (error) {
    el.status.value = `Replay failed: ${error.message}`;
  }
}

function stepFrame(delta) {
  replayMode = true;
  const maxFrame = Number(el.frameIndex.max || 999999);
  el.frameIndex.value = Math.max(0, Math.min(maxFrame, Number(el.frameIndex.value || 0) + delta));
  refreshFeeds();
}

function updateHealth(data) {
  const sync = data.sync || {};
  const cameraIds = data.camera_ids || [];
  const health = data.health || {};
  const spread = Number(sync.spread_ms || 0);
  setBackendState(true, `${cameraIds.length} cams | sync ${spread.toFixed(1)} ms`);
  el.cameraCount.textContent = `${cameraIds.length} / ${cameraIds.length} online`;
  el.syncChip.textContent = `SYNC ${spread.toFixed(1)} ms`;
  el.latencyMetric.textContent = `${Math.max(8, spread + 18).toFixed(0)} ms`;
  el.inferenceMetric.textContent = `${(18 + Math.sin(Date.now() / 900) * 4).toFixed(1)} ms`;
  const fpsValues = Object.values(health).map((item) => Number(item.fps || 0)).filter(Boolean);
  const avgFps = fpsValues.length ? fpsValues.reduce((a, b) => a + b, 0) / fpsValues.length : 0;
  el.fpsMetric.textContent = avgFps ? avgFps.toFixed(1) : "--";
  renderCameraCards(cameraIds, health);
}

function renderCameraCards(ids, health) {
  el.cameraStatusGrid.innerHTML = ids.map((id) => {
    const item = health[id] || {};
    const fps = Number(item.fps || 0).toFixed(1);
    const source = item.synthetic ? "SYNTH" : "LIVE";
    return `<div class="camera-card online"><span>CAM ${id}</span><strong>${source}</strong><small>${fps} fps</small></div>`;
  }).join("");
}

function mergeBackendCameras(ids) {
  const existing = new Set(config.cameras.map((camera) => camera.ip));
  ids.forEach((id) => {
    const ip = `local:${id}`;
    if (!existing.has(ip)) {
      config.cameras.push({
        name: `CAM ${id}`,
        ip,
        status: "Enabled",
        syncDelay: "0 ms",
        resolution: "backend",
        fps: "auto",
      });
    }
  });
  saveConfig();
  renderCameraSettings();
}

function renderCameraSettings() {
  if (!config) return;
  if (!config.cameras.length) {
    config.cameras = [0, 1, 2, 3, 4, 5].map((id) => ({
      name: `CAM ${id}`,
      ip: `local:${id}`,
      status: "Enabled",
      syncDelay: "0 ms",
      resolution: "1280x720",
      fps: "60",
    }));
  }
  el.cameraSettingsList.innerHTML = config.cameras.map((camera, index) => `
    <div class="camera-row" data-index="${index}">
      <input data-camera-field="name" value="${camera.name}" placeholder="Camera Name" />
      <input data-camera-field="ip" value="${camera.ip}" placeholder="Camera IP" />
      <select data-camera-field="status">
        <option ${camera.status === "Enabled" ? "selected" : ""}>Enabled</option>
        <option ${camera.status === "Disabled" ? "selected" : ""}>Disabled</option>
        <option ${camera.status === "Offline" ? "selected" : ""}>Offline</option>
      </select>
      <input data-camera-field="syncDelay" value="${camera.syncDelay}" placeholder="Sync Delay" />
      <input data-camera-field="resolution" value="${camera.resolution}" placeholder="Resolution" />
      <input data-camera-field="fps" value="${camera.fps}" placeholder="FPS" />
      <button type="button" data-camera-toggle>${camera.status === "Disabled" ? "Enable" : "Disable"}</button>
      <button type="button" data-camera-remove>Remove</button>
    </div>
  `).join("");
}

function renderReviewHistory() {
  el.reviewHistory.innerHTML = `
    <table>
      <thead><tr><th>Type</th><th>Decision</th><th>Conf.</th></tr></thead>
      <tbody>
        ${config.reviewHistory.map((item) => (
          `<tr><td>${item.type}</td><td>${item.decision}</td><td>${item.confidence}%</td></tr>`
        )).join("")}
      </tbody>
    </table>
  `;
}

function updateCameraConfig(target) {
  const row = target.closest(".camera-row");
  if (!row) return;
  const index = Number(row.dataset.index);
  const field = target.dataset.cameraField;
  if (field) config.cameras[index][field] = target.value;
  if (target.matches("[data-camera-toggle]")) {
    config.cameras[index].status = config.cameras[index].status === "Disabled" ? "Enabled" : "Disabled";
  }
  if (target.matches("[data-camera-remove]")) {
    config.cameras.splice(index, 1);
  }
  saveConfig();
  if (!field) renderCameraSettings();
}

function log(message) {
  const stamp = new Date().toLocaleTimeString();
  el.liveLogs.textContent = `${stamp} | ${message}`;
}

function fitCanvas(canvas) {
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const width = Math.max(1, Math.floor(rect.width * dpr));
  const height = Math.max(1, Math.floor(rect.height * dpr));
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
  return { width, height, dpr };
}

function drawLineChart(canvas, values, color) {
  const { width, height } = fitCanvas(canvas);
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, width, height);
  ctx.strokeStyle = "rgba(142,163,191,.18)";
  ctx.lineWidth = 1;
  for (let i = 1; i < 4; i++) {
    const y = (height / 4) * i;
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(width, y);
    ctx.stroke();
  }
  ctx.strokeStyle = color;
  ctx.lineWidth = 3;
  ctx.beginPath();
  values.forEach((value, index) => {
    const x = (index / (values.length - 1)) * width;
    const y = height - value * height;
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
}

function drawPitchMap() {
  const canvas = canvases.pitchMap;
  const { width, height } = fitCanvas(canvas);
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#163f2e";
  ctx.fillRect(0, 0, width, height);
  ctx.fillStyle = "rgba(224,210,156,.34)";
  ctx.fillRect(width * 0.42, 0, width * 0.16, height);
  ctx.strokeStyle = "rgba(237,246,255,.35)";
  ctx.lineWidth = 2;
  ctx.strokeRect(width * 0.12, height * 0.12, width * 0.76, height * 0.76);
  ctx.strokeStyle = "#42ff9a";
  ctx.lineWidth = 3;
  ctx.beginPath();
  ctx.moveTo(width * 0.14, height * 0.76);
  ctx.bezierCurveTo(width * 0.38, height * 0.28, width * 0.62, height * 0.18, width * 0.86, height * 0.36);
  ctx.stroke();
  ctx.fillStyle = "#ffd45c";
  ctx.beginPath();
  ctx.arc(width * 0.61, height * 0.44, 7, 0, Math.PI * 2);
  ctx.fill();
}

function draw3D() {
  const canvas = canvases.pitch3d;
  const { width, height } = fitCanvas(canvas);
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, width, height);
  const cx = width / 2;
  const cy = height * 0.58;
  ctx.strokeStyle = "rgba(56,168,255,.38)";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(cx - width * 0.36, cy + height * 0.24);
  ctx.lineTo(cx - width * 0.18, cy - height * 0.28);
  ctx.lineTo(cx + width * 0.32, cy - height * 0.18);
  ctx.lineTo(cx + width * 0.42, cy + height * 0.2);
  ctx.closePath();
  ctx.stroke();
  ctx.strokeStyle = "#42ff9a";
  ctx.lineWidth = 4;
  ctx.beginPath();
  ctx.moveTo(cx - width * 0.26, cy + height * 0.14);
  ctx.bezierCurveTo(cx - width * 0.1, cy - height * 0.34, cx + width * 0.14, cy - height * 0.42, cx + width * 0.32, cy - height * 0.02);
  ctx.stroke();
  ctx.fillStyle = "#ffd45c";
  ctx.beginPath();
  ctx.arc(cx + width * 0.17, cy - height * 0.22 + Math.sin(animationTick / 18) * 8, 7, 0, Math.PI * 2);
  ctx.fill();
}

function drawTrajectoryOverlay() {
  const canvas = canvases.trajectory;
  const { width, height } = fitCanvas(canvas);
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, width, height);
  ctx.strokeStyle = "rgba(66,255,154,.82)";
  ctx.lineWidth = 4;
  ctx.beginPath();
  ctx.moveTo(width * 0.08, height * 0.72);
  ctx.bezierCurveTo(width * 0.34, height * 0.18, width * 0.58, height * 0.2, width * 0.86, height * 0.45);
  ctx.stroke();
}

function drawTimeline() {
  const canvas = canvases.timeline;
  const { width, height } = fitCanvas(canvas);
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, width, height);
  const colors = ["#38a8ff", "#42ff9a", "#ffd45c", "#ff5f6d"];
  for (let i = 0; i < 16; i++) {
    ctx.fillStyle = colors[i % colors.length];
    ctx.fillRect((width / 16) * i + 3, height * 0.25, width / 20, height * 0.5);
  }
}

function animate() {
  animationTick += 1;
  const values = Array.from({ length: 28 }, (_, i) => 0.25 + 0.48 * Math.abs(Math.sin((animationTick + i * 9) / 34)));
  drawLineChart(canvases.speed, values, "#42ff9a");
  drawLineChart(canvases.swing, values.map((v, i) => 0.18 + ((v + i * 0.03) % 0.68)), "#38a8ff");
  drawLineChart(canvases.bounce, values.map((v) => 1 - v * 0.75), "#ffd45c");
  drawLineChart(canvases.accuracy, values.map((v) => 0.55 + v * 0.35), "#ff5f6d");
  drawPitchMap();
  draw3D();
  drawTrajectoryOverlay();
  drawTimeline();
  el.reviewClock.textContent = new Date().toLocaleTimeString();
  el.ballSpeedReadout.textContent = `${(126 + Math.sin(animationTick / 24) * 8).toFixed(1)} km/h`;
  el.spinReadout.textContent = `${Math.round(2050 + Math.cos(animationTick / 31) * 160)} rpm`;
  el.swingReadout.textContent = `${(2.1 + Math.sin(animationTick / 37) * 0.7).toFixed(1)} deg`;
  const elapsed = Math.max(0, Date.now() - Number(config.sessionStartedAt || Date.now()));
  const minutes = Math.floor(elapsed / 60000);
  const seconds = Math.floor((elapsed % 60000) / 1000);
  el.sessionDuration.textContent = `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  el.confidenceChip.textContent = `${config.confidence}%`;
  el.predictionFeed.textContent = replayMode ? "Replay frame synchronized across selected cameras" : "Live prediction model monitoring delivery";
  requestAnimationFrame(animate);
}

const PF_GROUPS = [
  ["cameras", "Cameras"],
  ["capture", "Capture & Replay"],
  ["vision", "Vision & Models"],
  ["system", "System"],
];

function cameraLetter(index) {
  return index < 26 ? String.fromCharCode(65 + index) : String(index);
}

function selectedCameraIds() {
  const saved = Array.isArray(config.selectedCameras)
    ? config.selectedCameras.filter((id) => detectedCameras.includes(id))
    : null;
  return saved && saved.length ? saved : detectedCameras.slice();
}

function renderPreflightCameras() {
  if (!detectedCameras.length) {
    el.preflightCameraSelect.innerHTML = `<span class="pf-empty">No cameras detected yet — start the backend, then Run Checks.</span>`;
    return;
  }
  const active = new Set(selectedCameraIds());
  el.preflightCameraSelect.innerHTML = detectedCameras.map((id, index) => `
    <label class="pf-cam ${active.has(id) ? "on" : ""}">
      <input type="checkbox" data-preflight-camera="${id}" ${active.has(id) ? "checked" : ""} />
      <strong>CAM ${cameraLetter(index)}</strong><small>index ${id}</small>
    </label>
  `).join("");
}

function setSelectedCameras(ids) {
  config.selectedCameras = ids.length === detectedCameras.length ? null : ids;
  saveConfig();
  renderPreflightCameras();
  runPreflight();
}

function setPreflightBanner(state, text) {
  el.preflightBanner.className = `preflight-banner ${state}`;
  el.preflightBanner.textContent = text;
}

function preflightRow(item) {
  const acked = preflightAck.has(item.key);
  const checkable = item.status === "warn" || item.status === "skip";
  const checked = item.status === "pass" || acked;
  const optional = item.required ? "" : ` <em>(optional)</em>`;
  return `
    <label class="pf-row ${item.status} ${acked ? "acked" : ""}">
      <input type="checkbox" data-preflight-ack="${item.key}" ${checked ? "checked" : ""} ${checkable ? "" : "disabled"} />
      <span class="pf-pill ${item.status}">${item.status.toUpperCase()}</span>
      <span class="pf-row-text"><strong>${item.label}${optional}</strong><small>${item.detail}</small></span>
    </label>
  `;
}

function renderPreflight(data) {
  const rows = data.items.filter((item) => item.key !== "match_ready");
  el.preflightList.innerHTML = PF_GROUPS.map(([key, title]) => {
    const groupItems = rows.filter((item) => item.group === key);
    if (!groupItems.length) return "";
    return `<div class="pf-group"><div class="pf-group-title">${title}</div>${groupItems.map(preflightRow).join("")}</div>`;
  }).join("");

  const unmetWarnings = rows.filter((item) => item.status === "warn" && !preflightAck.has(item.key));
  if (data.blocking.length) {
    const n = data.blocking.length;
    setPreflightBanner("fail", `NOT READY — ${n} blocking check${n > 1 ? "s" : ""}`);
  } else if (unmetWarnings.length) {
    const n = unmetWarnings.length;
    setPreflightBanner("warn", `Ready with warnings — acknowledge ${n} item${n > 1 ? "s" : ""}`);
  } else {
    setPreflightBanner("pass", "MATCH READY — cleared for live operation");
  }
}

async function runPreflight() {
  if (!el.preflightModal.classList.contains("open")) return;
  const ids = selectedCameraIds();
  const params = new URLSearchParams();
  if (ids.length) params.set("cameras", ids.join(","));
  params.set("require_audio", String(!!config.requireAudio));
  params.set("require_gpu", String(!!config.requireGpu));
  try {
    renderPreflight(await apiJson(`/api/preflight?${params.toString()}`));
  } catch {
    const hint = detectedCameras.join(",") || "0,1,2";
    el.preflightList.innerHTML = `<div class="pf-offline">Backend offline — start it with<br><code>python drs_app.py --api --cameras ${hint} --record</code></div>`;
    setPreflightBanner("offline", "Backend offline — cannot verify readiness");
  }
}

function startPreflightAutorefresh() {
  stopPreflightAutorefresh();
  if (el.pfAutorefresh.checked) preflightTimer = setInterval(runPreflight, 2000);
}

function stopPreflightAutorefresh() {
  if (preflightTimer) {
    clearInterval(preflightTimer);
    preflightTimer = null;
  }
}

function openPreflight() {
  el.pfRequireAudio.checked = !!config.requireAudio;
  el.pfRequireGpu.checked = !!config.requireGpu;
  renderPreflightCameras();
  el.preflightModal.classList.add("open");
  el.preflightModal.setAttribute("aria-hidden", "false");
  runPreflight();
  startPreflightAutorefresh();
}

function closePreflight() {
  el.preflightModal.classList.remove("open");
  el.preflightModal.setAttribute("aria-hidden", "true");
  stopPreflightAutorefresh();
}

el.bigCamera.addEventListener("change", refreshFeeds);
el.smallCamera.addEventListener("change", refreshFeeds);
el.frameIndex.addEventListener("change", () => {
  replayMode = true;
  refreshFeeds();
});

document.querySelectorAll("button[data-mode]").forEach((button) => {
  button.addEventListener("click", () => applyAppealMode(button.dataset.mode));
});

document.querySelectorAll("button[data-command]").forEach((button) => {
  button.addEventListener("click", async () => {
    const command = button.dataset.command;
    if (command === "capture-replay") await captureReplay();
    else if (command === "prev") stepFrame(-1);
    else if (command === "next") stepFrame(1);
    else if (command === "play") {
      replayMode = false;
      refreshFeeds();
      log("Returned to live mode");
    } else if (command === "start-match") {
      config.status = "LIVE";
      config.sessionStartedAt = Date.now();
      saveConfig();
      log("Match started");
    } else if (command === "pause-match") {
      config.status = "PAUSED";
      saveConfig();
      log("Match paused");
    } else if (command === "end-match") {
      config.status = "MATCH ENDED";
      saveConfig();
      log("Match ended");
    } else if (command === "save-session") {
      saveConfig();
      log("Session saved to localStorage");
    } else if (command === "export-review") {
      pushReview(config.reviewType, config.decision, config.confidence);
      log("Review evidence marked for export");
    } else if (command === "generate-report") {
      log("Report generated from current session state");
    } else if (command === "diagnostics") {
      log(latestHealth ? "Diagnostics available from backend health stream" : "Diagnostics waiting for backend");
    } else if (command === "preflight") {
      el.operatorDrawer.classList.remove("open");
      el.operatorDrawer.setAttribute("aria-hidden", "true");
      openPreflight();
    } else {
      const result = await window.drs.command(command);
      log(`Operator command: ${result.command}`);
    }
  });
});

el.settingsOpen.addEventListener("click", () => {
  populateSettingsForm();
  el.settingsModal.classList.add("open");
  el.settingsModal.setAttribute("aria-hidden", "false");
});

el.settingsClose.addEventListener("click", () => {
  el.settingsModal.classList.remove("open");
  el.settingsModal.setAttribute("aria-hidden", "true");
});

el.settingsModal.addEventListener("click", (event) => {
  if (event.target === el.settingsModal) {
    el.settingsModal.classList.remove("open");
    el.settingsModal.setAttribute("aria-hidden", "true");
  }
});

el.settingsForm.addEventListener("input", (event) => {
  if (event.target.matches("[data-camera-field]")) {
    updateCameraConfig(event.target);
    return;
  }
  if (event.target.type !== "file") updateConfigFromForm();
});

el.settingsForm.addEventListener("change", async (event) => {
  const input = event.target;
  if (input.name === "tournamentLogo") {
    config.tournamentLogo = await readFileAsDataUrl(input.files[0]);
    saveConfig();
  }
  if (input.name === "teamALogo") {
    config.teamALogo = await readFileAsDataUrl(input.files[0]);
    saveConfig();
  }
  if (input.name === "teamBLogo") {
    config.teamBLogo = await readFileAsDataUrl(input.files[0]);
    saveConfig();
  }
});

el.settingsForm.addEventListener("click", (event) => {
  if (event.target.matches("[data-camera-toggle], [data-camera-remove]")) {
    updateCameraConfig(event.target);
  }
});

el.settingsForm.addEventListener("submit", (event) => {
  event.preventDefault();
  updateConfigFromForm();
  el.settingsModal.classList.remove("open");
  log("Settings saved");
});

el.resetSettings.addEventListener("click", () => {
  config = { ...defaultConfig, cameras: [] };
  saveConfig();
  renderCameraSettings();
  populateSettingsForm();
  log("Settings reset");
});

el.addCamera.addEventListener("click", () => {
  const index = config.cameras.length;
  config.cameras.push({
    name: `CAM ${index}`,
    ip: `local:${index}`,
    status: "Enabled",
    syncDelay: "0 ms",
    resolution: "1280x720",
    fps: "60",
  });
  saveConfig();
  renderCameraSettings();
});

el.operatorOpen.addEventListener("click", () => {
  el.operatorDrawer.classList.add("open");
  el.operatorDrawer.setAttribute("aria-hidden", "false");
});

el.operatorClose.addEventListener("click", () => {
  el.operatorDrawer.classList.remove("open");
  el.operatorDrawer.setAttribute("aria-hidden", "true");
});

el.preflightOpen.addEventListener("click", openPreflight);
el.preflightClose.addEventListener("click", closePreflight);
el.preflightModal.addEventListener("click", (event) => {
  if (event.target === el.preflightModal) closePreflight();
});
el.preflightRun.addEventListener("click", runPreflight);
el.pfAutorefresh.addEventListener("change", startPreflightAutorefresh);
el.pfRequireAudio.addEventListener("change", () => {
  config.requireAudio = el.pfRequireAudio.checked;
  saveConfig();
  runPreflight();
});
el.pfRequireGpu.addEventListener("change", () => {
  config.requireGpu = el.pfRequireGpu.checked;
  saveConfig();
  runPreflight();
});
el.preflightCameraSelect.addEventListener("change", (event) => {
  const attr = event.target.getAttribute("data-preflight-camera");
  if (attr === null) return;
  const id = Number(attr);
  const current = new Set(selectedCameraIds());
  if (event.target.checked) current.add(id);
  else current.delete(id);
  setSelectedCameras(detectedCameras.filter((camId) => current.has(camId)));
});
el.preflightList.addEventListener("change", (event) => {
  const key = event.target.getAttribute("data-preflight-ack");
  if (!key) return;
  if (event.target.checked) preflightAck.add(key);
  else preflightAck.delete(key);
  runPreflight();
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    el.settingsModal.classList.remove("open");
    el.operatorDrawer.classList.remove("open");
    closePreflight();
  }
  if (event.key === ",") stepFrame(-1);
  if (event.key === ".") stepFrame(1);
  if (event.key.toLowerCase() === "r") captureReplay();
  if (event.key.toLowerCase() === "s" && event.ctrlKey) {
    event.preventDefault();
    saveConfig();
    log("Configuration saved by shortcut");
  }
});

loadConfig();
connectStatusSocket();
loadCameras().then((ok) => {
  if (ok) {
    refreshFeeds();
    refreshTimer = setInterval(refreshFeeds, 150);
  }
});
animate();

window.addEventListener("beforeunload", () => {
  if (refreshTimer) clearInterval(refreshTimer);
  if (socket) socket.close();
});
