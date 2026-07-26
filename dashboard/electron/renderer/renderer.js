import { bailsLabel, observationLabel } from "./overlay/observation.js";
import { drawFrameOverlay } from "./overlay/frame-overlay.js";
import { CalibrationTabs } from "./components/CalibrationTabs.js";
import { StatusPanel } from "./components/StatusPanel.js";
import { TestingPanel } from "./components/TestingPanel.js";
import { ValidationPanel } from "./components/ValidationPanel.js";
import { ModelManagerPanel } from "./components/ModelManagerPanel.js";

const API_BASE = "http://localhost:8765";
const WS_BASE = "ws://localhost:8765";
const MAX_CAMERAS = 6;

/* ===================== config-driven review engine (registry) ===================== */
const REVIEW_MODULES = {
  lbw: {
    label: "LBW", role: "Ball Tracking", panel: "lbw",
    feedTitle: "Ball Tracking", detailTitle: "LBW Review", detailSub: "Ball-tracking trajectory",
    stages: ["Release", "Pitch", "Impact", "Prediction", "Decision"],
    protocol: [{ key: "front_foot", label: "Front Foot" }, { key: "ultra_edge", label: "UltraEdge" },
               { key: "trajectory", label: "Ball Tracking" }, { key: "decision", label: "Decision" }],
  },
  wide: {
    label: "Wide", role: "Wide Camera", panel: "wide",
    feedTitle: "Wide Camera", detailTitle: "Wide Review", detailSub: "Off / leg-side wide line",
    stages: ["Release", "Passing Batter", "Wide Line", "Decision"],
    protocol: [{ key: "wide_line", label: "Wide Line" }, { key: "decision", label: "Decision" }],
  },
  noball: {
    label: "No Ball", role: "Front Foot", panel: "noball",
    feedTitle: "Front Foot Camera", detailTitle: "Front Foot No Ball", detailSub: "Popping-crease overstep",
    stages: ["Release", "Landing", "Front Foot", "Decision"],
    protocol: [{ key: "front_foot", label: "Front Foot" }, { key: "decision", label: "Decision" }],
  },
  edge: {
    label: "Edge", role: "Stump Camera", panel: "edge",
    feedTitle: "Stump Camera", detailTitle: "Edge Review", detailSub: "UltraEdge + HotSpot",
    stages: ["Release", "Spike", "HotSpot", "Decision"],
    protocol: [{ key: "audio_sync", label: "Audio Sync" }, { key: "decision", label: "Decision" }],
    decisionMode: "assisted",
  },
  runout: {
    label: "Run Out", role: "Stump", panel: "runout",
    feedTitle: "Run-Out Camera", detailTitle: "Run Out", detailSub: "Crease / bat / bails",
    stages: ["Appeal", "Crease", "Bat", "Bails", "Decision"],
    protocol: [{ key: "crease", label: "Crease Check" }, { key: "decision", label: "Decision" }],
    decisionMode: "assisted",
  },
  stumping: {
    label: "Stumping", role: "Stump", panel: "stumping",
    feedTitle: "Stump Camera", detailTitle: "Stumping", detailSub: "Gloves / bails / bat position",
    stages: ["Appeal", "Gloves", "Bails", "Bat", "Decision"],
    protocol: [{ key: "crease", label: "Crease Check" }, { key: "timing", label: "Bail Timing" },
               { key: "decision", label: "Decision" }],
    decisionMode: "assisted",
  },
};

// assisted = the system provides evidence tools and ADVISORY readings only;
// the umpire makes the call (Edge / Run Out / Stumping — like broadcast DRS).
// automatic = the system produces a measurement-backed reading (LBW/Wide/NoBall).
const isAssisted = (type) => (REVIEW_MODULES[type]?.decisionMode || "automatic") === "assisted";

// The decision is confirmed in the review type's OWN vocabulary — a Wide review
// resolves WIDE / NOT WIDE, never OUT. `send` is what /api/decision/confirm
// records; the backend maps "appeal upheld" words onto its binary status.
const DECISION_ACTIONS = {
  wide: { positive: { send: "WIDE", label: "WIDE" }, negative: { send: "NOT WIDE", label: "NOT WIDE" } },
  noball: { positive: { send: "NO BALL", label: "NO BALL" }, negative: { send: "LEGAL", label: "LEGAL" } },
  default: { positive: { send: "OUT", label: "OUT" }, negative: { send: "NOT_OUT", label: "NOT OUT" } },
};
const FUTURE_MODULES = ["Bouncer Height", "Above-Waist No Ball", "Custom"];

// The backend is the source of truth for review-type capabilities: each module
// declares its label, camera role, timeline, evidence, replay mode and decision-card
// rows (see core/review_modules + /api/review-types). The static map above is the
// offline fallback + presentation extras (panel/feed/detail text); this merge keeps
// both in sync and surfaces any NEW backend module automatically.
async function loadReviewTypes() {
  try {
    const data = await jsonFetch("/api/review-types");
    for (const contract of data.types || []) {
      const key = contract.key;
      const existing = REVIEW_MODULES[key] || {
        panel: key,
        feedTitle: `${contract.required_role} Camera`,
        detailTitle: `${contract.label} Review`,
        detailSub: (contract.evidence || []).slice(0, 3).join(" · ") || "Review analysis",
      };
      REVIEW_MODULES[key] = {
        ...existing,
        label: contract.label,
        role: contract.required_role,
        stages: contract.timeline || existing.stages,
        evidence: contract.evidence || [],
        replayMode: contract.replay_mode || "generic",
        decisionCard: contract.decision_card || ["Decision"],
        // The operator protocol drives the review workspace (stage rail + flow).
        protocol: contract.protocol || existing.protocol,
        decisionMode: contract.decision_mode || existing.decisionMode || "automatic",
        supports: contract.supports || existing.supports || { frame_step: true },
      };
    }
    // Rebuild the selector so backend-declared types (e.g. a new module) appear.
    els.reviewTypeHeader?.querySelectorAll(".rt-btn").forEach((b) => b.remove());
    buildReviewSelector();
    applyReviewType();
  } catch {
    /* offline: the static registry above still drives the UI */
  }
}

const CAMERA_ROLES = ["Ball Tracking", "Front Foot", "Wide Camera", "Replay Camera", "Stump Camera", "Broadcast Camera", "Reserve"];
const DEFAULT_ROLE_BY_INDEX = ["Ball Tracking", "Stump Camera", "Front Foot", "Wide Camera", "Replay Camera", "Broadcast Camera"];
const VIEW_TITLES = { dashboard: "Dashboard", reviews: "Reviews", replay: "Replay", "sync-replay": "Sync Replay", cameras: "Cameras", "camera-health": "Camera Health", calibration: "Calibration", testing: "Testing", validation: "Validation", models: "Model Manager", health: "System", checklist: "Pre-Match Checklist", development: "Vision Studio", settings: "Settings" };

const store = {
  get(key, fallback) {
    try { const raw = localStorage.getItem(key); return raw === null ? fallback : JSON.parse(raw); }
    catch { return fallback; }
  },
  set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch {} },
};

const state = {
  decision: null,
  cameras: [],
  mode: { id: "visible", label: "Mode A - visible-spectrum approximation" },
  activeAppeal: false,
  // Review generation counter: bumped whenever a review starts OR is finished/
  // cancelled. An in-flight appeal response whose generation is stale must NOT
  // touch the UI (it would reopen the workspace as a zombie after confirm).
  reviewGen: 0,
  replayFrame: 0,
  replayTimer: null,
  panelMode: "live",
  testingPanel: null,
  statusPanel: null,
  calibrationModal: null,
  activeVideoInfo: null,
  lastStatus: "WAITING",
  lastHealth: null,
  reviewStartMs: null,
  reviewElapsed: null,
  // Operator system always launches idle. The last-open view and the last review
  // type belong to the previous session/match, not the next one, so they are NOT
  // restored — every launch defaults to the Dashboard in LBW. Only operator
  // settings, theme, and camera assignments persist (see the store.get calls below).
  view: "dashboard",
  operatorMode: store.get("drs.operatorMode", "match"),
  reviewType: "lbw",
  cameraRoles: store.get("drs.cameraRoles", {}),
  primaryOverride: null,
  match: null,
  confirmHold: false,
  confirmHoldTimer: null,
  queue: [],
  queueSeq: 0,
  // Which cameras the operator marked in-use (null = all detected). Set on the
  // Cameras page; the read-only checklist reads it.
  preflightSelected: store.get("drs.preflightCameras", null),
  // Reviews page client-side filter/search (see renderReviews).
  reviewFilter: "all",
  reviewSearch: "",
  reviewsCache: [],
  // Replay workspace: layers the operator switched OFF (per session; defaults on).
  replayLayerOff: {},
  // Canonical pipeline job for the current live appeal: {jobId, results} once complete.
  canonical: null,
  // Latest /api/preflight payload — feeds the dashboard's one-line readiness.
  readiness: null,
};

const timers = {};

const els = {
  appShell: document.querySelector(".app-shell"),
  sidebar: document.getElementById("sidebar"),
  sidebarToggle: document.getElementById("sidebar-toggle"),
  viewTitle: document.getElementById("view-title"),
  engineState: document.getElementById("engine-state"),
  liveIndicator: document.getElementById("live-indicator"),
  modeBanner: document.getElementById("mode-banner"),
  modeToggle: document.getElementById("mode-toggle"),
  systemStatus: document.getElementById("system-status"),
  systemAlerts: document.getElementById("system-alerts"),
  reviewTypeHeader: document.getElementById("review-type-header"),
  rtState: document.getElementById("rt-state"),
  dashGrid: document.getElementById("dash-grid"),
  leftPanelTitle: document.getElementById("left-panel-title"),
  cameraPills: document.getElementById("camera-pills"),
  cameraCount: document.getElementById("camera-count"),
  cameraGrid: document.getElementById("camera-grid"),
  cameraThumbs: document.getElementById("camera-thumbs"),
  primaryFeed: document.getElementById("primary-feed"),
  replayFeed: document.getElementById("replay-feed"),
  primaryTag: document.getElementById("primary-tag"),
  feedTitle: document.getElementById("feed-title"),
  feedSub: document.getElementById("feed-sub"),
  reviewStrip: document.getElementById("review-strip"),
  reviewStripTitle: document.getElementById("review-strip-title"),
  reviewStripElapsed: document.getElementById("review-strip-elapsed"),
  openReviewMode: document.getElementById("open-review-mode"),
  readinessStrip: document.getElementById("readiness-strip"),
  readinessText: document.getElementById("readiness-text"),
  readinessDetail: document.getElementById("readiness-detail"),
  healthGrid: document.getElementById("health-grid"),
  preflightSummary: document.getElementById("preflight-summary"),
  preflightGrid: document.getElementById("preflight-grid"),
  preflightBanner: document.getElementById("preflight-banner"),
  camerasInUse: document.getElementById("cameras-inuse"),
  reviewsList: document.getElementById("reviews-list"),
  reviewsCount: document.getElementById("reviews-count"),
  revFilters: document.getElementById("rev-filters"),
  revSearch: document.getElementById("rev-search"),
  systemInfoGrid: document.getElementById("system-info-grid"),
  activityLog: document.getElementById("activity-log"),
  activityRefresh: document.getElementById("activity-refresh"),
  replayStage: document.getElementById("replay-stage"),
  replayOverlay: document.getElementById("replay-overlay"),
  replayLayers: document.getElementById("replay-layers"),
  replayAudioStrip: document.getElementById("replay-audio-strip"),
  replayAudioTrack: document.getElementById("replay-audio-track"),
  replayAudioNote: document.getElementById("replay-audio-note"),
  replayJumpDecision: document.getElementById("replay-jump-decision"),
  replayZoom: document.getElementById("replay-zoom"),
  rpBallPath: document.getElementById("rp-ball-path"),
  rpBallNow: document.getElementById("rp-ball-now"),
  camhealthGrid: document.getElementById("camhealth-grid"),
  camhealthSummary: document.getElementById("camhealth-summary"),
  openSettings: document.getElementById("open-settings"),
  kpiFps: document.getElementById("kpi-fps"),
  kpiLatency: document.getElementById("kpi-latency"),
  kpiInference: document.getElementById("kpi-inference"),
  kpiTracking: document.getElementById("kpi-tracking"),
  kpiSync: document.getElementById("kpi-sync"),
  kpiGpu: document.getElementById("kpi-gpu"),
  kpiCuda: document.getElementById("kpi-cuda"),
  kpiQueue: document.getElementById("kpi-queue"),
  kpiCalibration: document.getElementById("kpi-calibration"),
  kpiModel: document.getElementById("kpi-model"),
  calibReadiness: document.getElementById("calib-readiness"),
  calibRms: document.getElementById("calib-rms"),
  calibCams: document.getElementById("calib-cams"),
  calibHomography: document.getElementById("calib-homography"),
  reviewQueue: document.getElementById("review-queue"),
  queueCount: document.getElementById("queue-count"),
  matchName: document.getElementById("match-name"),
  matchStatus: document.getElementById("match-status"),
  newMatchBtn: document.getElementById("new-match"),
  openHistoryBtn: document.getElementById("open-history"),
  newMatchDialog: document.getElementById("new-match-dialog"),
  nmCurrentName: document.getElementById("nm-current-name"),
  nmCurrentCount: document.getElementById("nm-current-count"),
  nmTeam1: document.getElementById("nm-team1"),
  nmTeam2: document.getElementById("nm-team2"),
  nmOperator: document.getElementById("nm-operator"),
  nmTournament: document.getElementById("nm-tournament"),
  nmVenue: document.getElementById("nm-venue"),
  nmGround: document.getElementById("nm-ground"),
  nmConfirm: document.getElementById("nm-confirm"),
  nmCancel: document.getElementById("nm-cancel"),
  historyDialog: document.getElementById("match-history-dialog"),
  closeHistory: document.getElementById("close-history"),
  historyList: document.getElementById("history-list"),
  historyDetail: document.getElementById("history-detail"),
  frameTimeline: document.getElementById("frame-timeline"),
  frameLabel: document.getElementById("frame-label"),
  requestReview: document.getElementById("request-review"),
  calibrationButton: document.getElementById("calibration-button"),
  replayBack: document.getElementById("replay-back"),
  replayForward: document.getElementById("replay-forward"),
  replayPlay: document.getElementById("replay-play"),
  replayPause: document.getElementById("replay-pause"),
  replaySpeed: document.getElementById("replay-speed"),
  replayExport: document.getElementById("replay-export"),
  aiDevelopmentOutput: document.getElementById("ai-development-output"),
  visionStudioStatus: document.getElementById("vision-studio-status"),
  visionStudioReady: document.getElementById("vision-studio-ready"),
  visionStudioProject: document.getElementById("vision-studio-project"),
  visionStudioWorkspace: document.getElementById("vision-studio-workspace"),
  visionStudioDataset: document.getElementById("vision-studio-dataset"),
  visionStudioModel: document.getElementById("vision-studio-model"),
  visionStudioGpu: document.getElementById("vision-studio-gpu"),
  visionStudioCuda: document.getElementById("vision-studio-cuda"),
  visionStudioVersion: document.getElementById("vision-studio-version"),
  visionStudioRecent: document.getElementById("vision-studio-recent"),
  developmentLock: document.getElementById("development-lock"),
  openVisionStudio: document.getElementById("open-vision-studio"),
  openVisionStudioWorkspace: document.getElementById("open-vision-studio-workspace"),
  importMatchRecordings: document.getElementById("import-match-recordings"),
  testingDialog: document.getElementById("testing-platform-dialog"),
  testingFrame: document.getElementById("testing-platform-frame"),
  closeTesting: document.getElementById("close-testing-platform"),
  testingRoot: document.getElementById("testing-root"),
  validationRoot: document.getElementById("validation-root"),
  modelsRoot: document.getElementById("models-root"),
  calibrationRoot: document.getElementById("calibration-root"),
  // operator + settings controls
  opModeMatch: document.getElementById("mode-match"),
  opModeEngineer: document.getElementById("mode-engineer"),
  settingsModeMatch: document.getElementById("settings-mode-match"),
  settingsModeEngineer: document.getElementById("settings-mode-engineer"),
  calmToggle: document.getElementById("calm-toggle"),
  settingsCollapse: document.getElementById("settings-collapse"),
  settingsModeAnalysis: document.getElementById("settings-mode-analysis"),
};

async function jsonFetch(route, options = {}) {
  const response = await fetch(`${API_BASE}${route}`, options);
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

function setEngineOnline(online) {
  els.liveIndicator.classList.toggle("offline", !online);
  els.liveIndicator.querySelector("span").textContent = online ? "Live" : "Offline";
}

/* ===================== view router + operator mode ===================== */
function setView(view) {
  if (!VIEW_TITLES[view]) view = "dashboard";
  const previous = state.view;
  state.view = view;
  // Intentionally not persisted: the operator always relaunches on the Dashboard.
  document.querySelectorAll(".view").forEach((section) => section.classList.toggle("active", section.dataset.view === view));
  document.querySelectorAll(".nav-item[data-view]").forEach((nav) => nav.classList.toggle("active", nav.dataset.view === view));
  els.viewTitle.textContent = VIEW_TITLES[view];
  renderMode();
  if (view === "testing") ensureTestingPanel();
  if (view === "validation") ensureValidationPanel();
  if (view === "models") ensureModelManagerPanel();
  if (view === "development") refreshAiDevelopmentStatus();
  if (view === "checklist" || view === "dashboard") refreshPreflight();
  if (view === "cameras") renderCamerasInUse();
  if (view === "reviews") renderReviews();
  if (view === "health") renderSystemView();
  if (view === "replay") { applyReplayMode(); armReplayWorkspace(); }
  if (view === "sync-replay") SyncReplay.ensure();
  if (view === "camera-health") renderCameraHealth();
  if (view === "calibration") state.calibrationModal?.activate?.();
  else if (previous === "calibration") state.calibrationModal?.deactivate?.();
}

function setOperatorMode(mode) {
  state.operatorMode = mode === "engineer" ? "engineer" : "match";
  store.set("drs.operatorMode", state.operatorMode);
  document.body.classList.toggle("mode-engineer", state.operatorMode === "engineer");
  document.body.classList.toggle("mode-match", state.operatorMode === "match");
  const engineer = state.operatorMode === "engineer";
  [els.opModeEngineer, els.settingsModeEngineer].forEach((b) => b && b.classList.toggle("active", engineer));
  [els.opModeMatch, els.settingsModeMatch].forEach((b) => b && b.classList.toggle("active", !engineer));
}

function applySidebarState() {
  const collapsed = store.get("drs.sidebarCollapsed", false);
  els.appShell.classList.toggle("collapsed", collapsed);
  if (els.sidebarToggle) els.sidebarToggle.title = collapsed ? "Expand sidebar" : "Collapse sidebar";
}

function toggleSidebar() {
  const collapsed = !els.appShell.classList.contains("collapsed");
  els.appShell.classList.toggle("collapsed", collapsed);
  store.set("drs.sidebarCollapsed", collapsed);
  if (els.sidebarToggle) els.sidebarToggle.title = collapsed ? "Expand sidebar" : "Collapse sidebar";
}

function setCalm(on) {
  document.body.classList.toggle("calm", on);
  store.set("drs.calm", on);
}

/* ===================== review modules ===================== */
function buildReviewSelector() {
  const buttons = Object.entries(REVIEW_MODULES)
    .map(([key, mod]) => `<button class="rt-btn ${key === state.reviewType ? "active" : ""}" type="button" data-review-type="${key}">${mod.label}</button>`)
    .join("");
  els.rtState.insertAdjacentHTML("beforebegin", buttons);
  els.reviewTypeHeader.querySelectorAll(".rt-btn").forEach((button) => {
    button.addEventListener("click", () => setReviewType(button.dataset.reviewType));
  });
}

function setReviewType(type) {
  if (!REVIEW_MODULES[type]) type = "lbw";
  state.reviewType = type;
  state.primaryOverride = null;
  // Intentionally not persisted: every launch defaults back to LBW.
  applyReviewType();
  refreshCameraFrames();
}

function applyReviewType() {
  const type = state.reviewType;
  const mod = REVIEW_MODULES[type];
  document.querySelectorAll(".rt-btn").forEach((b) => b.classList.toggle("active", b.dataset.reviewType === type));
  if (els.feedTitle) els.feedTitle.textContent = mod.feedTitle;
  if (els.feedSub) els.feedSub.textContent = `${mod.label} primary feed`;
  // The Replay workspace declares which replay mode the active review type uses
  // (from the module contract) — trajectory for LBW, frame-stepping for Run Out, etc.
  const replaySubEl = document.getElementById("replay-sub");
  if (replaySubEl) {
    const modeLabel = {
      trajectory: "Trajectory replay — ball path, prediction, stumps",
      wide_line: "Wide-line replay — ball path, crease, guideline",
      freeze_frame: "Freeze-frame replay — front foot, crease, zoom",
      frame_stepping: "Frame-by-frame replay — crease, bat, bails",
      audio_sync: "Audio-sync replay — waveform, spike, HotSpot",
      generic: "Frame-by-frame appeal inspection",
    }[mod.replayMode || "generic"];
    replaySubEl.textContent = `${mod.label} · ${modeLabel}`;
  }
  applyReplayMode();
}

/* ===================== camera roles + primary selection ===================== */
function cameraRoleFor(cameraId) {
  if (state.cameraRoles[cameraId]) return state.cameraRoles[cameraId];
  const index = state.cameras.findIndex((camera) => camera.id === cameraId);
  return DEFAULT_ROLE_BY_INDEX[index] || "Reserve";
}

function setCameraRole(cameraId, role) {
  state.cameraRoles[cameraId] = role;
  store.set("drs.cameraRoles", state.cameraRoles);
  renderCameraGrid();
  renderCameraThumbs();
  refreshCameraFrames();
}

function getPrimaryCameraId() {
  const connected = state.cameras.filter((camera) => camera.connected);
  if (state.primaryOverride != null) {
    const override = connected.find((camera) => camera.id === state.primaryOverride);
    if (override) return override.id;
  }
  const wantRole = REVIEW_MODULES[state.reviewType].role;
  const match = connected.find((camera) => cameraRoleFor(camera.id) === wantRole);
  if (match) return match.id;
  if (connected.length) return connected[0].id;
  return state.cameras[0]?.id ?? null;
}

async function refreshHealth() {
  try {
    const health = window.drs?.getHealth ? await window.drs.getHealth() : await jsonFetch("/api/health");
    setEngineOnline(true);
    els.engineState.textContent = `Engine ${health.status || "ok"} | ${health.active_model_name || "model"} | ${formatDuration(health.uptime_seconds)}`;
  } catch {
    setEngineOnline(false);
    els.engineState.textContent = "Engine offline";
  }
}

async function refreshSystemHealth() {
  try {
    const health = window.drs?.getSystemHealth ? await window.drs.getSystemHealth() : await jsonFetch("/api/system/health");
    renderSystemPayload(health);
  } catch {
    els.healthGrid.innerHTML = `<div><span>Health</span><strong>Offline</strong></div>`;
  }
}

async function refreshCameraStatus() {
  try {
    const payload = await jsonFetch("/api/cameras/fps");
    state.cameras = payload.cameras || [];
    state.mode = payload.mode || state.mode;
    renderMode();
    renderCameraGrid();
    renderCameraThumbs();
    refreshCameraFrames();
    renderSystemStatus();
    renderCameraHealth();
    renderCamerasInUse();
  } catch {
    state.cameras = [];
    renderCameraGrid();
    renderCameraThumbs();
    renderCameraHealth();
    renderCamerasInUse();
  }
}

function renderMode() {
  // During calibration the analysis mode is irrelevant to the operator; show the
  // camera picture instead of the engineering mode label.
  if (state.view === "calibration") {
    const connected = state.cameras.filter((camera) => camera.connected);
    const roles = connected.map((camera) => cameraRoleFor(camera.id)).filter((role) => role && role !== "Reserve");
    els.modeBanner.textContent = connected.length
      ? `${connected.length} active camera${connected.length === 1 ? "" : "s"}${roles.length ? " — " + roles.join(" • ") : ""}`
      : "No cameras connected";
    els.modeBanner.classList.remove("thermal");
  } else {
    els.modeBanner.textContent = state.mode.label;
    els.modeBanner.classList.toggle("thermal", state.mode.id === "thermal_demo");
  }
}

function renderCameraMeta() {
  const connected = state.cameras.filter((camera) => camera.connected);
  const text = `${connected.length} / ${state.cameras.length || MAX_CAMERAS} connected`;
  if (els.cameraCount) els.cameraCount.textContent = text;
  if (els.cameraPills) {
    els.cameraPills.innerHTML = state.cameras.map((camera) => (
      `<span class="camera-pill ${camera.status}">Cam ${camera.id} | ${Number(camera.fps || 0).toFixed(1)} fps</span>`
    )).join("");
  }
}

// Structural signature of the camera set (ids, connected, roles). While this is
// unchanged we update fps/status in place instead of rebuilding innerHTML, so the
// grid does not flicker and an open role menu is not wiped on every live tick.
let _lastGridSig = "";
let _lastThumbSig = "";
function cameraStructSignature() {
  return state.panelMode + "|" + state.cameras
    .map((c) => `${c.id}:${c.connected ? 1 : 0}:${cameraRoleFor(c.id)}`)
    .join(",");
}
function updateCameraGridInPlace() {
  state.cameras.forEach((camera) => {
    const panel = els.cameraGrid.querySelector(`.camera-panel[data-camera-id="${camera.id}"]`);
    if (!panel) return;
    panel.className = `camera-panel ${camera.connected ? (camera.status || "online") : "offline"}`;
    const fpsEl = panel.querySelector(".camera-fps");
    if (fpsEl) fpsEl.textContent = `${Number(camera.fps || 0).toFixed(1)} fps`;
  });
}

function renderCameraGrid() {
  renderCameraMeta();
  if (state.panelMode !== "live") return;
  if (!els.cameraGrid) return;
  const sig = cameraStructSignature();
  if (sig === _lastGridSig && els.cameraGrid.childElementCount) {
    updateCameraGridInPlace();
    return;
  }
  _lastGridSig = sig;
  els.cameraGrid.className = "camera-grid";
  if (!state.cameras.length) {
    els.cameraGrid.innerHTML = `<div class="analysis-tile"><span>Cameras</span><strong>No cameras detected</strong><small>Connect cameras or start the backend.</small></div>`;
    return;
  }
  els.cameraGrid.innerHTML = state.cameras.map((camera) => {
    const role = cameraRoleFor(camera.id);
    const statusClassName = camera.connected ? (camera.status || "online") : "offline";
    const video = camera.connected
      ? `<img id="camera-${camera.id}" alt="Camera ${camera.id} feed" />
         <div class="camera-placeholder">Waiting for feed</div>
         <div class="camera-label">Camera ${camera.id}</div>
         <div class="camera-fps">${Number(camera.fps || 0).toFixed(1)} fps</div>`
      : `<div class="camera-placeholder">Camera ${camera.id} — not connected</div>
         <div class="camera-label">Camera ${camera.id}</div>`;
    const menu = CAMERA_ROLES.map((r) => `<button type="button" data-camera-id="${camera.id}" data-role="${r}" class="${r === role ? "active" : ""}">${r}</button>`).join("");
    return `
      <article class="camera-panel ${statusClassName}" data-camera-id="${camera.id}">
        <div class="cam-video">${video}</div>
        <div class="cam-role">
          <div class="cam-role-current"><label>Role</label><span class="role-chip">${role}</span></div>
          <button type="button" class="change-role" data-camera-id="${camera.id}">Change Role</button>
          <div class="role-menu" data-camera-id="${camera.id}" hidden>${menu}</div>
        </div>
      </article>`;
  }).join("");
  els.cameraGrid.querySelectorAll(".camera-panel img").forEach((img) => {
    img.addEventListener("load", () => { const ph = img.nextElementSibling; if (ph) ph.hidden = true; img.style.opacity = "1"; });
    img.addEventListener("error", () => { const ph = img.nextElementSibling; if (ph) ph.hidden = false; img.style.opacity = "0"; });
  });
  els.cameraGrid.querySelectorAll(".change-role").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      const menu = button.parentElement.querySelector(".role-menu");
      const wasHidden = menu.hidden;
      closeRoleMenus();
      menu.hidden = !wasHidden;
    });
  });
  els.cameraGrid.querySelectorAll(".role-menu button").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      setCameraRole(Number(button.dataset.cameraId), button.dataset.role);
    });
  });
}

function closeRoleMenus() {
  document.querySelectorAll(".role-menu").forEach((menu) => { menu.hidden = true; });
}

// Smart thumbnails: role + FPS + LIVE rather than camera numbers (item 3)
function renderCameraThumbs() {
  renderCameraMeta();
  if (!els.cameraThumbs) return;
  const connected = state.cameras.filter((camera) => camera.connected);
  const primaryId = getPrimaryCameraId();
  const sig = cameraStructSignature() + "|p" + primaryId;
  if (sig === _lastThumbSig && els.cameraThumbs.childElementCount) {
    connected.forEach((camera) => {
      const meta = els.cameraThumbs.querySelector(`.cam-thumb[data-camera-id="${camera.id}"] .t-meta`);
      if (meta) meta.innerHTML = `${Number(camera.fps || 0).toFixed(1)} FPS<span class="t-live">LIVE</span>`;
    });
    return;
  }
  _lastThumbSig = sig;
  els.cameraThumbs.innerHTML = connected.map((camera) => `
    <button type="button" class="cam-thumb ${camera.id === primaryId ? "active" : ""}" data-camera-id="${camera.id}">
      <img id="thumb-${camera.id}" alt="Camera ${camera.id}" />
      <div class="t-info">
        <span class="t-role">${cameraRoleFor(camera.id)}</span>
        <span class="t-meta">${Number(camera.fps || 0).toFixed(1)} FPS<span class="t-live">LIVE</span></span>
      </div>
    </button>`).join("") || `<span class="muted">No live cameras</span>`;
  els.cameraThumbs.querySelectorAll(".cam-thumb").forEach((thumb) => {
    thumb.addEventListener("click", () => {
      state.primaryOverride = Number(thumb.dataset.cameraId);
      renderCameraThumbs();
      refreshCameraFrames();
    });
  });
  const primaryRole = primaryId != null ? cameraRoleFor(primaryId) : "--";
  if (els.primaryTag) els.primaryTag.textContent = primaryId != null ? `Cam ${primaryId} · ${primaryRole}` : "No camera";
}

function refreshCameraFrames() {
  const stamp = Date.now();
  const primaryId = getPrimaryCameraId();
  state.cameras.filter((camera) => camera.connected).forEach((camera) => {
    const src = `${API_BASE}/api/live/${camera.id}.jpg?t=${stamp}`;
    const grid = document.getElementById(`camera-${camera.id}`);
    if (grid) grid.src = src;
    const thumb = document.getElementById(`thumb-${camera.id}`);
    if (thumb) thumb.src = src;
  });
  if (primaryId != null) {
    const psrc = `${API_BASE}/api/live/${primaryId}.jpg?t=${stamp}`;
    if (els.primaryFeed) els.primaryFeed.src = psrc;
    // The replay stage mirrors live ONLY until a frozen buffer is armed —
    // after that the stage belongs to the buffer frames at the cursor.
    if (els.replayFeed && !state.replayArmed) els.replayFeed.src = psrc;
  }
}

function renderLiveFrames(frames) {
  const primaryId = getPrimaryCameraId();
  Object.entries(frames || {}).forEach(([cameraId, frame]) => {
    if (!frame.jpeg_base64) return;
    const src = `data:image/jpeg;base64,${frame.jpeg_base64}`;
    const grid = document.getElementById(`camera-${cameraId}`);
    if (grid) grid.src = src;
    const thumb = document.getElementById(`thumb-${cameraId}`);
    if (thumb) thumb.src = src;
    if (Number(cameraId) === primaryId) {
      if (els.primaryFeed) els.primaryFeed.src = src;
      if (els.replayFeed && !state.replayArmed) els.replayFeed.src = src;
    }
  });
}

async function refreshDecision() {
  // Suppress the poll during the confirm→reset handoff, so a GET dispatched just
  // before the reset can't resolve late and resurrect the verdict.
  if (state.confirmHold) return;
  try {
    const decision = await jsonFetch("/api/decision/current");
    renderDecision(decision);
  } catch {}
}

function renderDecision(decision) {
  state.decision = decision;
  const status = decision.status || "WAITING";
  state.activeAppeal = status !== "WAITING";
  const nowResolved = status === "OUT" || status === "NOT_OUT";
  const wasResolved = state.lastStatus === "OUT" || state.lastStatus === "NOT_OUT";
  if (nowResolved && !wasResolved) {
    state.reviewElapsed = state.reviewStartMs ? (Date.now() - state.reviewStartMs) / 1000 : null;
  }
  state.lastStatus = status;
  // The dashboard is a control room: the decision only drives the layout state,
  // the review strip and the match/queue records. ALL evidence rendering happens
  // in Review Mode — there is no second review surface to feed.
  renderDecisionState(status);
  if (nowResolved && !wasResolved) resolveQueue(status);
  if (els.kpiModel) els.kpiModel.textContent = pct(decision.overall_confidence ?? decision.ball_confidence);
  if (state.view === "replay") applyReplayMode();
  if (ReviewMode.active) ReviewMode.update(decision);
}

/* decision state machine + state-driven layout + contextual buttons (items 1, 5) */
function setReviewLayoutState(phase, status) {
  if (els.dashGrid) els.dashGrid.dataset.state = phase;
  if (els.rtState) {
    els.rtState.textContent = phase === "result" ? displayStatus(status || state.lastStatus)
      : phase === "processing" ? "Reviewing" : "Waiting";
  }
}

// Current Match lifecycle badge: WAITING → REVIEW IN PROGRESS → CONFIRMED → WAITING.
function setMatchStatus(kind) {
  if (!els.matchStatus) return;
  els.matchStatus.textContent = kind === "review" ? "REVIEW IN PROGRESS" : kind === "confirmed" ? "CONFIRMED" : "WAITING";
  els.matchStatus.className = `match-status ${kind}`;
}

// Canonical replay package for live LBW appeals — the dashboard consumes the SAME job
// results, endpoints and replay exports as the Testing page (one implementation).
let _canonicalJobWatching = null;
function watchCanonicalReview(decision) {
  // The canonical trajectory pipeline IS the LBW protocol's Ball-Tracking stage.
  // No other review type runs it — their protocols never mention trajectory, so
  // nothing here may touch their screens (the old "No pipeline replay for this
  // review type" dead-end came from exactly that leak).
  if ((decision?.review_type || state.reviewType) !== "lbw") return;
  state.canonical = null;
  syncCanonicalSurfaces();                                 // clear the badges for the new appeal
  const jobId = decision?.canonical_job_id;
  if (!jobId) {
    // honesty rule: never fabricate a replay. Operator copy up front, the
    // pipeline's own reason preserved for the engineer surfaces.
    const why = "Replay clip wasn't captured for this appeal — decide from the live replay.";
    if (ReviewMode.active) {
      ["rm-observed", "rm-broadcast"].forEach((id) => { const b = document.getElementById(id); if (b) b.innerHTML = `<div class="rm2-noreplay">${why}</div>`; });
      ReviewMode.renderFlow(decision);   // trajectory stage → skipped, via the engine
    }
    return;
  }
  _canonicalJobWatching = jobId;
  setCanonicalChip("Replay rendering …", false);
  const started = Date.now();
  const poll = async () => {
    if (_canonicalJobWatching !== jobId) return;           // superseded by a newer appeal
    if (Date.now() - started > 5 * 60 * 1000) {
      setCanonicalChip("Replay timed out — check the backend log", false);
      if (ReviewMode.active) ReviewMode.renderPending("Replay render timed out — decide from the frame-stepped evidence.");
      return;
    }
    try {
      const res = await fetch(`${API_BASE}/api/analyze/${jobId}/results`);
      if (!res.ok) {
        // Still processing — show the job's REAL progress (percent + step) in the
        // Review Mode chip, instead of a static note.
        try {
          const st = await fetch(`${API_BASE}/api/analyze/${jobId}/status`).then((r) => r.json());
          if (_canonicalJobWatching === jobId) {
            const pct = Number(st.progress ?? st.percent ?? 0);
            setCanonicalChip(`Replay rendering ${pct ? `${pct}%` : "…"}`, false);
            if (ReviewMode.active) ReviewMode.renderPending(`Rendering replay… ${pct ? pct + "%" : ""}`);
          }
        } catch { /* status endpoint unavailable — keep the previous note */ }
        setTimeout(poll, 3000); return;
      }
      const results = await res.json();
      state.canonical = { jobId, results };
      if (ReviewMode.active) ReviewMode.setCanonical(jobId, results);   // the single review workspace
      syncCanonicalSurfaces();                             // badge the chip + mirror into Replay
    } catch { setTimeout(poll, 3000); }
  };
  setTimeout(poll, 3000);
}

// Review Mode status chip: replay-rendering progress / readiness, visible WITHOUT
// leaving Review Mode. `ready` turns it green and clicking jumps to the videos view.
function setCanonicalChip(text, ready) {
  const chip = document.getElementById("rm-canonical-status");
  if (!chip) return;
  chip.hidden = !text;
  chip.textContent = text || "";
  chip.classList.toggle("ready", Boolean(ready));
  chip.disabled = !ready;
}

// Mirror the canonical replays into the Replay workspace (LBW mode) and badge
// the Review Mode chip so the operator knows the videos are ready.
function syncCanonicalSurfaces() {
  const replayHost = document.getElementById("replay-canonical");
  const c = state.canonical;
  const ready = Boolean(c && c.results && (c.results.exports || {}).replay_players);
  if (c && c.results) {
    setCanonicalChip(ready ? "DRS replay ready ✓ — view" : "Analysis done — no replay (see DRS Replay tab)", ready);
  } else if (!c) {
    setCanonicalChip("", false);
  }
  if (replayHost) {
    const showInReplay = ready && state.reviewType === "lbw";
    replayHost.hidden = !showInReplay;
    if (showInReplay) renderCanonicalReview(replayHost, c.jobId, c.results);
    else replayHost.innerHTML = "";
  }
  // The Ball-Tracking stage depends on the async gates — refresh BOTH protocol
  // surfaces from the single state machine now that canonical results have
  // landed: the workspace rail and the dashboard's Evidence Checklist (the
  // trajectory renders in the background while the operator is on the earlier
  // stages — the checklist row must flip to done on its own).
  if (ReviewMode.active) ReviewMode.renderProtocol();
  if (state.activeAppeal) {
    const phase = (state.lastStatus === "OUT" || state.lastStatus === "NOT_OUT") ? "result" : "processing";
    renderDashEvidence(phase, state.lastStatus);
  }
}

function renderCanonicalReview(host, jobId, results, which = "both") {
  const ex = results.exports || {};
  const g = results.reconstruction?.gates;
  const gates = g
    ? `<div class="cr-gates"><span>Pitching <b>${g.pitching}</b></span><span>Impact <b>${g.impact}</b></span><span>Wickets <b>${g.wickets}</b></span><span>Verdict <b>${g.verdict}</b></span></div>`
    : "";
  if (!ex.replay_players) {
    const t = results.trajectory || {};
    const why = t.valid === false
      ? `trajectory rejected: ${(t.reasons || []).join("; ") || t.observed?.end_reason || "invalid"}`
      : "replay generation failed";
    // Make the reason ACTIONABLE: distinguish "the camera never saw a ball"
    // (operator-fixable) from a render failure (check the logs).
    const trackingIssue = /tracked points|no ball|detection|gap|lost/i.test(why);
    const tips = trackingIssue
      ? ["Confirm the ball is clearly visible to the ball-tracking camera for at least ~10 frames",
         "Check lighting/exposure — a dim or blown-out ball won't detect",
         "Verify the camera points down the pitch (Cameras page → roles)"]
      : ["Check the Activity Log / backend log for the render error"];
    host.innerHTML = `${gates}<div class="cr-note">No DRS replay — ${why}</div>
      <ul class="cr-tips">${tips.map((tip) => `<li>${tip}</li>`).join("")}</ul>`;
    return;
  }
  const players = `<video muted playsinline controls autoplay loop src="${API_BASE}/api/testing/jobs/${jobId}/exports/replay_players"></video>`;
  const review = ex.replay_review
    ? `<video muted playsinline controls autoplay loop src="${API_BASE}/api/testing/jobs/${jobId}/exports/replay_review"></video>`
    : `<div class="cr-note">Clean broadcast replay was not generated for this delivery.</div>`;
  if (which === "players") host.innerHTML = `${gates}${players}`;
  else if (which === "review") host.innerHTML = `${gates}${review}`;
  else host.innerHTML = `${gates}<h4>Observed Trajectory</h4>${players}${ex.replay_review ? `<h4>DRS Review</h4>${review}` : ""}`;
}

function renderDecisionState(status) {
  let phase = "waiting";
  if (status === "PROCESSING") phase = "processing";
  else if (status === "OUT" || status === "NOT_OUT") phase = "result";
  setReviewLayoutState(phase, status);
  // Reflect the lifecycle on the Current Match badge, unless we're holding a brief
  // CONFIRMED flash right after a confirmation.
  if (!state.confirmHold) setMatchStatus(phase === "waiting" ? "waiting" : "review");
  els.requestReview.hidden = phase !== "waiting";
  renderReviewStrip(phase, status);
}

// The dashboard's ONE review presence. Idle → readiness ("can I review right
// now?"). Active → the protocol itself: which checks are done and what each
// concluded, which are still running, and the way back into the workspace.
// The dashboard never asks the operator to REMEMBER the protocol — it shows it.
function renderReviewStrip(phase, status) {
  if (!els.reviewStrip) return;
  const active = phase !== "waiting";
  els.reviewStrip.hidden = !active;
  els.reviewStrip.classList.toggle("result", phase === "result");
  // The two lines share one slot — readiness only matters between reviews.
  if (els.readinessStrip) els.readinessStrip.hidden = active || !state.readiness;
  if (!active) { stopReviewClock(); return; }
  const mod = REVIEW_MODULES[state.decision?.review_type || state.reviewType] || {};
  if (phase === "result") {
    const word = state.decision?.outcome || displayStatus(status);
    if (els.reviewStripTitle) els.reviewStripTitle.textContent = `${mod.label || "Review"} · decision ready — ${word}`;
    stopReviewClock();
    if (els.reviewStripElapsed) els.reviewStripElapsed.textContent = "Confirm in the workspace";
  } else {
    if (els.reviewStripTitle) els.reviewStripTitle.textContent = `${mod.label || "Review"} review running`;
    startReviewClock();
  }
  renderDashEvidence(phase, status);
}

// Checklist row icons speak the same tri-state language as the workspace rail.
const EV_ICONS = { passed: "✓", processing: "⏳", waiting: "○", skipped: "⚠", failed: "✕" };
const EV_FALLBACK_WORD = { passed: "DONE", processing: "Processing", waiting: "Pending", skipped: "Skipped", failed: "Failed" };

// The Evidence Checklist: the active type's protocol from the ONE state machine
// (computeFlow), one row per evidence stage. Clicking a row opens the workspace
// ON that stage — the dashboard is the control panel, the workspace is where the
// evidence is inspected. The Decision footer answers "can I decide yet?".
function renderDashEvidence(phase, status) {
  const host = document.getElementById("rs-evidence");
  const dec = document.getElementById("rs-decision");
  if (!host || !dec) return;
  const type = state.decision?.review_type || state.reviewType;
  const flow = computeFlow(type, state.decision || {});
  const rows = flow.stages.filter((s) => s.key !== "decision");
  host.hidden = !rows.length;
  host.innerHTML = rows.map((s) => `
    <button type="button" class="rs-ev ${s.state}" data-stage="${s.key}" title="${(s.note || s.label).replace(/"/g, "&quot;")} — click to open this evidence">
      <span class="rs-ev-ico">${EV_ICONS[s.state] || "○"}</span>
      <span class="rs-ev-lbl">${s.label}</span>
      <span class="rs-ev-word">${s.word || EV_FALLBACK_WORD[s.state] || ""}</span>
    </button>`).join("");
  // Decision footer: ready only when every evidence stage has settled.
  const settled = rows.length && rows.every((s) => s.state === "passed" || s.state === "skipped" || s.state === "failed");
  dec.hidden = false;
  let text, cls;
  if (phase === "result") {
    text = `Ready for decision — confirm ${state.decision?.outcome || displayStatus(status)} in the workspace`;
    cls = "ready";
  } else if (settled) {
    const rr = state.decision?.review_result || {};
    const reading = rr.verdict && !NON_VERDICTS.test(rr.verdict) && rr.verdict !== "INCONCLUSIVE" ? rr.verdict : null;
    text = reading ? `Ready for decision${isAssisted(type) ? ` — system reading ${reading} (advisory)` : ` — system reads ${reading}`}` : "Evidence complete — your call from the workspace";
    cls = "ready";
  } else {
    text = "Waiting for evidence";
    cls = "";
  }
  dec.className = `rs-decision ${cls}`;
  dec.innerHTML = `<span class="rs-ev-lbl">Decision</span><span class="rs-dec-text">${text}</span>`;
}

// Elapsed time is the one number worth watching from the dashboard: a review that
// has been running too long is the operator's cue to go look at it.
function startReviewClock() {
  paintReviewClock();
  if (timers.reviewClock) return;
  timers.reviewClock = setInterval(paintReviewClock, 1000);
}

function stopReviewClock() {
  clearInterval(timers.reviewClock);
  timers.reviewClock = null;
}

function paintReviewClock() {
  if (!els.reviewStripElapsed) return;
  // Honest when unknown: a review already running when this window opened has no
  // local start time, so we say nothing rather than invent an elapsed figure.
  if (!state.reviewStartMs) { els.reviewStripElapsed.textContent = ""; return; }
  const secs = Math.max(0, Math.round((Date.now() - state.reviewStartMs) / 1000));
  const mm = String(Math.floor(secs / 60)).padStart(2, "0");
  const ss = String(secs % 60).padStart(2, "0");
  els.reviewStripElapsed.textContent = `Started ${mm}:${ss} ago`;
}

// Readiness: a ONE-LINE summary of the checklist the operator would otherwise
// have to open. Same /api/preflight the Pre-Match Checklist uses — one source of
// truth, no second opinion about whether the rig is ready.
function renderReadiness(data) {
  state.readiness = data;
  if (!els.readinessStrip) return;
  const blocking = (data.blocking || []).length;
  const warnings = (data.warnings || []).length;
  const kind = blocking ? "fail" : warnings ? "warn" : "pass";
  els.readinessStrip.className = `readiness-strip ${kind}`;
  els.readinessStrip.hidden = state.activeAppeal;
  els.readinessText.textContent = blocking ? "Not ready to review"
    : warnings ? "Ready — with warnings" : "Ready to review";
  // Name the first thing that is actually wrong; a count alone isn't actionable.
  const first = (data.items || []).find((i) => i.status === (blocking ? "fail" : "warn"));
  const summary = data.summary || {};
  els.readinessDetail.textContent = blocking || warnings
    ? `${first ? first.label : `${blocking + warnings} checks`} — open the checklist`
    : `${summary.pass || 0} checks passed`;
}

// Reopen the workspace for the review that is already running (the strip's
// button). Canonical replays that finished while the workspace was closed are
// re-attached from state.
function reopenReviewMode() {
  if (ReviewMode.active) return;
  const decision = state.decision && state.decision.status !== "WAITING"
    ? state.decision
    : { review_type: state.reviewType, status: "PROCESSING", review_result: { verdict: "REVIEWING" } };
  ReviewMode.enter(decision);
  if (state.canonical?.results) ReviewMode.setCanonical(state.canonical.jobId, state.canonical.results);
}

// A checklist row is a deep link: open (or refocus) the workspace ON that
// stage's evidence. From the dashboard the operator clicks WHAT they want to
// inspect, not just "the workspace".
function openReviewAtStage(stageKey) {
  if (!ReviewMode.active) reopenReviewMode();
  if (ReviewMode.active && stageKey) ReviewMode.focusStage(stageKey);
}


// ── PROTOCOL ENGINE — one state machine per review ───────────────────────────
// The active review type's contract declares its ordered operator stages
// (REVIEW_MODULES[type].protocol, served by /api/review-types). This engine is
// the ONLY thing that decides stage states; every screen renders FROM it — a
// Wide review is built from Wide's protocol and never learns trajectory exists.
// Stage states:
//   waiting    — not started yet
//   processing — the review is live but this stage's result hasn't arrived
//   passed     — the check completed and produced a result
//   skipped    — the check can't run here (no camera / mic / track); the note
//                tells the operator what to do instead — the review continues
//   failed     — the check ran and genuinely errored
// Every stage carries an OPERATOR-language note; internal pipeline terminology
// never reaches the rail (raw detail stays on hover / Engineer Mode surfaces).

// Translate backend reason strings into umpire language. Last resort: the honest
// raw reason (better than silence), but every known case reads like an instruction.
function operatorReason(reason) {
  if (!reason) return "";
  if (/no ball detected|not detected/i.test(reason)) return "Ball not picked up on this camera — judge from the replay";
  if (/not calibrated|calibrat/i.test(reason)) return "Camera not calibrated — measurements unavailable; judge from the replay";
  if (/no .*camera|camera .*unavailable|unavailable/i.test(reason)) return "Camera unavailable for this check — judge from the replay";
  if (/did not reach|insufficient/i.test(reason)) return "Not enough of the flight was captured — judge from the replay";
  return reason;
}

const fmtCm = (v) => (v == null || !Number.isFinite(Number(v)) ? null : `${Math.abs(Number(v)).toFixed(1)} cm`);

// In-flight status words are NOT verdicts — they must never reach the operator
// as if the system had made a call.
const NON_VERDICTS = /^(AWAITING|PROCESSING|REVIEWING|WAITING)$/i;

// One evaluator per stage KEY (stage keys are shared across types on purpose:
// "front_foot" behaves identically in an LBW and a No Ball review).
const STAGE_EVAL = {
  front_foot(decision, pending) {
    const nb = decision.no_ball_analysis || decision.noball || {};
    if (nb.is_no_ball === true) return { state: "passed", word: "NO BALL", note: `NO BALL — over the line${fmtCm(nb.distance_past_cm) ? ` by ${fmtCm(nb.distance_past_cm)}` : ""}` };
    if (nb.is_no_ball === false) return { state: "passed", word: "NOT A NO BALL", note: "Front foot behind the line — legal delivery" };
    if (nb.reason) return { state: "skipped", word: "NOT SEEN", note: "Front foot not visible — check it on the replay" };
    return { state: pending, note: "Checking the front foot…" };
  },
  ultra_edge(decision, pending) {
    const edge = decision.edge_analysis || {};
    if (edge.available === false || edge.inconclusive) return { state: "skipped", word: "NO AUDIO", note: "No stump-mic audio — clear the bat from the slow-motion replay" };
    if ((edge.events || []).length || edge.edge_probability != null) {
      const hit = (edge.edge_probability || 0) >= 0.5;
      return { state: "passed", word: hit ? "POSSIBLE EDGE" : "NO EDGE", note: hit ? "Possible bat involvement — review the spike" : "No bat involvement detected" };
    }
    return { state: pending, note: "Listening for an edge…" };
  },
  audio_sync(decision, pending) {
    const edge = decision.edge_analysis || {};
    if (edge.available === false || edge.inconclusive) return { state: "skipped", word: "NO AUDIO", note: "No stump microphone — judge from the slow-motion replay" };
    if ((edge.events || []).length || edge.edge_probability != null) {
      const n = (edge.events || []).length;
      return { state: "passed", word: n ? `${n} SPIKE${n === 1 ? "" : "S"}` : "NO SPIKES", note: n ? `${n} spike${n === 1 ? "" : "s"} found — step to the marked frames` : "Audio analysed — no spikes found" };
    }
    return { state: pending, note: "Synchronising audio with the replay…" };
  },
  trajectory(decision, pending) {
    const canon = state.canonical;
    if (canon && canon.results) {
      const gates = canon.results.reconstruction && canon.results.reconstruction.gates;
      const hasReplay = Boolean((canon.results.exports || {}).replay_players);
      // The wickets gate is THE trajectory verdict word (HITTING / MISSING);
      // a track without gates is honest about being just a track.
      if (gates || hasReplay) return { state: "passed", word: (gates && gates.wickets) ? String(gates.wickets).replace(/_/g, " ").toUpperCase() : "TRACKED", note: "Ball track ready — read pitching, impact and wickets" };
      return { state: "skipped", word: "NOT TRACKED", note: "Ball couldn't be tracked — decide from the replay" };
    }
    if (decision.canonical_skip_reason) return { state: "skipped", word: "NOT TRACKED", note: "Ball tracking unavailable — decide from the replay" };
    if (typeof ReviewMode !== "undefined" && ReviewMode.active && ReviewMode.restored) {
      return { state: "skipped", word: "NOT STORED", note: "Tracking data is not stored for this review" };
    }
    if (decision.canonical_job_id || (canon && !canon.results)) return { state: "processing", note: "Tracking the ball…" };
    return { state: pending, note: "Preparing ball tracking…" };
  },
  wide_line(decision, pending) {
    const wide = decision.wide_analysis || decision.wide || {};
    if (wide.is_wide === true) return { state: "passed", word: "WIDE", note: `WIDE — outside the guideline${fmtCm(wide.distance_cm) ? ` by ${fmtCm(wide.distance_cm)}` : ""}` };
    if (wide.is_wide === false) return { state: "passed", word: "NOT WIDE", note: "Inside the guideline — not a wide" };
    if (wide.reason) return { state: "skipped", word: "NOT MEASURED", note: operatorReason(wide.reason) };
    return { state: pending, note: "Measuring the wide line…" };
  },
  crease(decision, pending) {
    const a = decision.run_out_analysis || decision.stumping_analysis || {};
    if (a.is_out === true || a.is_out === false || a.distance_cm != null) {
      const cm = fmtCm(a.distance_cm);
      const where = Number(a.distance_cm) < 0 ? "short of the crease" : "behind the crease";
      return { state: "passed", word: cm ? `${cm} ${Number(a.distance_cm) < 0 ? "SHORT" : "BEHIND"}`.toUpperCase() : "FRAME FOUND", note: cm ? `Bat ${cm} ${where} at the decision frame` : "Decision frame found — step around it" };
    }
    if (a.reason) return { state: "skipped", word: "NOT MEASURED", note: operatorReason(a.reason) };
    return { state: pending, note: "Finding the decision frame…" };
  },
  timing(decision, pending) {
    const a = decision.stumping_analysis || {};
    // A decision frame is NOT a bail reading. Only an actual bails_status may be
    // reported as a completed check; a frame number alone means "here is where to
    // look", which is a skipped check with a useful pointer.
    if (a.bails_status) {
      return { state: "passed", word: String(a.bails_status).replace(/_/g, " ").toUpperCase(), note: `Bails: ${String(a.bails_status).replace(/_/g, " ")}` };
    }
    if (a.frame_number != null) {
      return { state: "skipped", word: "NOT OBSERVED", note: `Bails not observed — judge the moment around frame #${a.frame_number}` };
    }
    return { state: "skipped", word: "NOT OBSERVED", note: "Bails not observed — step to the moment manually" };
  },
  decision(decision, pending, type) {
    const status = decision.status || "";
    if (status === "OUT" || status === "NOT_OUT") {
      return { state: "passed", note: `Decision confirmed: ${decision.outcome || displayStatus(status)}` };
    }
    const rr = decision.review_result || {};
    // Only a REAL verdict word counts — in-flight status vocabulary never leaks.
    if (rr.verdict && !NON_VERDICTS.test(rr.verdict)) {
      if (rr.verdict === "INCONCLUSIVE") return { state: "processing", note: "No system recommendation — your call from the evidence" };
      // Assisted types (Edge / Run Out / Stumping): the system reading is
      // ADVISORY — the umpire decides from the evidence tools.
      if (isAssisted(type)) return { state: "processing", note: `Evidence ready (system reading: ${rr.verdict}, advisory) — your call` };
      return { state: "processing", note: `System reads ${rr.verdict} — confirm the decision` };
    }
    return { state: pending, note: pending === "processing" ? "Weighing the evidence…" : "" };
  },
  analysis(decision, pending) {
    const rr = decision.review_result || {};
    if (rr.verdict && rr.verdict !== "AWAITING") return { state: "passed", note: "Analysis complete" };
    return { state: pending, note: "Analysing…" };
  },
};

// The one state machine for the active review: the type's protocol stages, each
// with a state + operator note, plus the CURRENT stage (what's happening now).
function computeFlow(type, decision) {
  decision = decision || {};
  const proto = (REVIEW_MODULES[type] && REVIEW_MODULES[type].protocol) ||
    [{ key: "analysis", label: "Analysis" }, { key: "decision", label: "Decision" }];
  const status = decision.status || state.lastStatus || "WAITING";
  // The review is "live" whenever the workspace is open on a live review — during
  // the appeal round-trip the poll can still report the stale pre-appeal WAITING
  // decision; without this, stages would read "waiting" while actually running.
  const reviewLive = typeof ReviewMode !== "undefined" && ReviewMode.active && !ReviewMode.restored;
  const live = reviewLive || status === "PROCESSING" || status === "OUT" || status === "NOT_OUT";
  const pending = live ? "processing" : "waiting";
  const stages = proto.map(({ key, label }) => {
    const evaluate = STAGE_EVAL[key] || STAGE_EVAL.analysis;
    const result = evaluate(decision, pending, type) || {};
    return { key, label, state: result.state || "waiting", note: result.note || "", word: result.word || "" };
  });
  // TV-umpiring short-circuit: a front-foot NO BALL ends the review at that
  // stage — everything after it (except the decision itself) never runs.
  const ffIndex = stages.findIndex((s) => s.key === "front_foot");
  const noBall = (decision.no_ball_analysis || {}).is_no_ball === true || decision.review_ended === "no_ball";
  if (ffIndex >= 0 && noBall) {
    for (let i = ffIndex + 1; i < stages.length; i += 1) {
      if (stages[i].key !== "decision") {
        stages[i].state = "skipped";
        stages[i].note = "Not needed — the NO BALL ends the review";
        stages[i].word = "NOT NEEDED";
      }
    }
  }
  const current = stages.find((s) => s.state === "processing") ||
    stages.find((s) => s.state === "waiting") || stages[stages.length - 1];
  return { stages, current };
}


/* ===================== review queue / history (item 2) ===================== */
function renderQueue() {
  updateDevelopmentGuard();
  const pending = state.queue.filter((q) => q.status === "Processing");
  if (els.queueCount) els.queueCount.textContent = `${pending.length} pending`;
  if (els.kpiQueue) els.kpiQueue.textContent = pending.length ? String(pending.length) : "0";
  if (!els.reviewQueue) return;
  const rows = [...state.queue].slice(-14).reverse();
  els.reviewQueue.innerHTML = rows.length ? rows.map((q) => {
    const verdict = q.verdict || "";
    let statusTxt, cls;
    if (q.status === "Processing") { statusTxt = "Processing"; cls = "processing"; }
    else if (q.status === "Interrupted") { statusTxt = "Interrupted"; cls = "interrupted"; }
    else { statusTxt = verdict || "Completed"; cls = verdict === "OUT" ? "out" : verdict === "NOT OUT" ? "not-out" : "done"; }
    return `<div class="queue-row">
      <span class="q-num">#${q.id}</span>
      <div class="q-main"><span class="q-type">${q.label}</span><span class="q-time">${q.time}</span></div>
      <span class="q-status ${cls}">${statusTxt}</span>
    </div>`;
  }).join("") : `<span class="muted">No reviews yet.</span>`;
}

function resolveQueue(status) {
  const entry = [...state.queue].reverse().find((q) => q.status === "Processing");
  // Record the review type's own confirmed word (WIDE / NO BALL / …) when the
  // decision carries one; the binary status is only the fallback.
  if (entry) { entry.status = "Completed"; entry.verdict = state.decision?.outcome || displayStatus(status); }
  renderQueue();
}

// Resume the CURRENT MATCH on launch: its name + review queue survive a restart
// (rain delay / crash), but the active review never does. The backend scopes these
// reviews to the current match only, so this is a resume — not cross-session history.
async function loadCurrentMatch() {
  try {
    const match = await jsonFetch("/api/match/current");
    state.match = match;
    if (els.matchName) els.matchName.textContent = match.name || "Untitled Match";
    updateSessionChip();
    // Rebuild the queue oldest-first so new reviews still append at the tail.
    state.queue = [];
    state.queueSeq = 0;
    (match.reviews || []).slice().reverse().forEach((review) => {
      const type = String(review.type || review.review_type || "lbw").toLowerCase();
      const interrupted = review.decision === "INTERRUPTED";
      state.queue.push({
        id: ++state.queueSeq,
        type: REVIEW_MODULES[type] ? type : "lbw",
        label: REVIEW_MODULES[type]?.label || "LBW",
        time: review.time ? new Date(review.time).toLocaleTimeString() : "--",
        status: interrupted ? "Interrupted" : "Completed",
        verdict: interrupted ? "" : (review.decision || ""),
      });
    });
    renderQueue();
  } catch {}
}

/* ===================== session identity chip ===================== */
// The topbar avatar is the SESSION, not decoration: label shows the operator once a
// session starts; clicking opens the full session card (teams/venue/model/…).
function updateSessionChip() {
  const m = state.match || {};
  const s = m.session || {};
  const operator = s.operator || "";
  const nameEl = document.getElementById("user-name");
  const roleEl = document.getElementById("user-role");
  const avaEl = document.getElementById("user-ava");
  if (nameEl) nameEl.textContent = operator || "Admin";
  if (roleEl) roleEl.textContent = m.name && m.name !== "Untitled Match" ? m.name : "Administrator";
  if (avaEl) avaEl.textContent = operator ? operator.trim().slice(0, 2).toUpperCase() : "AD";
  const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v || "--"; };
  set("sp-match", m.name && m.name !== "Untitled Match" ? m.name : "No session started");
  set("sp-operator", operator);
  set("sp-started", m.started_at ? new Date(m.started_at).toLocaleString() : "");
  set("sp-venue", s.venue);
  set("sp-ground", s.ground);
  set("sp-tournament", s.tournament);
  set("sp-reviews", String(m.review_count ?? 0));
  set("sp-model", s.active_model);
  set("sp-calibration", s.calibration_profile);
}

/* ===================== new match + session history ===================== */
function openNewMatchDialog() {
  const m = state.match || {};
  if (els.nmCurrentName) els.nmCurrentName.textContent = m.name || "Untitled Match";
  if (els.nmCurrentCount) els.nmCurrentCount.textContent = String(m.review_count ?? (m.reviews?.length ?? 0));
  if (els.nmTeam1) els.nmTeam1.value = "";
  if (els.nmTeam2) els.nmTeam2.value = "";
  // Carry the operator forward (same person usually runs consecutive sessions);
  // clear the venue-specific fields so they are re-entered per ground.
  const prevSession = (state.match && state.match.session) || {};
  if (els.nmOperator && !els.nmOperator.value) els.nmOperator.value = prevSession.operator || "";
  els.newMatchDialog?.showModal();
}

async function confirmNewMatch() {
  // Match name composes from the two teams ("India vs Australia"); one team alone
  // names the session after it; none → backend default "Untitled Match".
  const team1 = els.nmTeam1?.value.trim() || "";
  const team2 = els.nmTeam2?.value.trim() || "";
  const name = [team1, team2].filter(Boolean).join(" vs ") || undefined;
  const session = {
    operator: els.nmOperator?.value.trim() || undefined,
    tournament: els.nmTournament?.value.trim() || undefined,
    venue: els.nmVenue?.value.trim() || undefined,
    ground: els.nmGround?.value.trim() || undefined,
  };
  const payload = {
    ...(name ? { name } : {}),
    ...(team1 || team2 ? { teams: { team1, team2 } } : {}),
    session,
  };
  try {
    await jsonFetch("/api/match/new", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch { return; }
  els.newMatchDialog?.close();
  // The backend archived the old match, cleared the queue and returned to IDLE.
  finishActiveReview();
  await loadCurrentMatch();               // refresh header + now-empty queue
  renderDecision({ status: "WAITING" });  // clean idle dashboard
  showToast("New match started", "not-out");
}

async function openSessionHistory() {
  els.historyDialog?.showModal();
  if (els.historyDetail) els.historyDetail.innerHTML = `<p class="muted">Select a match to view its reviews (read-only).</p>`;
  try {
    const { matches } = await jsonFetch("/api/matches");
    if (!els.historyList) return;
    els.historyList.innerHTML = (matches || []).length
      ? matches.map((m) => `<li><button type="button" class="history-item" data-match-id="${m.id}">
          <strong>${m.name || "Untitled Match"}</strong>
          <small>${m.archived_at ? new Date(m.archived_at).toLocaleDateString() : "--"} · ${m.review_count} reviews</small>
        </button></li>`).join("")
      : `<li class="muted">No archived matches yet.</li>`;
    els.historyList.querySelectorAll(".history-item").forEach((btn) => {
      btn.addEventListener("click", () => showArchivedMatch(btn.dataset.matchId));
    });
  } catch {
    els.historyList.innerHTML = `<li class="muted">History unavailable.</li>`;
  }
}

async function showArchivedMatch(matchId) {
  if (!els.historyDetail) return;
  try {
    const match = await jsonFetch(`/api/matches/${matchId}`);
    const reviews = match.reviews || [];
    const rows = reviews.map((r) => {
      const type = String(r.type || "lbw").toLowerCase();
      const cls = r.decision === "OUT" ? "out" : r.decision === "NOT OUT" ? "not-out" : r.decision === "INTERRUPTED" ? "interrupted" : "done";
      return `<div class="queue-row">
        <span class="q-num">${r.id || "--"}</span>
        <div class="q-main"><span class="q-type">${REVIEW_MODULES[type]?.label || "LBW"}</span>
          <span class="q-time">${r.time ? new Date(r.time).toLocaleTimeString() : "--"}</span></div>
        <span class="q-status ${cls}">${r.decision || "--"}</span>
      </div>`;
    }).join("");
    els.historyDetail.innerHTML = `
      <header class="hd-head"><strong>${match.name || "Untitled Match"}</strong>
        <small>${reviews.length} reviews · read-only</small></header>
      <div class="hd-rows">${rows || `<p class="muted">No reviews in this match.</p>`}</div>`;
  } catch {
    els.historyDetail.innerHTML = `<p class="muted">Could not load match.</p>`;
  }
}

/* ===================== system status summary (item 9) ===================== */
function renderSystemStatus() {
  const alerts = [];
  const health = state.lastHealth || {};
  // Backend-connection honesty (never silently show a synthetic feed): if the app
  // attached to a dev/synthetic/external backend, say so prominently and persistently.
  const eng = state.engineInfo || {};
  if (eng.synthetic) alerts.push("Developer Mode — synthetic backend, not your cameras");
  else if (eng.external) alerts.push("Connected to an external backend (not started by this app)");
  if (eng.status === "offline" || eng.status === "failed") alerts.push("No backend running");
  state.cameras.filter((camera) => !camera.connected).forEach((camera) => alerts.push(`Camera ${camera.id} offline`));
  const gpu = health.gpu || {};
  if (gpu.available && Number(gpu.percent) >= 92) alerts.push("GPU overload");
  if (gpu.temperature_c != null && Number(gpu.temperature_c) >= 85) alerts.push("GPU temperature high");
  if (health.storage?.free_gb != null && Number(health.storage.free_gb) < 20) alerts.push("Storage low");
  if (health.latency_ms != null && Number(health.latency_ms) > 150) alerts.push("High latency");
  const ok = alerts.length === 0;
  if (els.systemStatus) {
    els.systemStatus.className = `system-status ${ok ? "ok" : "warn"}`;
    els.systemStatus.textContent = ok ? "System Healthy" : `Warning · ${alerts.length}`;
  }
  if (els.systemAlerts) {
    els.systemAlerts.hidden = ok;
    els.systemAlerts.innerHTML = alerts.slice(0, 6).map((alert) => `<span class="alert-chip">${alert}</span>`).join("");
  }
}

// Add a ⛶ fullscreen toggle to a media stage (camera feed / replay / video). One
// helper, applied everywhere a video lives, so the operator can always inspect the ball.
function addFullscreenButton(stage, target) {
  if (!stage || stage.querySelector(":scope > .fs-btn")) return;
  stage.classList.add("fs-target");
  if (getComputedStyle(stage).position === "static") stage.style.position = "relative";
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "fs-btn";
  btn.title = "Toggle fullscreen";
  btn.setAttribute("aria-label", "Toggle fullscreen");
  btn.textContent = "⛶";
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    const t = target || stage;
    if (document.fullscreenElement) document.exitFullscreen();
    else t.requestFullscreen?.();
  });
  stage.appendChild(btn);
}

function initDashboardModules() {
  state.statusPanel = new StatusPanel(els.leftPanelTitle, els.cameraCount, els.cameraGrid);
  state.calibrationModal = new CalibrationTabs(els.calibrationRoot, {
    onRoleChange: (cameraId, role) => setCameraRole(cameraId, role),
    getRole: (cameraId) => cameraRoleFor(cameraId),
  });
  // The dashboard's review widgets (EvidencePanel, BroadcastReview, the SVG
  // animation sequencer, the 3D scene, the per-type panels) are GONE — the
  // review workspace is the one place evidence is shown.
  // Fullscreen on every camera/replay stage (control-room feed + Replay Workspace).
  document.querySelectorAll(".live-stage").forEach((stage) => addFullscreenButton(stage));
}


// Engineer-only Testing page: a self-contained upload → analyze → results wizard
// rendered into its own view (not the cameras grid). Instantiated once, on first visit.
function ensureTestingPanel() {
  if (!els.testingRoot || state.testingPanel) return;
  state.testingPanel = new TestingPanel(els.testingRoot);
  state.testingPanel.render();
}

function ensureValidationPanel() {
  if (!els.validationRoot || state.validationPanel) return;
  state.validationPanel = new ValidationPanel(els.validationRoot);
  state.validationPanel.render();
}

function ensureModelManagerPanel() {
  if (!els.modelsRoot || state.modelManagerPanel) return;
  state.modelManagerPanel = new ModelManagerPanel(els.modelsRoot);
  state.modelManagerPanel.render();
}

async function refreshAiDevelopmentStatus() {
  if (!window.drs?.getAiDevelopmentStatus) return;
  try {
    const status = await window.drs.getAiDevelopmentStatus();
    const studio = status.vision_studio || {};
    const runningLabel = studio.running ? "Running" : (studio.status || "Ready");
    els.visionStudioStatus.textContent = runningLabel;
    els.visionStudioReady.textContent = runningLabel;
    els.visionStudioProject.textContent = studio.project || studio.project_path || "--";
    els.visionStudioWorkspace.textContent = studio.workspace || "--";
    els.visionStudioDataset.textContent = studio.dataset || "--";
    els.visionStudioModel.textContent = studio.model_name || studio.model || "--";
    els.visionStudioGpu.textContent = studio.gpu || "--";
    els.visionStudioCuda.textContent = studio.cuda || "--";
    els.visionStudioVersion.textContent = studio.version || "--";
    if (els.visionStudioRecent) {
      const projects = studio.recent_projects?.length ? studio.recent_projects : [studio.workspace].filter(Boolean);
      els.visionStudioRecent.innerHTML = projects.map((project) => `<option value="${project}">${project}</option>`).join("");
      if (studio.workspace) els.visionStudioRecent.value = studio.workspace;
    }
    updateDevelopmentGuard();
    els.aiDevelopmentOutput.textContent = [
      `Status: ${runningLabel}`,
      `Workspace: ${studio.workspace || "--"}`,
      `Dataset: ${studio.dataset || "--"}`,
      `Model: ${studio.model || "--"}`,
      `GPU: ${studio.gpu || "--"} | CUDA: ${studio.cuda || "--"}`,
      `Version: ${studio.version || "--"}`,
      `Dataset root: ${status.dataset?.root || "--"}`,
    ].join("\n");
  } catch (error) {
    els.visionStudioStatus.textContent = "Unavailable";
    els.visionStudioReady.textContent = "Unavailable";
    els.aiDevelopmentOutput.textContent = error.message;
  }
}

function isActiveMatch() {
  return Boolean(state.activeAppeal || state.queue.some((item) => item.status === "Processing"));
}

function updateDevelopmentGuard() {
  const guarded = isActiveMatch();
  if (els.developmentLock) els.developmentLock.hidden = !guarded;
}

function confirmDevelopmentLaunch() {
  if (!isActiveMatch()) return true;
  return window.confirm("Opening Vision Studio may affect system performance during an active match. Continue?");
}

async function showAiDevelopment() {
  setView("development");
  await refreshAiDevelopmentStatus();
}

async function runAiDevelopmentCommand(name) {
  if (!window.drs?.runAiDevelopmentCommand) return;
  els.aiDevelopmentOutput.textContent = `Running ${name}...`;
  const result = await window.drs.runAiDevelopmentCommand(name);
  els.aiDevelopmentOutput.textContent = result.output || `${name}: ${result.ok ? "complete" : "failed"}`;
  await refreshAiDevelopmentStatus();
}

async function importDataset(activate) {
  if (!window.drs?.importDataset) return;
  els.aiDevelopmentOutput.textContent = "Select a YOLO dataset export (.zip)...";
  const result = await window.drs.importDataset({ activate });
  if (result.canceled) { els.aiDevelopmentOutput.textContent = "Import canceled."; return; }
  if (!result.ok) {
    els.aiDevelopmentOutput.textContent = `Import failed: ${result.message || "validation failed"}`;
    await refreshAiDevelopmentStatus();
    return;
  }
  const splits = result.splits || {};
  els.aiDevelopmentOutput.textContent = [
    `Imported ${result.version}`,
    `Active: ${result.activated ? "yes" : "no"}`,
    `Validation score: ${result.validation_score}/100`,
    `Images: ${result.image_count} (train ${splits.train ?? 0} / val ${splits.val ?? 0} / test ${splits.test ?? 0})`,
    `Labels: ${result.label_count} | annotations: ${result.annotation_count}`,
    `Next: ${result.next_command}`,
  ].join("\n");
  await refreshAiDevelopmentStatus();
}


async function requestReview() {
  state.confirmHold = false;
  clearTimeout(state.confirmHoldTimer);
  // Clear the PREVIOUS review's canonical result up-front so the new review's
  // Ball-Tracking stage starts at "processing", not the last review's pass/fail.
  state.canonical = null;
  setMatchStatus("review");
  const mod = REVIEW_MODULES[state.reviewType];
  state.reviewStartMs = Date.now();
  state.reviewElapsed = null;
  state.queue.push({ id: ++state.queueSeq, type: state.reviewType, label: mod.label, time: new Date().toLocaleTimeString(), status: "Processing" });
  renderQueue();
  // Enter the review workspace IMMEDIATELY — the protocol engine has already
  // decided this type's stage rail, evidence surface and decision vocabulary
  // from its contract, so the operator sees THEIR review from the first frame.
  els.requestReview.disabled = true;
  ReviewMode.enter({ review_type: state.reviewType, status: "PROCESSING", review_result: { verdict: "REVIEWING" } });
  const cameraIds = state.cameras.filter((camera) => camera.connected).map((camera) => camera.id);
  const cameraRoles = {};
  state.cameras.forEach((camera) => { cameraRoles[camera.id] = cameraRoleFor(camera.id); });
  const payload = {
    camera_ids: cameraIds,
    review_type: state.reviewType,
    camera_roles: cameraRoles,
    primary_camera_id: getPrimaryCameraId(),
  };
  const gen = ++state.reviewGen;
  try {
    const response = window.drs?.requestReview
      ? await window.drs.requestReview(payload)
      : await jsonFetch("/api/appeal/request", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
    // The operator may have confirmed or cancelled while the appeal was in
    // flight — a stale response must not resurrect the review workspace.
    if (gen !== state.reviewGen) return;
    const decision = response.decision || response;
    renderDecision(decision);
    ReviewMode.play(decision);   // real overlay + verdict → replay the animation
    watchCanonicalReview(decision);   // canonical pipeline replays (same jobs as Testing)
  } catch (err) {
    if (gen === state.reviewGen) ReviewMode.exit();
  } finally {
    els.requestReview.disabled = false;
  }
}

async function confirmDecision(outcome) {
  try {
    await jsonFetch("/api/decision/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ outcome }),
    });
  } catch { return; }
  // Record the confirmed verdict against the current-match queue (the operator may
  // confirm a different call than the AI verdict), then transition RESULT → IDLE.
  const entry = [...state.queue].reverse().find((q) => q.status === "Processing") || [...state.queue].reverse()[0];
  if (entry) { entry.status = "Completed"; entry.verdict = displayStatus(outcome); }
  renderQueue();
  showToast(`Confirmed: ${displayStatus(outcome)}`, statusClass(outcome));
  // RESULT → IDLE: clear the active review and reset the backend to WAITING so
  // nothing from the verdict lingers (and the 5s poll can't snap it back)...
  finishActiveReview();
  try { await jsonFetch("/api/decision/reset", { method: "POST" }); } catch {}
  state.confirmHold = true;
  renderDecision({ status: "WAITING" });   // clears the RESULT panels immediately
  setMatchStatus("confirmed");             // ...but hold a brief CONFIRMED on the match badge
  clearTimeout(state.confirmHoldTimer);
  state.confirmHoldTimer = setTimeout(() => {
    state.confirmHold = false;
    setMatchStatus("waiting");
  }, 1500);
}

// Abandon the active review WITHOUT recording a verdict (the "New Review" action),
// resetting the backend too so the 5s poll doesn't snap the UI back to RESULT.
async function resetReview() {
  state.confirmHold = false;
  clearTimeout(state.confirmHoldTimer);
  finishActiveReview();
  try {
    renderDecision(await jsonFetch("/api/decision/reset", { method: "POST" }));
  } catch {
    renderDecision({ status: "WAITING" });
  }
}

// Tear down all Active-Review state so the dashboard returns to a clean IDLE.
function finishActiveReview() {
  state.reviewGen += 1;     // invalidate any appeal response still in flight
  state.reviewStartMs = null;
  state.reviewElapsed = null;
  updateDevelopmentGuard();
  if (ReviewMode.active) ReviewMode.exit();
}

/* ==================== PER-TYPE EVIDENCE SURFACES ==================== */
// Each surface renders ONE review type's evidence into the workspace middle
// zone. A surface only ever reads its own type's analysis block — the Wide
// surface doesn't know trajectory exists, the Edge surface never shows one.

function rmsRow(label, value, flagged) {
  return `<div class="rms-row${flagged ? " flagged" : ""}"><span>${label}</span><strong>${value ?? "--"}</strong></div>`;
}

// Waveform painter shared by the Edge surface (and reusable elsewhere): real
// envelope buckets revealed to the cursor, spike buckets cyan, red markers at
// event frames, white cursor line.
function drawSurfaceWave(canvas, buckets, events, cursor, total) {
  if (!canvas || !total) return;
  const ctx2 = canvas.getContext("2d");
  const W = canvas.width, H = canvas.height, PADX = 16;
  const plotW = W - PADX * 2, mid = H / 2, amp = H * 0.4;
  ctx2.clearRect(0, 0, W, H);
  ctx2.strokeStyle = "rgba(140,175,195,0.3)";
  ctx2.lineWidth = 1;
  ctx2.beginPath(); ctx2.moveTo(PADX, mid); ctx2.lineTo(W - PADX, mid); ctx2.stroke();
  const nb = buckets.length;
  if (nb) {
    const revealed = Math.floor(((cursor + 1) / total) * nb);
    const bw = Math.max(1, (plotW / nb) * 0.6);
    for (let b = 0; b < revealed && b < nb; b++) {
      const frame = Math.floor((b / nb) * total);
      const spike = events.some((f) => Math.abs(f - frame) <= 1);
      const x = PADX + (plotW * (b + 0.5)) / nb;
      ctx2.strokeStyle = spike ? "#7df0ff" : "rgba(238,248,252,0.9)";
      ctx2.lineWidth = bw;
      ctx2.beginPath();
      ctx2.moveTo(x, mid - buckets[b][1] * amp - 0.5);
      ctx2.lineTo(x, mid - buckets[b][0] * amp + 0.5);
      ctx2.stroke();
    }
  }
  ctx2.fillStyle = "#ff5252";
  events.forEach((f) => {
    const x = PADX + (plotW * (f + 0.5)) / total;
    ctx2.beginPath();
    ctx2.moveTo(x, H - 14); ctx2.lineTo(x - 5, H - 5); ctx2.lineTo(x + 5, H - 5);
    ctx2.closePath(); ctx2.fill();
  });
  const cxp = PADX + plotW * Math.min(1, (cursor + 1) / total);
  ctx2.strokeStyle = "#ffffff";
  ctx2.lineWidth = 2;
  ctx2.beginPath(); ctx2.moveTo(cxp, 3); ctx2.lineTo(cxp, H - 3); ctx2.stroke();
}

// Frame-stepping surface over the review's FROZEN replay buffer (read-only:
// frames are fetched by index, the backend replay cursor is never touched).
// Powers Wide, Edge (waveform + audio), No Ball, Run Out and Stumping.
// Tools: frame stepping (also ←/→ keys, Shift = ×10), slow motion, scrub,
// 1×/2×/4× zoom (click the frame to centre it), brightness/contrast for
// difficult footage, jump-to-decision-frame, waveform click-to-seek.
function createStepSurface(cfg) {
  // cfg: { waveform?, audio?, role?, readouts(decision)→html, jumpFrame(decision)→index|null, jumpLabel }
  return {
    host: null, armed: false, arming: false, total: 0, cursor: 0, cam: null,
    playing: false, speed: 0.5, timer: null, acc: 0, retry: null,
    window: null, wave: { buckets: [], available: false }, events: [],
    zoom: 1, zoomOrigin: "50% 50%", audioEl: null, audioUrl: null,
    mount(host, restored) {
      this.host = host;
      host.dataset.surface = "panel";
      host.innerHTML = `
        <div class="rms-stagewrap">
          <div class="rms-stage"><img alt="Replay frame" draggable="false" /><canvas class="rms-fo" aria-hidden="true"></canvas>
            <div class="rms-empty">${restored ? "Replay frames from this review are no longer stored — the recorded evidence is on the right." : "Capturing the replay…"}</div>
          </div>
          ${cfg.waveform ? `<canvas class="rms-wave" width="900" height="84" hidden title="Click to seek"></canvas>` : ""}
          <div class="rms-controls">
            <button type="button" data-act="start" title="First frame">⏮</button>
            <button type="button" data-act="back" title="Previous frame (←)">◀</button>
            <input type="range" min="0" max="0" value="0" />
            <button type="button" data-act="fwd" title="Next frame (→)">▶</button>
            <button type="button" data-act="play" title="Play slow motion (Space)">▶︎ Play</button>
            <button type="button" data-act="s25">0.25×</button>
            <button type="button" data-act="s50" class="active">0.5×</button>
            <button type="button" data-act="s100">1×</button>
            <button type="button" data-act="zoom" title="Zoom — click the frame to centre (Z)">🔍 1×</button>
            <button type="button" data-act="jump" hidden>${cfg.jumpLabel || "Decision frame"}</button>
            ${cfg.audio ? `<button type="button" data-act="audio" title="Play the window's captured audio">🔊 Audio</button>` : ""}
            <span class="rms-frame-label">—</span>
          </div>
          <div class="rms-controls rms-adjust">
            <span class="rms-adjust-lbl" title="Brightness">☀</span><input type="range" data-adj="b" min="50" max="170" value="100" />
            <span class="rms-adjust-lbl" title="Contrast">◐</span><input type="range" data-adj="c" min="50" max="170" value="100" />
            <button type="button" data-act="reset-adj" title="Reset image adjustments">Reset</button>
          </div>
        </div>
        <aside class="rms-side"></aside>`;
      host.querySelector(".rms-stagewrap").addEventListener("click", (ev) => {
        const act = ev.target?.dataset?.act;
        if (!act) return;
        if (act === "start") this.seek(0);
        else if (act === "back") this.step(-1);
        else if (act === "fwd") this.step(1);
        else if (act === "play") this.togglePlay();
        else if (act === "s25") this.setSpeed(0.25, ev.target);
        else if (act === "s50") this.setSpeed(0.5, ev.target);
        else if (act === "s100") this.setSpeed(1, ev.target);
        else if (act === "zoom") this.cycleZoom();
        else if (act === "jump" && this.jumpTarget != null) this.seek(this.jumpTarget);
        else if (act === "audio") this.playAudio();
        else if (act === "reset-adj") {
          this.host.querySelectorAll("[data-adj]").forEach((r) => { r.value = "100"; });
          this.applyAdjust();
        }
      });
      const scrub = host.querySelector(".rms-controls input[type=range]:not([data-adj])");
      scrub.addEventListener("input", (ev) => this.seek(Number(ev.target.value)));
      host.querySelectorAll("[data-adj]").forEach((r) => r.addEventListener("input", () => this.applyAdjust()));
      // The overlay can only be placed once the frame's natural size is known, and
      // must be re-placed whenever the panel resizes.
      host.querySelector(".rms-stage img").addEventListener("load", () => this.drawOverlay());
      this._ro = new ResizeObserver(() => this.drawOverlay());
      this._ro.observe(host.querySelector(".rms-stage"));
      // Zoom centres on wherever the umpire clicks the frame.
      const img = host.querySelector(".rms-stage img");
      img.addEventListener("click", (ev) => {
        const rect = img.getBoundingClientRect();
        this.zoomOrigin = `${(((ev.clientX - rect.left) / rect.width) * 100).toFixed(1)}% ${(((ev.clientY - rect.top) / rect.height) * 100).toFixed(1)}%`;
        if (this.zoom === 1) this.cycleZoom(); else this.applyZoom();
      });
      // Waveform click = seek straight to that moment.
      const wave = host.querySelector(".rms-wave");
      if (wave) wave.addEventListener("click", (ev) => {
        if (!this.armed || !this.total) return;
        const rect = wave.getBoundingClientRect();
        const x = ((ev.clientX - rect.left) / rect.width) * wave.width;
        const PADX = 16;
        this.seek(Math.round(((x - PADX) / (wave.width - PADX * 2)) * this.total));
      });
      if (!restored) this.arm();
    },
    step(delta) { this.seek(this.cursor + delta); },
    applyZoom() {
      const img = this.host?.querySelector(".rms-stage img");
      if (!img) return;
      img.style.transform = this.zoom > 1 ? `scale(${this.zoom})` : "";
      img.style.transformOrigin = this.zoomOrigin;
      const btn = this.host.querySelector("[data-act=zoom]");
      if (btn) { btn.textContent = `🔍 ${this.zoom}×`; btn.classList.toggle("active", this.zoom > 1); }
    },
    cycleZoom() {
      this.zoom = this.zoom === 1 ? 2 : this.zoom === 2 ? 4 : 1;
      this.applyZoom();
    },
    applyAdjust() {
      const img = this.host?.querySelector(".rms-stage img");
      if (!img) return;
      const b = this.host.querySelector("[data-adj=b]")?.value ?? 100;
      const c = this.host.querySelector("[data-adj=c]")?.value ?? 100;
      img.style.filter = (Number(b) !== 100 || Number(c) !== 100) ? `brightness(${b}%) contrast(${c}%)` : "";
    },
    async playAudio() {
      // The window's REAL captured audio — fetched once, honest note when absent.
      const btn = this.host?.querySelector("[data-act=audio]");
      if (!this.window) return;
      try {
        if (!this.audioUrl) {
          const res = await fetch(`${API_BASE}/api/audio/clip.wav?start_ms=${this.window.start_timestamp_ms}&end_ms=${this.window.end_timestamp_ms}`);
          if (!res.ok) {
            const why = (await res.json().catch(() => ({})))?.detail || "no audio captured";
            if (btn) { btn.disabled = true; btn.title = why; }
            return;
          }
          this.audioUrl = URL.createObjectURL(await res.blob());
          this.audioEl = new Audio(this.audioUrl);
        }
        this.audioEl.currentTime = 0;
        this.audioEl.play();
        if (btn) btn.classList.add("active");
        this.audioEl.onended = () => { if (btn) btn.classList.remove("active"); };
      } catch { if (btn) { btn.disabled = true; btn.title = "audio unavailable"; } }
    },
    async arm() {
      if (this.arming || !this.host) return;
      this.arming = true;
      try {
        const payload = await jsonFetch("/api/replay/state");
        const cams = payload.camera_ids || [];
        if (payload.total_frames && cams.length) {
          this.window = payload;
          this.total = Number(payload.total_frames);
          // The surface's OWN declared role wins (an LBW front-foot stage steps
          // the front-foot camera); otherwise the review type's role decides.
          const want = cfg.role || REVIEW_MODULES[state.reviewType]?.role;
          this.cam = cams.find((c) => cameraRoleFor(Number(c)) === want) ?? (cams.includes(getPrimaryCameraId()) ? getPrimaryCameraId() : cams[0]);
          const scrub = this.host.querySelector("input[type=range]");
          if (scrub) scrub.max = String(Math.max(0, this.total - 1));
          this.host.querySelector(".rms-empty")?.setAttribute("hidden", "");
          this.armed = true;
          this.seek(this.jumpTarget != null ? this.jumpTarget : 0);
          if (cfg.waveform) this.armWave(payload);
        } else if (ReviewMode.active && !ReviewMode.restored) {
          // The buffer freezes moments after the appeal — retry until it lands.
          this.retry = setTimeout(() => { this.arming = false; this.arm(); }, 2000);
          return;
        }
      } catch {
        if (ReviewMode.active && !ReviewMode.restored) {
          this.retry = setTimeout(() => { this.arming = false; this.arm(); }, 3000);
          return;
        }
      }
      this.arming = false;
    },
    async armWave(payload) {
      try {
        const buckets = Math.max(240, Math.min(3600, this.total * 3));
        const wf = await jsonFetch(`/api/audio/waveform?start_ms=${payload.start_timestamp_ms}&end_ms=${payload.end_timestamp_ms}&buckets=${buckets}`);
        if (wf.available) { this.wave.buckets = wf.buckets; this.wave.available = true; }
      } catch {}
      this.drawWave();
    },
    drawWave() {
      const canvas = this.host?.querySelector(".rms-wave");
      if (!canvas) return;
      const show = this.armed && (this.wave.available || this.events.length);
      canvas.hidden = !show;
      if (show) drawSurfaceWave(canvas, this.wave.buckets, this.events, this.cursor, this.total);
    },
    seek(i) {
      if (!this.armed || !this.host) return;
      this.cursor = Math.max(0, Math.min(this.total - 1, Math.round(i)));
      const img = this.host.querySelector(".rms-stage img");
      if (img) img.src = `${API_BASE}/api/replay/${this.cam}.jpg?frame_index=${this.cursor}&t=${Date.now()}`;
      const scrub = this.host.querySelector("input[type=range]");
      if (scrub) scrub.value = String(this.cursor);
      const label = this.host.querySelector(".rms-frame-label");
      if (label) label.textContent = `Frame ${this.cursor + 1} / ${this.total}`;
      this.drawWave();
      this.drawOverlay();
    },
    // Evidence drawn ON the frame. Sized to the displayed image every time, because
    // the payload is in the frame's NATURAL pixel space — drawing in display space
    // would misplace the evidence the moment the panel resized.
    drawOverlay() {
      const canvas = this.host?.querySelector(".rms-fo");
      const img = this.host?.querySelector(".rms-stage img");
      const stage = this.host?.querySelector(".rms-stage");
      if (!canvas || !img || !stage || !img.naturalWidth) return;
      const box = img.getBoundingClientRect();
      const stageBox = stage.getBoundingClientRect();
      if (!box.width || !box.height) return;
      // `object-fit: contain` letterboxes the frame inside the element, so the
      // element box is NOT the picture. Sizing to it would offset every mark by
      // the letterbox — evidence drawn slightly wrong is worse than none.
      const scale = Math.min(box.width / img.naturalWidth, box.height / img.naturalHeight);
      const w = img.naturalWidth * scale;
      const h = img.naturalHeight * scale;
      const left = (box.left - stageBox.left) + (box.width - w) / 2;
      const top = (box.top - stageBox.top) + (box.height - h) / 2;
      if (canvas.width !== Math.round(w) || canvas.height !== Math.round(h)) {
        canvas.width = Math.round(w);
        canvas.height = Math.round(h);
      }
      canvas.style.left = `${left}px`;
      canvas.style.top = `${top}px`;
      canvas.style.width = `${w}px`;
      canvas.style.height = `${h}px`;
      drawFrameOverlay(canvas, state.decision?.overlay, {
        frameSize: { width: img.naturalWidth, height: img.naturalHeight },
        frameIndex: this.cursor,
      });
    },
    setSpeed(speed, btn) {
      this.speed = speed;
      this.host?.querySelectorAll("[data-act^='s']").forEach((b) => b.classList.toggle("active", b === btn));
    },
    togglePlay() {
      this.playing = !this.playing;
      const btn = this.host?.querySelector("[data-act=play]");
      if (btn) { btn.textContent = this.playing ? "⏸ Pause" : "▶︎ Play"; btn.classList.toggle("active", this.playing); }
      if (this.playing && !this.timer) {
        this.acc = 0;
        this.timer = setInterval(() => {
          this.acc += this.speed * 1.5;                    // 30fps buffer, 50ms tick
          const advance = Math.floor(this.acc);
          if (advance >= 1) {
            this.acc -= advance;
            const next = this.cursor + advance;
            if (next >= this.total - 1) { this.seek(this.total - 1); this.togglePlay(); }
            else this.seek(next);
          }
        }, 50);
      }
      if (!this.playing && this.timer) { clearInterval(this.timer); this.timer = null; }
    },
    update(decision) {
      if (!this.host) return;
      const side = this.host.querySelector(".rms-side");
      if (side) side.innerHTML = cfg.readouts(decision || {});
      this.jumpTarget = cfg.jumpFrame ? cfg.jumpFrame(decision || {}) : null;
      const jump = this.host.querySelector("[data-act=jump]");
      if (jump) jump.hidden = this.jumpTarget == null;
      this.events = (decision?.edge_analysis?.events || [])
        .map((e) => Number(e.frame_id ?? e.frame)).filter((f) => Number.isFinite(f));
      this.drawWave();
    },
    unmount() {
      if (this.timer) clearInterval(this.timer);
      if (this.retry) clearTimeout(this.retry);
      if (this._ro) { try { this._ro.disconnect(); } catch {} this._ro = null; }
      this.timer = this.retry = null;
      if (this.audioEl) { try { this.audioEl.pause(); } catch {} this.audioEl = null; }
      if (this.audioUrl) { try { URL.revokeObjectURL(this.audioUrl); } catch {} this.audioUrl = null; }
      this.zoom = 1; this.zoomOrigin = "50% 50%";
      this.playing = false; this.armed = false; this.arming = false; this.host = null;
      this.wave = { buckets: [], available: false }; this.events = []; this.jumpTarget = null;
    },
  };
}

// -- readout builders (operator language, own-type fields only) --------------
function wideReadouts(decision) {
  const wide = decision.wide_analysis || decision.wide || {};
  const has = wide.is_wide === true || wide.is_wide === false;
  const cm = wide.distance_cm == null ? NaN : Number(wide.distance_cm);
  // Ball marker only when MEASURED: outside the guideline for a wide, back
  // toward the stumps otherwise — positional, to scale where possible.
  const offset = Number.isFinite(cm) ? Math.min(96, Math.abs(cm) * 3) : 40;
  const bx = has ? (wide.is_wide ? 190 + Math.max(14, offset) : 190 - Math.max(14, offset)) : null;
  const diagram = `
    <svg class="rms-wide-mini" viewBox="0 0 300 140" preserveAspectRatio="xMidYMid meet">
      <line x1="60" y1="24" x2="60" y2="116" stroke="rgba(230,240,235,.8)" stroke-width="4" stroke-linecap="round"></line>
      <text x="60" y="132" fill="#8ea79c" font-size="11" text-anchor="middle">STUMPS</text>
      <line x1="190" y1="14" x2="190" y2="126" stroke="#38bdf8" stroke-width="2" stroke-dasharray="6 6"></line>
      <text x="190" y="10" fill="#7dd3fc" font-size="11" text-anchor="middle">WIDE LINE</text>
      ${bx != null ? `<line x1="190" y1="70" x2="${bx}" y2="70" stroke="#f5be5a" stroke-width="2"></line>
      <circle cx="${bx}" cy="70" r="9" fill="#f8fafc" stroke="#0b0f14" stroke-width="2"></circle>
      <text x="${(190 + bx) / 2}" y="58" fill="#f5be5a" font-size="12" font-weight="700" text-anchor="middle">${fmtCm(wide.distance_cm) || ""}</text>`
      : `<text x="150" y="74" fill="#9fb3ab" font-size="12" text-anchor="middle">${wide.reason ? "Not measured" : "Measuring…"}</text>`}
    </svg>`;
  const verdictWord = has ? (wide.is_wide ? "WIDE" : "NOT WIDE") : "ANALYSING…";
  return `<div class="rms-verdict ${has ? (wide.is_wide ? "out" : "not-out") : ""}">${verdictWord}</div>` + diagram +
    rmsRow("Distance from line", fmtCm(wide.distance_cm), Boolean(wide.is_wide)) +
    rmsRow("Confidence", pct(wide.confidence)) +
    (wide.reason ? `<div class="rms-note">${operatorReason(wide.reason)}</div>` : "");
}

function edgeReadouts(decision) {
  // ASSISTED review: the system never announces EDGE / NO EDGE as the decision.
  // It provides the evidence (waveform, spikes, slow motion) plus an advisory
  // reading — the umpire makes the call.
  const edge = decision.edge_analysis || {};
  const n = (edge.events || []).length;
  const noMic = edge.available === false || edge.inconclusive;
  const analyzed = !noMic && (edge.edge_probability != null || n > 0);
  const advisory = !analyzed ? null : (n || (edge.edge_probability || 0) >= 0.5) ? "Spike evidence — advisory" : "No spike found — advisory";
  return `<div class="rms-verdict">${analyzed || noMic ? "YOUR CALL" : "ANALYSING…"}</div>` +
    (advisory ? rmsRow("System reading", advisory, advisory.startsWith("Spike")) : "") +
    rmsRow("Edge probability", pct(edge.edge_probability)) +
    rmsRow("Spikes", n ? `${n} marked in red` : analyzed ? "None" : "--") +
    (noMic ? `<div class="rms-note">No stump microphone — judge bat contact from the slow-motion frames.</div>` : "");
}

function noballReadouts(decision) {
  const nb = decision.no_ball_analysis || {};
  const has = nb.is_no_ball === true || nb.is_no_ball === false;
  const verdictWord = has ? (nb.is_no_ball ? "NO BALL" : "LEGAL") : "ANALYSING…";
  return `<div class="rms-verdict ${has ? (nb.is_no_ball ? "out" : "not-out") : ""}">${verdictWord}</div>` +
    rmsRow("Front foot", fmtCm(nb.distance_past_cm) ? `${fmtCm(nb.distance_past_cm)} ${nb.is_no_ball ? "past the line" : "behind the line"}` : "--", Boolean(nb.is_no_ball)) +
    rmsRow("Decision frame", nb.landing_frame_id != null ? `#${nb.landing_frame_id}` : "--") +
    rmsRow("Confidence", pct(nb.confidence)) +
    (nb.reason ? `<div class="rms-note">${operatorReason(nb.reason)}</div>` : "");
}

function creaseReadouts(analysis, extraRows) {
  // ASSISTED review: the crease measurement is an ADVISORY reading; the umpire
  // steps the frames and makes the call.
  const has = analysis.is_out === true || analysis.is_out === false;
  const cm = analysis.distance_cm == null ? NaN : Number(analysis.distance_cm);
  return `<div class="rms-verdict">${has ? "YOUR CALL" : "ANALYSING…"}</div>` +
    (has ? rmsRow("System reading", `${analysis.is_out ? "OUT" : "NOT OUT"} — advisory`, Boolean(analysis.is_out)) : "") +
    rmsRow("Bat to crease", Number.isFinite(cm) ? `${Math.abs(cm).toFixed(1)} cm ${cm < 0 ? "short" : "behind"}` : "--", has && analysis.is_out) +
    rmsRow("Decision frame", analysis.frame_number != null ? `#${analysis.frame_number}` : "--") +
    (extraRows || "") +
    rmsRow("Confidence", pct(analysis.confidence)) +
    (analysis.reason ? `<div class="rms-note">${operatorReason(analysis.reason)}</div>` : "");
}

// Bails have no detector. Three states, and "unknown" is stated in the umpire's
// language rather than as a dash: an empty readout looks broken, and "not detected"
// reads as a model failure. Wording comes from the shared tri-state contract so the
// readout and the frame overlay can never disagree.
const bailsText = (status) => bailsLabel(status);

const RM_SURFACES = {
  // LBW: the two canonical pipeline replays + the wicket gates (bottom zone).
  // Mounting must SELF-ATTACH whatever the canonical job has already produced —
  // the trajectory renders in the background while the operator inspects the
  // Front Foot / UltraEdge stages, so this surface is usually mounted AFTER the
  // results (or the pending state) already exist.
  lbw: {
    mount(host, restored) {
      host.dataset.surface = "lbw";
      host.innerHTML = `
        <div class="rm2-vid"><span class="rm2-vlabel">Observed Trajectory</span><div class="rm2-vbox" id="rm-observed"></div></div>
        <div class="rm2-vid"><span class="rm2-vlabel">Broadcast Replay</span><div class="rm2-vbox" id="rm-broadcast"></div></div>`;
      const c = state.canonical;
      const d = state.decision || {};
      if (c && c.results) ReviewMode.setCanonical(c.jobId, c.results);
      else if (!restored && d.status && d.status !== "WAITING" && !d.canonical_job_id) {
        // The appeal came back without a replay job — never pretend one is coming.
        ["rm-observed", "rm-broadcast"].forEach((id) => { const b = document.getElementById(id); if (b) b.innerHTML = `<div class="rm2-noreplay">Replay clip wasn't captured for this appeal — decide from the live replay.</div>`; });
      } else if (!restored) ReviewMode.renderPending("Rendering replay…");
    },
    update() {}, unmount() {},
  },
  // Wide: a MEASUREMENT tool — the delivery's frames with stepping/zoom, and
  // the wide-line diagram + margin beside them. Nothing else.
  wide: createStepSurface({
    readouts: wideReadouts,
  }),
  // Edge: slow-motion frame stepping + synchronized waveform + real audio.
  edge: createStepSurface({
    waveform: true,
    audio: true,
    readouts: edgeReadouts,
    jumpLabel: "Jump to spike",
    jumpFrame(decision) {
      const events = decision?.edge_analysis?.events || [];
      const best = events.find((e) => e.is_bat) || events[0];
      const f = Number(best?.frame_id ?? best?.frame);
      return Number.isFinite(f) ? f : null;
    },
  }),
  // No Ball: the front-foot decision frame, stepped precisely.
  noball: createStepSurface({
    readouts: noballReadouts,
    jumpLabel: "Landing frame",
    jumpFrame(decision) {
      const f = Number(decision?.no_ball_analysis?.landing_frame_id);
      return Number.isFinite(f) ? f : null;
    },
  }),
  // Run Out / Stumping: crease check by frame stepping (+ bail timing rows).
  // "Not observed" — never a bare "--", which reads as a broken readout, and never
  // a claim. There is no bails detector; the umpire judges it from the replay.
  runout: createStepSurface({
    readouts: (decision) => creaseReadouts(decision.run_out_analysis || {},
      rmsRow("Bails", bailsText((decision.run_out_analysis || {}).bails_status))),
    jumpLabel: "Decision frame",
    jumpFrame(decision) {
      const f = Number(decision?.run_out_analysis?.frame_number);
      return Number.isFinite(f) ? f : null;
    },
  }),
  stumping: createStepSurface({
    readouts: (decision) => {
      const a = decision.stumping_analysis || {};
      const hasData = a.is_out != null || a.distance_cm != null;
      return creaseReadouts(a,
        rmsRow("Bail timing", a.frame_number != null ? `Frame #${a.frame_number}` : "--") +
        rmsRow("Bails", bailsText(a.bails_status)) +
        // "Check manually" is an instruction that only exists once there is a
        // decision frame to check — before that, nothing is claimed.
        // Same tri-state contract: an unobserved check names what to do about it
        // rather than reporting a negative finding.
        (hasData ? rmsRow("Gloves", observationLabel(a.gloves_detected, "Collected cleanly", "Not collected", "Not observed — check by eye")) : ""));
    },
    jumpLabel: "Bail frame",
    jumpFrame(decision) {
      const f = Number(decision?.stumping_analysis?.frame_number);
      return Number.isFinite(f) ? f : null;
    },
  }),
};

// LBW protocol stages each get a REAL evidence surface: the rail is not just
// status — Front Foot and UltraEdge are inspected with the same tools the
// dedicated No Ball / Edge reviews use (the LBW decision carries its own
// no_ball_analysis + edge_analysis). Ball Tracking / Decision show the canonical
// replays (RM_SURFACES.lbw), which keep rendering in the background while the
// operator works through the earlier stages. Fresh instances on purpose — never
// the other types' surface objects, so mounted state can't leak across types.
const LBW_STAGE_SURFACES = {
  front_foot: createStepSurface({
    role: "Front Foot",
    readouts: noballReadouts,
    jumpLabel: "Landing frame",
    jumpFrame(decision) {
      const f = Number(decision?.no_ball_analysis?.landing_frame_id);
      return Number.isFinite(f) ? f : null;
    },
  }),
  ultra_edge: createStepSurface({
    role: "Stump Camera",
    waveform: true,
    audio: true,
    readouts: edgeReadouts,
    jumpLabel: "Jump to spike",
    jumpFrame(decision) {
      const events = decision?.edge_analysis?.events || [];
      const best = events.find((e) => e.is_bat) || events[0];
      const f = Number(best?.frame_id ?? best?.frame);
      return Number.isFinite(f) ? f : null;
    },
  }),
};

// Unknown (future backend-declared) types: honest generic readouts.
const GENERIC_SURFACE = {
  mount(host) {
    host.dataset.surface = "panel";
    host.innerHTML = `<div class="rms-stagewrap"><div class="rms-stage"><div class="rms-empty">This review type has no dedicated evidence surface yet.</div></div></div><aside class="rms-side"></aside>`;
  },
  update(decision) {
    const side = document.querySelector("#rm-mid .rms-side");
    if (!side) return;
    const rr = decision?.review_result || {};
    side.innerHTML = (rr.measurements || []).map((m) => rmsRow(m.label, m.value, m.flag)).join("") || rmsRow("Status", "Analysing…");
  },
  unmount() {},
};

/* ============================ REVIEW MODE ============================ */
// The single review workspace, driven ENTIRELY by the protocol engine: the
// active type's contract decides the stage rail, the evidence surface and the
// decision vocabulary BEFORE the screen opens. One screen, one state machine,
// from Request Review to Decision.
const ReviewMode = {
  el: null, active: false, jobId: null, restored: false, type: "lbw",
  surface: null, actions: null, stage: null,
  ensure() {
    if (this.el) return;
    this.el = document.getElementById("review-mode");
    // Back to Dashboard just CLOSES the workspace — the review keeps running and
    // the dashboard's review strip leads straight back here. (It used to cancel
    // the review, which is why glancing at the cameras destroyed your appeal.)
    document.getElementById("rm-back").addEventListener("click", () => this.exit());
    // The stage rail is NAVIGATION, not just status: clicking a stage inspects
    // that evidence — the same deep link the dashboard checklist rows use.
    document.getElementById("rm-steps").addEventListener("click", (ev) => {
      const step = ev.target.closest(".rm2-step");
      if (step && step.dataset.step) this.focusStage(step.dataset.step);
    });
    // Abandon is the ONLY destructive exit: discard the review, reset the backend
    // to WAITING so no zombie review blocks the next Request Review.
    document.getElementById("rm-abandon").addEventListener("click", () => {
      if (this.restored) this.exit();
      else if (this.active) resetReview();
    });
    document.getElementById("rm-confirm-out").addEventListener("click", () => confirmDecision(this.actions?.positive.send || "OUT"));
    document.getElementById("rm-confirm-not-out").addEventListener("click", () => confirmDecision(this.actions?.negative.send || "NOT_OUT"));
    // Umpire keyboard: ←/→ step one frame (Shift = ×10), Space = play/pause,
    // Z = zoom cycle — active only while a step-surface review is open.
    document.addEventListener("keydown", (ev) => {
      if (!this.active) return;
      const surface = this.surface;
      if (!surface || typeof surface.step !== "function") return;
      const tag = (ev.target?.tagName || "").toLowerCase();
      if (tag === "input" || tag === "textarea" || ev.target?.isContentEditable) return;
      if (ev.key === "ArrowLeft") { surface.step(ev.shiftKey ? -10 : -1); ev.preventDefault(); }
      else if (ev.key === "ArrowRight") { surface.step(ev.shiftKey ? 10 : 1); ev.preventDefault(); }
      else if (ev.key === " ") { surface.togglePlay(); ev.preventDefault(); }
      else if (ev.key === "z" || ev.key === "Z") surface.cycleZoom();
    });
  },
  renderPending(text) {
    if (!this.el) return;
    const html = `<div class="rm2-rendering"><span>${text || "Rendering replay…"}</span><div class="rm2-bar"><i></i></div></div>`;
    ["rm-observed", "rm-broadcast"].forEach((id) => { const b = document.getElementById(id); if (b && !b.querySelector("video")) b.innerHTML = html; });
  },
  renderVideo(boxId, jobId, results, key) {
    const box = document.getElementById(boxId);
    if (!box) return;
    const ex = results.exports || {};
    if (!ex[key]) {
      // Operator copy up front; the pipeline's technical reason stays on hover.
      const t = results.trajectory || {};
      const detail = t.valid === false
        ? `trajectory rejected: ${(t.reasons || []).join("; ") || t.observed?.end_reason || "invalid"}`
        : "replay not available for this delivery";
      box.innerHTML = `<div class="rm2-noreplay" title="${String(detail).replace(/"/g, "&quot;")}">Ball couldn't be tracked for this delivery — use the broadcast replay.</div>`;
      return;
    }
    box.innerHTML = `<video muted playsinline controls autoplay loop src="${API_BASE}/api/testing/jobs/${jobId}/exports/${key}"></video>`;
  },
  // Render the stage rail + operator line from the ONE state machine. The
  // focused stage (whose evidence surface is mounted) is marked on the rail.
  renderFlow(decision) {
    if (!this.active || !this.el) return;
    const flow = computeFlow(this.type, decision || state.decision || {});
    flow.stages.forEach((s) => {
      const el = this.el.querySelector(`#rm-steps .rm2-step[data-step="${s.key}"]`);
      if (el) { el.className = `rm2-step ${s.state}${s.key === this.stage ? " focus" : ""}`; el.title = s.note || s.label; }
    });
    const oper = document.getElementById("rm-oper");
    if (oper) oper.textContent = (flow.current && flow.current.note) || "";
  },
  // Kept for canonical-surface callers (syncCanonicalSurfaces).
  renderProtocol(decision) { this.renderFlow(decision); },
  // Called when the canonical job's results land (from watchCanonicalReview).
  setCanonical(jobId, results) {
    if (!this.active || !results) return;
    this.renderVideo("rm-observed", jobId, results, "replay_players");
    this.renderVideo("rm-broadcast", jobId, results, "replay_review");
    const g = results.reconstruction && results.reconstruction.gates;
    const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v || "—"; };
    set("rm-gate-pitching", g && g.pitching);
    set("rm-gate-impact", g && g.impact);
    set("rm-gate-wickets", g && g.wickets);
    this.renderFlow();   // trajectory stage → passed/skipped per the state machine
  },
  // The surface for (type, focused stage): LBW stages get their own evidence
  // surfaces; every other case falls back to the type's single surface.
  surfaceFor(type, stageKey) {
    return (type === "lbw" && stageKey && LBW_STAGE_SURFACES[stageKey]) || RM_SURFACES[type] || GENERIC_SURFACE;
  },
  mountSurface(type) {
    const host = document.getElementById("rm-mid");
    if (!host) return;
    if (this.surface && this.surface.unmount) this.surface.unmount();
    this.surface = this.surfaceFor(type, this.stage);
    this.surface.mount(host, this.restored);
  },
  // Deep link from the rail or the dashboard checklist: focus a stage and mount
  // its evidence surface. Same-surface stages (e.g. Ball Tracking → Decision on
  // LBW, or any stage on a single-surface type) never remount — a remount would
  // throw away the operator's cursor position mid-inspection.
  focusStage(stageKey) {
    if (!this.active || !stageKey) return;
    this.stage = stageKey;
    const next = this.surfaceFor(this.type, stageKey);
    if (next !== this.surface) {
      this.mountSurface(this.type);
      if (this.surface && this.surface.update) this.surface.update(state.decision || {});
    }
    this.renderFlow();
  },
  // Shared open: the protocol engine decides EVERYTHING the screen shows —
  // stage rail, evidence surface, gates, decision vocabulary — from the type.
  _open(decision) {
    this.ensure();
    this.active = true;
    this.jobId = null;
    document.body.classList.add("review-active");
    this.el.classList.add("open");
    this.el.setAttribute("aria-hidden", "false");
    const type = decision.review_type || state.reviewType || "lbw";
    this.type = type;
    const mod = REVIEW_MODULES[type];
    document.getElementById("rm-type").textContent = `${(mod && mod.label) || "Review"} REVIEW`.toUpperCase();
    // Stage rail = the type's OWN protocol from its contract.
    const proto = (mod && mod.protocol) || [{ key: "analysis", label: "Analysis" }, { key: "decision", label: "Decision" }];
    const steps = this.el.querySelector("#rm-steps");
    if (steps) {
      steps.hidden = false;
      steps.innerHTML = proto.map((s, i) =>
        `<div class="rm2-step waiting" data-step="${s.key}"><span class="rm2-num">${i + 1}</span><span class="rm2-lbl">${s.label}</span></div>`).join("");
    }
    const oper = document.getElementById("rm-oper");
    if (oper) oper.textContent = "";
    // Wicket gates belong to LBW's trajectory stage only.
    const gates = this.el.querySelector(".rm2-gates");
    if (gates) gates.hidden = type !== "lbw";
    ["rm-gate-pitching", "rm-gate-impact", "rm-gate-wickets"].forEach((id) => { const el = document.getElementById(id); if (el) el.textContent = "—"; });
    // Decision actions in the type's own vocabulary (WIDE / NO BALL / OUT …).
    this.actions = DECISION_ACTIONS[type] || DECISION_ACTIONS.default;
    const bOut = document.getElementById("rm-confirm-out");
    const bNot = document.getElementById("rm-confirm-not-out");
    if (bOut) bOut.textContent = this.actions.positive.label;
    if (bNot) bNot.textContent = this.actions.negative.label;
    // A restored review is already decided and stored: there is nothing to
    // abandon and nothing new to export.
    const bAbandon = document.getElementById("rm-abandon");
    if (bAbandon) bAbandon.hidden = this.restored;
    const bExport = document.getElementById("rm-export");
    if (bExport) bExport.hidden = this.restored;
    // Open ON the protocol's current stage — the workspace resumes where the
    // protocol actually is (a fresh LBW appeal starts at Front Foot; a decided
    // review lands on Decision). A checklist deep link refocuses right after.
    const flow = computeFlow(type, decision || {});
    this.stage = (flow.current && flow.current.key) || null;
    this.mountSurface(type);
  },
  enter(decision) {
    this.restored = false;
    this._open(decision);
    this.renderPending("Rendering replay…");
    this.update(decision);
  },
  // Reopen a PAST review from History — read-only: recorded verdict, no confirm,
  // Back just closes. Videos/gates re-load from its canonical job (openReviewFromHistory).
  enterRestored(decision) {
    this.restored = true;
    this._open(decision);
    this.renderPending("Loading replay…");
    this.update(decision);
  },
  // Kept for call-site compatibility (requestReview): real decision arrived.
  play(decision) { if (!this.active) return this.enter(decision); this.update(decision); },
  update(decision) {
    if (!this.active) return;
    this.renderFlow(decision);
    if (this.surface && this.surface.update) this.surface.update(decision);
    const status = decision.status || "PROCESSING";
    const rr = decision.review_result || {};
    const resolved = status === "OUT" || status === "NOT_OUT";
    const v = document.getElementById("rm-verdict");
    // Verdict box in operator language: the type's own word, never pipeline
    // jargon. Assisted types (Edge / Run Out / Stumping) NEVER present a system
    // reading as the decision — the box says YOUR CALL and the reading stays
    // advisory in the evidence readouts. INCONCLUSIVE is likewise the umpire's.
    const word = resolved ? (decision.outcome || displayStatus(status))
      : rr.verdict === "INCONCLUSIVE" ? "YOUR CALL"
      : (rr.verdict && !NON_VERDICTS.test(rr.verdict)) ? (isAssisted(this.type) ? "YOUR CALL" : rr.verdict)
      : (this.restored ? "—" : "REVIEWING");
    const cls = /^(not |no edge|legal|missing)/i.test(word) ? "not-out"
      : /out|wide|no ball|edge|hitting|stumped/i.test(word) ? "out"
      : this.restored ? "" : "reviewing";
    v.className = "rm-verdict " + cls;
    v.textContent = word;
    // Restored reviews are read-only — never offer to confirm an already-decided review.
    const hideConfirm = this.restored || resolved;
    document.getElementById("rm-confirm-out").hidden = hideConfirm;
    document.getElementById("rm-confirm-not-out").hidden = hideConfirm;
  },
  exit() {
    const wasRestored = this.restored;
    this.active = false;
    this.restored = false;
    this.jobId = null;
    this.stage = null;
    if (this.surface && this.surface.unmount) this.surface.unmount();
    this.surface = null;
    document.body.classList.remove("review-active");
    if (this.el) {
      this.el.classList.remove("open");
      this.el.setAttribute("aria-hidden", "true");
      // Release any playing <video> so decoders/timers don't linger after close.
      this.el.querySelectorAll("video").forEach((v) => { try { v.pause(); v.removeAttribute("src"); v.load(); } catch {} });
    }
    // Closing a RESTORED review returns to a clean dashboard; closing a live one
    // must leave the strip up immediately (the way back in), not wait for the poll.
    if (wasRestored) { state.decision = null; renderDecisionState("WAITING"); }
    else renderDecisionState(state.lastStatus);
  },
};
window.__reviewMode = ReviewMode;


// Open the Replay workspace on the frozen buffer and start playback. Shared by
// every "Replay" button (Review State panel + LBW analysis card). Gives honest
// feedback when there is nothing buffered instead of a silent blank stage.
async function replayControl(action, extra = {}) {
  const payload = await jsonFetch("/api/replay/control", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, ...extra }),
  });
  renderReplayState(payload);
}

function renderReplayState(payload) {
  if (!payload) return;
  const total = Math.max(1, Number(payload.total_frames || 100) - 1);
  els.frameTimeline.max = String(total);
  els.frameTimeline.value = String(Math.min(total, Number(payload.cursor || 0)));
  els.frameLabel.textContent = `Frame ${els.frameTimeline.value} | ${payload.playing ? "Playing" : "Paused"} | ${Number(payload.speed || 1)}x`;
  showReplayBufferFrame(payload);
  ensureReplayClock(Boolean(payload.playing));
}

// The Replay workspace stage shows the FROZEN BUFFER frame at the backend
// cursor — never the live feed (that was the "No replay buffer loaded" bug:
// controls moved the cursor but nothing ever fetched the buffer's frames).
function showReplayBufferFrame(payload) {
  const cams = payload.camera_ids || [];
  if (!payload.total_frames || !cams.length) {
    state.replayArmed = false;
    const uePanel = document.getElementById("replay-ue-panel");
    if (uePanel) uePanel.hidden = true;
    return;
  }
  state.replayArmed = true;
  const primary = getPrimaryCameraId();
  const cam = cams.includes(primary) ? primary : cams[0];
  if (els.replayFeed) {
    els.replayFeed.src = `${API_BASE}/api/replay/${cam}.jpg?frame_index=${Number(payload.cursor || 0)}&t=${Date.now()}`;
  }
  armReplayUePanel(payload);
  drawReplayUePanel(payload);
}

// Entering the Replay view (or clicking Replay on a review) attaches to the
// existing frozen buffer via GET state — read-only, never clobbers the snapshot.
async function armReplayWorkspace() {
  try { renderReplayState(await jsonFetch("/api/replay/state")); } catch {}
}

// Broadcast-style UltraEdge panel over the Replay stage: the frozen window's
// real waveform revealed to the cursor, spike frames cyan, markers for every
// detected transient, in sync with the frozen-buffer playback.
const replayUe = { key: null, buckets: [], events: [], available: false, pending: false };

function replayUeEvents() {
  return (state.decision?.edge_analysis?.events || [])
    .map((e) => Number(e.frame_id ?? e.frame))
    .filter((f) => Number.isFinite(f));
}

async function armReplayUePanel(payload) {
  const key = `${payload.start_timestamp_ms}|${payload.end_timestamp_ms}|${payload.total_frames}`;
  if (replayUe.key === key || replayUe.pending) return;
  replayUe.pending = true;
  replayUe.key = key;
  replayUe.buckets = [];
  replayUe.available = false;
  try {
    const buckets = Math.max(240, Math.min(3600, Number(payload.total_frames || 0) * 3));
    const wf = await jsonFetch(`/api/audio/waveform?start_ms=${payload.start_timestamp_ms}&end_ms=${payload.end_timestamp_ms}&buckets=${buckets}`);
    if (wf.available) { replayUe.buckets = wf.buckets; replayUe.available = true; }
  } catch {}
  replayUe.pending = false;
  drawReplayUePanel(payload);
}

function drawReplayUePanel(payload) {
  const host = document.getElementById("replay-ue-panel");
  const canvas = document.getElementById("replay-ue-canvas");
  if (!host || !canvas) return;
  replayUe.events = replayUeEvents();
  const total = Number(payload.total_frames || 0);
  const show = Boolean(state.replayArmed && total > 0 && (replayUe.available || replayUe.events.length));
  host.hidden = !show;
  if (!show) return;
  const ctx2 = canvas.getContext("2d");
  const W = canvas.width, H = canvas.height, PADX = 20;
  const plotW = W - PADX * 2, mid = H / 2, amp = H * 0.4;
  const cursor = Number(payload.cursor || 0);
  ctx2.clearRect(0, 0, W, H);
  ctx2.strokeStyle = "rgba(140,175,195,0.3)";
  ctx2.lineWidth = 1;
  ctx2.beginPath(); ctx2.moveTo(PADX, mid); ctx2.lineTo(W - PADX, mid); ctx2.stroke();
  const nb = replayUe.buckets.length;
  if (nb) {
    const revealed = Math.floor(((cursor + 1) / total) * nb);
    const bw = Math.max(1, (plotW / nb) * 0.6);
    for (let b = 0; b < revealed && b < nb; b++) {
      const frame = Math.floor((b / nb) * total);
      const spike = replayUe.events.some((f) => Math.abs(f - frame) <= 1);
      const x = PADX + (plotW * (b + 0.5)) / nb;
      ctx2.strokeStyle = spike ? "#7df0ff" : "rgba(238,248,252,0.9)";
      ctx2.lineWidth = bw;
      ctx2.beginPath();
      ctx2.moveTo(x, mid - replayUe.buckets[b][1] * amp - 0.5);
      ctx2.lineTo(x, mid - replayUe.buckets[b][0] * amp + 0.5);
      ctx2.stroke();
    }
  }
  ctx2.fillStyle = "#ff5252";
  replayUe.events.forEach((f) => {
    const x = PADX + (plotW * (f + 0.5)) / total;
    ctx2.beginPath();
    ctx2.moveTo(x, H - 16); ctx2.lineTo(x - 5, H - 6); ctx2.lineTo(x + 5, H - 6);
    ctx2.closePath(); ctx2.fill();
  });
  const cxp = PADX + plotW * Math.min(1, (cursor + 1) / total);
  ctx2.strokeStyle = "#ffffff";
  ctx2.lineWidth = 2;
  ctx2.beginPath(); ctx2.moveTo(cxp, 4); ctx2.lineTo(cxp, H - 4); ctx2.stroke();
}

// While the backend replay is playing its cursor advances server-side; poll the
// state so the stage animates even when no /ws/replay push arrives.
function ensureReplayClock(playing) {
  if (playing && !timers.replayClock) {
    timers.replayClock = setInterval(async () => {
      try { renderReplayState(await jsonFetch("/api/replay/state")); } catch {}
    }, 150);
  }
  if (!playing && timers.replayClock) {
    clearInterval(timers.replayClock);
    timers.replayClock = null;
  }
}

/* ===================== Replay engine: mode-driven scene =====================
   The active review module's `supports` contract decides which overlay layers,
   strips and actions EXIST here — LBW gets the ball path, Wide adds the guideline,
   No Ball / Run Out / Stumping get crease + freeze-frame + zoom, Edge gets the
   audio timeline. One renderer, six scenes; no per-type conditionals sprinkled
   through the page. Guide lines are schematic until calibrated and say so; the
   ball path is the REAL analysed pixel track. */
const REPLAY_LAYER_DEFS = [
  { key: "trajectory", label: "Ball path" },
  { key: "guideline", label: "Wide guideline" },
  { key: "crease", label: "Crease line" },
];

function decisionFrameFor(decision) {
  const crease = decision.run_out_analysis || decision.stumping_analysis || {};
  if (crease.frame_number != null) return Number(crease.frame_number);
  const nb = decision.no_ball_analysis || {};
  if (nb.landing_frame_id != null) return Number(nb.landing_frame_id);
  return null;
}

function applyReplayMode() {
  const mod = REVIEW_MODULES[state.reviewType] || {};
  const sup = mod.supports || { frame_step: true };
  if (els.replayLayers) {
    els.replayLayers.innerHTML = REPLAY_LAYER_DEFS.filter((layer) => sup[layer.key]).map((layer) => `
      <label><input type="checkbox" data-replay-layer="${layer.key}" ${state.replayLayerOff[layer.key] ? "" : "checked"}/> ${layer.label}</label>`).join("");
  }
  els.replayOverlay?.querySelectorAll("[data-rlayer]").forEach((group) => {
    const key = group.dataset.rlayer;
    group.hidden = !sup[key] || Boolean(state.replayLayerOff[key]);
  });
  if (els.replayAudioStrip) {
    els.replayAudioStrip.hidden = !sup.audio;
    if (sup.audio) drawReplayAudio(state.decision || {});
  }
  if (els.replayZoom) els.replayZoom.hidden = !sup.zoom;
  if (els.replayJumpDecision) {
    const frame = decisionFrameFor(state.decision || {});
    els.replayJumpDecision.hidden = !sup.freeze_frame;
    els.replayJumpDecision.disabled = frame == null;
    els.replayJumpDecision.title = frame == null
      ? "No decision frame yet — run the review first"
      : `Seek to frame ${frame} — the frame the module decided on`;
  }
  syncCanonicalSurfaces();                                 // pipeline replay videos (LBW)
  drawReplayScene(state.decision || {});
}

function drawReplayScene(decision) {
  if (!els.rpBallPath) return;
  const overlay = decision.overlay || {};
  const path = overlay.ball_path || overlay.measured_px || [];
  const pts = path
    .map((p) => (Array.isArray(p) ? `${p[0]},${p[1]}` : `${p.x},${p.y}`))
    .join(" ");
  els.rpBallPath.setAttribute("points", pts);
}

function drawReplayAudio(decision) {
  if (!els.replayAudioTrack) return;
  const edge = decision.edge_analysis || {};
  const events = edge.events || [];
  const total = Math.max(1, Number(els.frameTimeline?.max || 100));
  els.replayAudioTrack.innerHTML = events.map((ev) => {
    const fid = Number(ev.frame_id ?? ev.frame ?? 0);
    const left = Math.max(0, Math.min(100, (fid / total) * 100));
    return `<i class="ras-spike" style="left:${left}%" data-label="f${fid}" data-spike-frame="${fid}"></i>`;
  }).join("");
  if (els.replayAudioNote) {
    els.replayAudioNote.textContent = events.length
      ? `${events.length} spike(s) — click a spike to seek the replay there.`
      : edge.reason || "No UltraEdge events for this review — request an Edge review first.";
  }
}

/* ===================== Sync Replay (multi-cam, one timeline) =====================
   One master clock in CAPTURE TIME; every pane fetches its camera's frame nearest
   that timestamp via /api/replay/{cam}.jpg?timestamp_ms=… — synchronization comes
   from aligning on when frames were captured, not on frame index (cameras drop
   frames independently, so index N is a different moment per camera). */
const SyncReplay = {
  meta: null, cams: [], t: 0, speed: 1, timer: null,
  // View entry ATTACHES to the existing frozen snapshot (GET state — no side effect),
  // so after any review this page shows the SAME timeline the review captured. The
  // "Capture fresh buffer" button is the only thing that takes a new snapshot.
  async ensure() { await this.load(false); },
  async load(fresh) {
    try {
      const meta = fresh
        ? await jsonFetch("/api/replay/create", { method: "POST" })
        : await jsonFetch("/api/replay/state");
      // Same frozen window as before → keep playback position and panes untouched
      // (view-router re-entry fires this on every internal click).
      const same = this.meta
        && meta.start_timestamp_ms === this.meta.start_timestamp_ms
        && meta.end_timestamp_ms === this.meta.end_timestamp_ms;
      this.meta = { ...this.meta, ...meta };
      const available = meta.camera_ids || [];
      if (!this.cams.length) this.cams = available.slice(0, 2);
      this.cams = this.cams.filter((id) => available.includes(id));
      this.renderPills(available);
      if (same) return;
      this.pause();
      this.renderPanes();
      if (meta.start_timestamp_ms != null) {
        this.show(meta.start_timestamp_ms);
      } else {
        const label = document.getElementById("syncrep-label");
        if (label) label.textContent = "No frames buffered yet — start the cameras first";
      }
    } catch {
      const grid = document.getElementById("syncrep-grid");
      if (grid) grid.innerHTML = `<div class="rev-empty">Backend offline — sync replay unavailable.</div>`;
    }
  },
  renderPills(available) {
    const host = document.getElementById("syncrep-cams");
    if (!host) return;
    host.innerHTML = available.map((id) => `
      <button type="button" class="rev-chip ${this.cams.includes(id) ? "active" : ""}" data-sync-cam="${id}">${cameraRoleFor(id)} · ${id}</button>`).join("");
  },
  renderPanes() {
    const grid = document.getElementById("syncrep-grid");
    if (!grid) return;
    if (!this.cams.length) { grid.innerHTML = `<div class="rev-empty">Pick 1–3 cameras above.</div>`; return; }
    grid.dataset.count = String(this.cams.length);
    grid.innerHTML = this.cams.map((id) => `
      <figure class="sync-pane">
        <img data-sync-pane="${id}" alt="Camera ${id} replay" />
        <figcaption>${cameraRoleFor(id)} · Cam ${id}</figcaption>
      </figure>`).join("");
  },
  show(t) {
    const m = this.meta;
    if (!m || m.start_timestamp_ms == null) return;
    this.t = Math.max(m.start_timestamp_ms, Math.min(m.end_timestamp_ms, t));
    this.cams.forEach((id) => {
      const img = document.querySelector(`[data-sync-pane="${id}"]`);
      if (img) img.src = `${API_BASE}/api/replay/${id}.jpg?timestamp_ms=${this.t}&t=${Date.now()}`;
    });
    const span = (m.end_timestamp_ms - m.start_timestamp_ms) || 1;
    const slider = document.getElementById("syncrep-timeline");
    if (slider) slider.value = String(Math.round(((this.t - m.start_timestamp_ms) / span) * 1000));
    const label = document.getElementById("syncrep-label");
    if (label) label.textContent = `${((this.t - m.start_timestamp_ms) / 1000).toFixed(2)}s / ${(span / 1000).toFixed(2)}s · ${this.cams.length} cam${this.cams.length === 1 ? "" : "s"}`;
  },
  play() {
    if (!this.meta || this.meta.start_timestamp_ms == null) return;
    this.pause();
    this.timer = setInterval(() => {
      if (this.t >= this.meta.end_timestamp_ms) { this.pause(); return; }
      this.show(this.t + (1000 / 30) * this.speed);
    }, 1000 / 30);
  },
  pause() { clearInterval(this.timer); this.timer = null; },
  step(direction) { this.pause(); this.show(this.t + direction * (1000 / 30)); },
};

async function exportReplay() {
  els.frameLabel.textContent = "Exporting replay...";
  try {
    const payload = await jsonFetch("/api/replay/export", { method: "POST" });
    els.frameLabel.textContent = `Exported: ${payload.path}`;
    showToast("Replay exported", "not-out");
  } catch {
    els.frameLabel.textContent = "Export unavailable";
    showToast("Export unavailable", "out");
  }
}


let ueFrame = 0;


function showToast(message, kind = "") {
  let host = document.getElementById("toast-host");
  if (!host) {
    host = document.createElement("div");
    host.id = "toast-host";
    host.className = "toast-host";
    document.body.appendChild(host);
  }
  const toast = document.createElement("div");
  toast.className = `toast ${kind}`;
  toast.innerHTML = `<i></i><span>${message}</span>`;
  host.appendChild(toast);
  setTimeout(() => {
    toast.classList.add("dismiss");
    setTimeout(() => toast.remove(), 400);
  }, 3400);
}


function connectChannel(channel) {
  const socket = new WebSocket(`${WS_BASE}/ws/${channel}`);
  socket.addEventListener("message", (event) => {
    try {
      const payload = JSON.parse(event.data);
      if (channel === "decision" && payload.decision) renderDecision(payload.decision);
      if (channel === "system" && payload.health) renderSystemPayload(payload.health);
      if (channel === "live" && payload.cameras) {
        state.cameras = payload.cameras;
        renderCameraGrid();
        renderCameraThumbs();
        renderLiveFrames(payload.frames);
        renderSystemStatus();
      }
      if (channel === "trajectory" && payload.trajectory) {
        renderDecision({ ...state.decision, trajectory: payload.trajectory });
      }
      if (channel === "replay" && payload.replay) renderReplayState(payload.replay);
    } catch {}
  });
  socket.addEventListener("close", () => { setTimeout(() => connectChannel(channel), 2000); });
}

function connectWebSockets() {
  ["system", "decision", "trajectory", "replay", "live"].forEach(connectChannel);
}

function renderSystemPayload(health) {
  state.lastHealth = health;
  els.healthGrid.innerHTML = [
    ["CPU", pctRaw(health.cpu_percent)],
    ["RAM", pctRaw(health.ram_percent)],
    ["GPU", health.gpu?.available ? pctRaw(health.gpu.percent) : "Telemetry n/a"],
    ["CUDA Temp", health.gpu?.temperature_c != null ? `${Math.round(health.gpu.temperature_c)}°C` : "--"],
    ["FPS", Object.values(health.camera_fps || {}).map((value) => Number(value).toFixed(1)).join(" / ") || "--"],
    ["Drops", sumValues(health.frame_drops)],
    ["Latency", `${health.latency_ms} ms`],
    ["Calibration", health.calibration?.readiness || "missing"],
    ["Tracking", trackingHealthLabel(health.camera_health)],
    ["Storage (SSD)", `${health.storage?.free_gb ?? "--"} GB free`],
    ["Network", health.network?.status || "--"],
  ].map(([label, value]) => `<div><span>${label}</span><strong>${value}</strong></div>`).join("");
  updateHealthKpis(health);
  renderSystemStatus();
}

/* ===================== pre-match operator checklist ===================== */
const PF_GROUPS = [
  ["cameras", "Cameras"],
  ["capture", "Capture & Replay"],
  ["vision", "Vision & Models"],
  ["system", "System"],
];

function preflightDetectedCameras() {
  return (state.cameras || []).map((camera) => camera.id);
}

function preflightSelectedCameras() {
  const detected = preflightDetectedCameras();
  const saved = Array.isArray(state.preflightSelected)
    ? state.preflightSelected.filter((id) => detected.includes(id))
    : null;
  return saved && saved.length ? saved : detected;
}

// Camera-in-use selection lives on the CAMERAS page (the owner of camera config).
// The checklist only READS state.preflightSelected; the persisted key is shared.
function renderCamerasInUse() {
  if (!els.camerasInUse) return;
  const detected = preflightDetectedCameras();
  if (!detected.length) {
    els.camerasInUse.innerHTML = `<span class="pf-empty">No cameras detected yet</span>`;
    return;
  }
  const active = new Set(preflightSelectedCameras());
  // Buttons, not checkboxes-in-labels: a nested <input> double-toggles (the label
  // forwards a second click), so a real click would never register.
  els.camerasInUse.innerHTML = detected.map((id, index) => `
    <button type="button" class="pf-cam ${active.has(id) ? "on" : ""}" data-inuse-cam="${id}" aria-pressed="${active.has(id)}">
      CAM ${String.fromCharCode(65 + index)}<small>#${id}</small>
    </button>
  `).join("");
}

// Where each failed/warn checklist item is actually fixed. The checklist itself
// never fixes anything — it only navigates to the owning page.
function preflightOwner(item) {
  const key = item.key || "";
  if (key.startsWith("camera_")) return { view: "cameras", label: "Cameras" };
  if (key === "fps_stable") return { view: "camera-health", label: "Camera Health" };
  if (key === "replay_buffer") return { view: "replay", label: "Replay" };
  if (key === "calibration") return { view: "calibration", label: "Calibration" };
  if (key === "models") return { view: "models", label: "Model Manager" };
  return { view: "health", label: "System" }; // storage / audio / gpu
}

function pfBanner(stateName, text) {
  if (!els.preflightBanner) return;
  els.preflightBanner.className = `preflight-banner ${stateName}`;
  els.preflightBanner.textContent = text;
}

function renderPreflight(data) {
  const rows = (data.items || []).filter((item) => item.key !== "match_ready");
  els.preflightGrid.innerHTML = PF_GROUPS.map(([key, title]) => {
    const items = rows.filter((item) => item.group === key);
    if (!items.length) return "";
    const body = items.map((item) => {
      const optional = item.required ? "" : " (optional)";
      // Read-only: pass rows need no action; anything else deep-links to its owner.
      const owner = preflightOwner(item);
      const link = item.status === "pass"
        ? ""
        : `<button type="button" class="pf-goto" data-pf-goto="${owner.view}">→ ${owner.label}</button>`;
      return `
        <div class="pf-row ${item.status}">
          <span class="pf-pill ${item.status}">${item.status.toUpperCase()}</span>
          <span class="pf-txt"><strong>${item.label}${optional}</strong><small>${item.detail}</small></span>
          ${link}
        </div>`;
    }).join("");
    return `<div class="pf-group"><div class="pf-group-h">${title}</div>${body}</div>`;
  }).join("");

  const summary = data.summary || {};
  if (els.preflightSummary) {
    els.preflightSummary.textContent = `${summary.pass || 0} pass · ${summary.warn || 0} warn · ${summary.fail || 0} fail`;
  }

  const warnings = data.warnings || [];
  const blocking = data.blocking || [];
  if (blocking.length) pfBanner("fail", `NOT READY — ${blocking.length} blocking check${blocking.length > 1 ? "s" : ""}`);
  else if (warnings.length) pfBanner("warn", `Ready with ${warnings.length} warning${warnings.length > 1 ? "s" : ""}`);
  else pfBanner("pass", "MATCH READY — cleared for live operation");
}

// ONE preflight fetch feeds both consumers: the full checklist page and the
// dashboard's one-line readiness. They can never disagree.
async function refreshPreflight() {
  if (state.view !== "checklist" && state.view !== "dashboard") return;
  const ids = preflightSelectedCameras();
  const params = new URLSearchParams();
  if (ids.length) params.set("cameras", ids.join(","));
  try {
    const data = await jsonFetch(`/api/preflight?${params.toString()}`);
    renderReadiness(data);
    if (state.view === "checklist" && els.preflightGrid) renderPreflight(data);
  } catch {
    state.readiness = null;
    if (els.readinessStrip) els.readinessStrip.hidden = true;
    if (state.view !== "checklist" || !els.preflightGrid) return;
    els.preflightGrid.innerHTML = `<div class="pf-offline">Backend offline — cannot verify readiness</div>`;
    if (els.preflightSummary) els.preflightSummary.textContent = "--";
    pfBanner("pending", "Backend offline");
  }
}

/* ===================== Reviews (archive of completed decisions) ===================== */
function reviewTypeSlug(review) {
  return String(review.review_type || review.type || "").toLowerCase().replace(/[^a-z]/g, "");
}

function reviewMatchesFilters(review) {
  const filter = state.reviewFilter || "all";
  if (filter !== "all") {
    const slug = reviewTypeSlug(review);
    // "noball" covers "no ball"/"no_ball"; "runout" covers "run out"/"run_out".
    if (!slug.includes(filter)) return false;
  }
  const query = (state.reviewSearch || "").trim().toLowerCase();
  if (query) {
    const op = (review.provenance && review.provenance.operator) || review.operator || "";
    const hay = [review.id, review.decision, review.review_type || review.type, op]
      .map((v) => String(v || "").toLowerCase()).join(" ");
    if (!hay.includes(query)) return false;
  }
  return true;
}

function exportReviewJson(reviewId) {
  const review = (state.reviewsCache || []).find((r) => String(r.id) === String(reviewId));
  if (!review) return;
  const blob = new Blob([JSON.stringify(review, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${review.id || "review"}.json`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

function paintReviews() {
  if (!els.reviewsList) return;
  const all = state.reviewsCache || [];
  const rows = all.filter(reviewMatchesFilters);
  if (els.reviewsCount) {
    els.reviewsCount.textContent = rows.length === all.length
      ? `${all.length} review${all.length === 1 ? "" : "s"}`
      : `${rows.length} of ${all.length}`;
  }
  if (!all.length) {
    els.reviewsList.innerHTML = `<div class="rev-empty">No reviews yet — completed decisions appear here.</div>`;
    return;
  }
  if (!rows.length) {
    els.reviewsList.innerHTML = `<div class="rev-empty">No reviews match the current filter.</div>`;
    return;
  }
  els.reviewsList.innerHTML = `
    <table class="rev-table">
      <thead><tr><th></th><th>Type</th><th>Decision</th><th class="engineer-only">Confidence</th><th class="engineer-only">Model</th><th>Time</th><th class="engineer-only"></th></tr></thead>
      <tbody>
        ${rows.map((review) => {
          const decision = String(review.decision || "--");
          const cls = decision === "OUT" ? "out" : decision === "NOT OUT" ? "not-out" : decision === "INTERRUPTED" ? "interrupted" : "";
          const conf = review.confidence != null ? `${Math.round(Number(review.confidence) * 100)}%` : "--";
          const type = String(review.review_type || review.type || "—").toUpperCase();
          const model = (review.provenance && review.provenance.model) || "—";
          const time = review.time ? new Date(Number(review.time)).toLocaleTimeString() : "--";
          const rid = review.review_id || "";
          return `<tr>
            <td><button type="button" class="rev-open" data-open-review="${rid}"${rid ? "" : " disabled"}>▶ Open Review</button></td>
            <td>${type}</td>
            <td><span class="rev-dec ${cls}">${decision}</span></td>
            <td class="engineer-only">${conf}</td>
            <td class="engineer-only rev-model">${model}</td>
            <td>${time}</td>
            <td class="engineer-only"><button type="button" class="rev-export" data-export-review="${review.id || ""}">Export</button></td>
          </tr>`;
        }).join("")}
      </tbody>
    </table>`;
}

// History "Open Review": reconstruct a PAST review in Review Mode (read-only) —
// protocol states, gates, verdict, and both replays from its canonical job. One
// review experience: reopening feels like the original session, not a replay jump.
async function openReviewFromHistory(reviewId) {
  if (!reviewId) return;
  // Never let a read-only restore collide with a LIVE review in flight — that left
  // the two fighting over Review Mode. One review experience: finish first.
  // The test is the REVIEW's state, not the workspace's: Back now closes the
  // workspace without ending the review, so a live review can be running with
  // ReviewMode.active === false.
  if (state.activeAppeal || (ReviewMode.active && !ReviewMode.restored)) {
    showToast("Finish or abandon the current review first", "out");
    return;
  }
  let decision;
  try {
    decision = await jsonFetch(`/api/reviews/${encodeURIComponent(reviewId)}/full`);
  } catch {
    showToast("This review has no stored detail to reopen", "out");
    return;
  }
  els.historyDialog?.close?.();
  state.reviewType = decision.review_type || state.reviewType;   // gate protocol/gates correctly
  state.decision = decision;
  state.canonical = null;
  ReviewMode.enterRestored(decision);
  const jobId = decision.canonical_job_id;
  if (!jobId) {
    ["rm-observed", "rm-broadcast"].forEach((id) => { const b = document.getElementById(id); if (b) b.innerHTML = `<div class="rm2-noreplay">No stored replay for this review</div>`; });
    return;
  }
  try {
    const results = await jsonFetch(`/api/analyze/${jobId}/results`);
    state.canonical = { jobId, results };
    ReviewMode.setCanonical(jobId, results);
    ReviewMode.renderProtocol();
  } catch {
    ["rm-observed", "rm-broadcast"].forEach((id) => { const b = document.getElementById(id); if (b) b.innerHTML = `<div class="rm2-noreplay">Replay files for this review are no longer available</div>`; });
  }
}

async function renderReviews() {
  if (!els.reviewsList) return;
  try {
    const data = await jsonFetch("/api/reviews");
    state.reviewsCache = data.reviews || [];
    paintReviews();
  } catch {
    els.reviewsList.innerHTML = `<div class="rev-empty">Backend offline — reviews unavailable.</div>`;
    if (els.reviewsCount) els.reviewsCount.textContent = "--";
  }
}

/* ===================== System: environment + activity log ===================== */
const ACTIVITY_ICONS = {
  backend_started: "⚙", session_started: "▶", review_requested: "🔎",
  decision_confirmed: "✔", replay_exported: "⬇", calibration_saved: "◎",
  camera_connected: "🟢", camera_disconnected: "🔴", model_promoted: "◑",
};

function relativeTime(ms) {
  const diff = Date.now() - Number(ms);
  if (!Number.isFinite(diff)) return "";
  const s = Math.max(0, Math.round(diff / 1000));
  if (s < 60) return `${s}s ago`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  return new Date(Number(ms)).toLocaleString();
}

async function renderSystemInfo() {
  if (!els.systemInfoGrid) return;
  try {
    const info = await jsonFetch("/api/system/info");
    const db = info.database || {};
    const rows = [
      ["Python", info.python],
      ["Platform", info.platform],
      ["PyTorch", info.torch || "—"],
      ["CUDA", info.cuda || "CPU only"],
      ["OpenCV", info.opencv || "—"],
      ["Ultralytics", info.ultralytics || "—"],
      ["GPU", (info.gpu && info.gpu.detail) || "—"],
      ["Disk free", info.disk_free_gb != null ? `${info.disk_free_gb} GB` : "—"],
      ["Database", db.exists ? `${db.size_mb} MB` : "not created"],
      ["Git commit", info.git_commit || "no repo"],
    ];
    els.systemInfoGrid.innerHTML = rows.map(([k, v]) =>
      `<div class="sysinfo-row"><span>${k}</span><strong>${String(v)}</strong></div>`).join("");
  } catch {
    els.systemInfoGrid.innerHTML = `<div class="rev-empty">Backend offline — environment unavailable.</div>`;
  }
}

async function renderActivityLog() {
  if (!els.activityLog) return;
  try {
    const data = await jsonFetch("/api/activity?limit=80");
    const events = data.events || [];
    if (!events.length) {
      els.activityLog.innerHTML = `<div class="rev-empty">No activity recorded yet.</div>`;
      return;
    }
    els.activityLog.innerHTML = events.map((ev) => {
      const icon = ACTIVITY_ICONS[ev.kind] || "•";
      return `<div class="activity-row">
        <span class="act-ic">${icon}</span>
        <span class="act-msg">${String(ev.message || ev.kind)}</span>
        <span class="act-time">${relativeTime(ev.ts_ms)}</span>
      </div>`;
    }).join("");
  } catch {
    els.activityLog.innerHTML = `<div class="rev-empty">Backend offline — activity unavailable.</div>`;
  }
}

function renderSystemView() {
  renderSystemInfo();
  renderActivityLog();
}

/* ===================== Camera Health (live per-camera telemetry) ===================== */
function renderCameraHealth() {
  if (!els.camhealthGrid) return;
  const cameras = state.cameras || [];
  const online = cameras.filter((camera) => camera.connected).length;
  if (els.camhealthSummary) els.camhealthSummary.textContent = cameras.length ? `${online}/${cameras.length} online` : "No cameras";
  if (!cameras.length) {
    els.camhealthGrid.innerHTML = `<div class="rev-empty">No cameras detected.</div>`;
    return;
  }
  els.camhealthGrid.innerHTML = cameras.map((camera, index) => {
    const letter = index < 26 ? String.fromCharCode(65 + index) : String(camera.id);
    const status = camera.status || (camera.connected ? "online" : "offline");
    const fps = Number(camera.fps || 0).toFixed(1);
    const latency = camera.latency_ms != null ? `${Math.round(camera.latency_ms)} ms` : "--";
    const drops = camera.dropped_frames != null ? camera.dropped_frames : "--";
    const health = camera.health_score != null ? `${Math.round(camera.health_score * 100)}%` : "--";
    const source = camera.synthetic ? "synthetic" : "live";
    return `
      <article class="camh-card ${status}">
        <header><strong>Camera ${letter}</strong><span class="camh-dot ${status}"></span></header>
        <div class="camh-fps">${fps}<small>fps</small></div>
        <dl>
          <div><dt>Status</dt><dd>${status}</dd></div>
          <div><dt>Latency</dt><dd>${latency}</dd></div>
          <div><dt>Dropped</dt><dd>${drops}</dd></div>
          <div><dt>Health</dt><dd>${health}</dd></div>
          <div><dt>Source</dt><dd>${source}</dd></div>
          <div><dt>Index</dt><dd>#${camera.id}</dd></div>
        </dl>
      </article>`;
  }).join("");
}

async function toggleMode() {
  const next = state.mode.id === "thermal_demo" ? "visible" : "thermal_demo";
  state.mode = window.drs?.setAnalysisMode
    ? await window.drs.setAnalysisMode({ mode: next })
    : await jsonFetch("/api/analysis-mode", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: next }),
      });
  renderMode();
  refreshCameraFrames();
}

function statusClass(status) {
  if (status === "OUT") return "out";
  if (status === "NOT_OUT") return "not-out";
  if (status === "PROCESSING") return "processing";
  return "waiting";
}

function displayStatus(status) {
  return String(status).replaceAll("_", " ");
}


function pct(value) {
  return value === null || value === undefined ? "--" : `${Math.round(Number(value) * 100)}%`;
}

function pctRaw(value) {
  if (value === null || value === undefined) return "--";
  return `${Math.round(Math.max(0, Math.min(100, Number(value))))}%`;
}

function updateHealthKpis(health) {
  const fpsValues = Object.values(health.camera_fps || {}).map(Number).filter((value) => value > 0);
  const avgFps = fpsValues.length ? fpsValues.reduce((a, b) => a + b, 0) / fpsValues.length : 0;
  if (els.kpiFps) els.kpiFps.textContent = avgFps ? avgFps.toFixed(1) : "--";
  if (els.kpiLatency) els.kpiLatency.textContent = health.latency_ms != null ? `${Math.round(health.latency_ms)} ms` : "--";
  if (els.kpiInference) els.kpiInference.textContent = health.inference_ms != null ? `${Math.round(health.inference_ms)} ms` : "--";
  if (els.kpiTracking) els.kpiTracking.textContent = trackingHealthLabel(health.camera_health);
  if (els.kpiSync) els.kpiSync.textContent = health.sync_error_ms != null ? `${Number(health.sync_error_ms).toFixed(1)} ms` : "--";
  const gpu = health.gpu || {};
  if (els.kpiGpu) els.kpiGpu.textContent = gpu.available ? pctRaw(gpu.percent) : "n/a";
  if (els.kpiCuda) els.kpiCuda.textContent = gpu.temperature_c != null ? `${Math.round(gpu.temperature_c)}°C` : "--";
  const calib = health.calibration || {};
  if (els.kpiCalibration) els.kpiCalibration.textContent = calib.readiness || "--";
  if (els.calibReadiness) els.calibReadiness.textContent = calib.readiness || "--";
  if (els.calibRms) els.calibRms.textContent = calib.rms_error_px != null ? `${Number(calib.rms_error_px).toFixed(1)} px` : "--";
  if (els.calibCams) els.calibCams.textContent = calib.calibrated_cameras != null ? `${calib.calibrated_cameras}` : "--";
  if (els.calibHomography) els.calibHomography.textContent = calib.homography_error_cm != null ? `${Number(calib.homography_error_cm).toFixed(1)} cm` : "--";
}

function formatPoint(point) {
  if (!point) return "--";
  if (Array.isArray(point)) return point.map((value) => Number(value).toFixed(1)).join(", ");
  if (typeof point !== "object") return String(point);
  return `${Number(point.x).toFixed(1)}, ${Number(point.y).toFixed(1)}, ${Number(point.z ?? 0).toFixed(1)}`;
}

function formatDuration(seconds) {
  if (seconds === null || seconds === undefined) return "--";
  const mins = Math.floor(seconds / 60);
  const secs = seconds % 60;
  return mins ? `${mins}m ${secs}s` : `${secs}s`;
}

function sumValues(values) {
  return Object.values(values || {}).reduce((total, value) => total + Number(value || 0), 0);
}

function trackingHealthLabel(cameras) {
  if (!Array.isArray(cameras) || !cameras.length) return "--";
  const score = cameras.reduce((total, camera) => total + Number(camera.health_score || 0), 0) / cameras.length;
  if (score >= 0.8) return "high";
  if (score >= 0.55) return "medium";
  return "low";
}

/* ===================== event wiring =====================
   All listeners use optional chaining: a single missing element id must never
   throw here and abort the rest of startup (view init, websockets, timers). */
els.requestReview?.addEventListener("click", requestReview);
els.openReviewMode?.addEventListener("click", reopenReviewMode);
// Evidence Checklist rows are deep links into the workspace: click the evidence
// you want to inspect, not just "the workspace". The Decision footer lands on
// the decision stage (verdict + confirm actions).
document.getElementById("rs-evidence")?.addEventListener("click", (ev) => {
  const row = ev.target.closest("[data-stage]");
  if (row) openReviewAtStage(row.dataset.stage);
});
document.getElementById("rs-decision")?.addEventListener("click", () => openReviewAtStage("decision"));
// Readiness is a summary, not a verdict — clicking opens the checklist that owns it.
els.readinessStrip?.addEventListener("click", () => setView("checklist"));
// Export lives in the review workspace — it exports THAT review's clip.
document.getElementById("rm-export")?.addEventListener("click", async (event) => {
  // ONE export, browser-download style: a save dialog asks WHERE, the backend
  // renders the broadcast review clip (UltraEdge scene + verdict) to that path.
  const btn = event.currentTarget;
  const label = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Rendering…";
  try {
    const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
    const suggestedName = `DRS_${String(state.reviewType || "review").toUpperCase()}_${stamp}.mp4`;
    let result;
    if (window.drs?.exportBroadcast) {
      result = await window.drs.exportBroadcast({ suggestedName });
    } else {
      result = await jsonFetch("/api/broadcast/export", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
    }
    if (result?.canceled) return;
    if (result?.error) throw new Error(result.error);
    showToast(`Review clip saved: ${result.path}`, "not-out");
  } catch {
    showToast("Export failed — is a review loaded?", "out");
  } finally {
    btn.disabled = false;
    btn.textContent = label;
  }
});

// Reviews page: type-filter chips, search box, and per-row JSON export.
els.revFilters?.addEventListener("click", (event) => {
  const chip = event.target.closest("[data-rev-filter]");
  if (!chip) return;
  state.reviewFilter = chip.dataset.revFilter;
  els.revFilters.querySelectorAll(".rev-chip").forEach((b) => b.classList.toggle("active", b === chip));
  paintReviews();
});
els.revSearch?.addEventListener("input", () => {
  state.reviewSearch = els.revSearch.value || "";
  paintReviews();
});
els.reviewsList?.addEventListener("click", (event) => {
  const open = event.target.closest("[data-open-review]");
  if (open) { openReviewFromHistory(open.dataset.openReview); return; }
  const btn = event.target.closest("[data-export-review]");
  if (btn) exportReviewJson(btn.dataset.exportReview);
});
els.activityRefresh?.addEventListener("click", renderActivityLog);

// Session identity chip: toggle the session card; close on any outside click.
document.getElementById("user-chip")?.addEventListener("click", (event) => {
  event.stopPropagation();
  const pop = document.getElementById("session-pop");
  if (!pop) return;
  updateSessionChip();          // fresh data every open
  pop.hidden = !pop.hidden;
});
document.addEventListener("click", (event) => {
  const pop = document.getElementById("session-pop");
  if (pop && !pop.hidden && !event.target.closest("#session-pop")) pop.hidden = true;
});

// Sync Replay: one master timeline drives every selected camera pane.
// The button is the ONLY fresh snapshot; view entry attaches to the existing one.
document.getElementById("syncrep-load")?.addEventListener("click", () => SyncReplay.load(true));
document.getElementById("syncrep-play")?.addEventListener("click", () => SyncReplay.play());
document.getElementById("syncrep-pause")?.addEventListener("click", () => SyncReplay.pause());
document.getElementById("syncrep-back")?.addEventListener("click", () => SyncReplay.step(-1));
document.getElementById("syncrep-forward")?.addEventListener("click", () => SyncReplay.step(1));
document.getElementById("syncrep-speed")?.addEventListener("change", (e) => { SyncReplay.speed = Number(e.target.value) || 1; });
document.getElementById("syncrep-timeline")?.addEventListener("input", (e) => {
  const m = SyncReplay.meta;
  if (!m || m.start_timestamp_ms == null) return;
  SyncReplay.pause();
  const span = m.end_timestamp_ms - m.start_timestamp_ms;
  SyncReplay.show(m.start_timestamp_ms + (Number(e.target.value) / 1000) * span);
});
document.getElementById("syncrep-cams")?.addEventListener("click", (e) => {
  const pill = e.target.closest("[data-sync-cam]");
  if (!pill) return;
  const id = Number(pill.dataset.syncCam);
  const at = SyncReplay.cams.indexOf(id);
  if (at >= 0) SyncReplay.cams.splice(at, 1);
  else { if (SyncReplay.cams.length >= 3) SyncReplay.cams.shift(); SyncReplay.cams.push(id); }
  SyncReplay.renderPills(SyncReplay.meta?.camera_ids || []);
  SyncReplay.renderPanes();
  SyncReplay.show(SyncReplay.t);
});
els.modeToggle?.addEventListener("click", toggleMode);
els.replayPlay?.addEventListener("click", () => replayControl("play", { speed: Number(els.replaySpeed.value) }));
els.replayPause?.addEventListener("click", () => replayControl("pause"));
els.replayBack?.addEventListener("click", () => replayControl("step_back"));
els.replayForward?.addEventListener("click", () => replayControl("step_forward"));
els.replaySpeed?.addEventListener("change", () => replayControl("speed", { speed: Number(els.replaySpeed.value) }));
els.replayExport?.addEventListener("click", exportReplay);
els.frameTimeline?.addEventListener("input", () => replayControl("seek", { frame_index: Number(els.frameTimeline.value) }));

// Replay mode-driven controls (which of these EXIST is decided by the module contract).
els.replayLayers?.addEventListener("change", (event) => {
  const cb = event.target.closest("[data-replay-layer]");
  if (!cb) return;
  state.replayLayerOff[cb.dataset.replayLayer] = !cb.checked;
  applyReplayMode();
});
els.replayJumpDecision?.addEventListener("click", () => {
  const frame = decisionFrameFor(state.decision || {});
  if (frame != null) replayControl("seek", { frame_index: frame });
});
els.replayZoom?.addEventListener("click", () => {
  const zoomed = els.replayStage?.classList.toggle("zoomed");
  if (els.replayZoom) els.replayZoom.textContent = zoomed ? "Zoom ✓" : "Zoom";
});
els.replayAudioTrack?.addEventListener("click", (event) => {
  const spike = event.target.closest("[data-spike-frame]");
  if (spike) replayControl("seek", { frame_index: Number(spike.dataset.spikeFrame) });
});
// Scale the overlay's coordinate space to the actual replay frame, so the REAL
// pixel ball path lands where the ball is; reposition the schematic guides.
els.replayFeed?.addEventListener("load", () => {
  const w = els.replayFeed.naturalWidth;
  const h = els.replayFeed.naturalHeight;
  if (!w || !h || !els.replayOverlay) return;
  els.replayOverlay.setAttribute("viewBox", `0 0 ${w} ${h}`);
  const wideX = Math.round(w * 0.28);
  const creaseY = Math.round(h * 0.62);
  const set = (id, attrs) => { const el = document.getElementById(id); if (el) Object.entries(attrs).forEach(([k, v]) => el.setAttribute(k, String(v))); };
  set("rp-wide-line", { x1: wideX, x2: wideX, y1: 0, y2: h });
  set("rp-wide-label", { x: wideX + 10, y: Math.round(h * 0.06) });
  set("rp-crease-line", { x1: 0, x2: w, y1: creaseY, y2: creaseY });
  set("rp-crease-label", { x: 16, y: creaseY - 10 });
});
els.closeTesting?.addEventListener("click", () => els.testingDialog.close());

async function launchVisionStudio(opts = {}) {
  if (!confirmDevelopmentLaunch()) {
    els.aiDevelopmentOutput.textContent = "Vision Studio launch canceled.";
    return;
  }
  els.aiDevelopmentOutput.textContent = "Opening Vision Studio...";
  const result = await window.drs.openVisionStudio(opts);
  els.aiDevelopmentOutput.textContent = result.ok
    ? (result.alreadyRunning ? `Vision Studio is already running. Focus ${result.focused ? "succeeded" : "requested"}.` : `Vision Studio launched: ${result.path}`)
    : `Vision Studio launch failed: ${result.message || "unknown error"}`;
  await refreshAiDevelopmentStatus();
}

els.openVisionStudio?.addEventListener("click", () => launchVisionStudio());
els.openVisionStudioWorkspace?.addEventListener("click", () => {
  launchVisionStudio({ workspace: els.visionStudioRecent?.value || undefined });
});
els.importMatchRecordings?.addEventListener("click", async () => {
  if (!confirmDevelopmentLaunch()) {
    els.aiDevelopmentOutput.textContent = "Import canceled.";
    return;
  }
  els.aiDevelopmentOutput.textContent = "Opening Vision Studio import workflow...";
  const result = await window.drs.importMatchRecordings({ workspace: els.visionStudioRecent?.value || undefined });
  els.aiDevelopmentOutput.textContent = result.ok
    ? (result.alreadyRunning ? "Vision Studio is already running. Focus it, then import recordings from the dashboard export folder." : "Vision Studio launched with match recordings import.")
    : `Import launch failed: ${result.message || "unknown error"}`;
  await refreshAiDevelopmentStatus();
});
els.newMatchBtn?.addEventListener("click", openNewMatchDialog);
els.nmCancel?.addEventListener("click", () => els.newMatchDialog.close());
els.nmConfirm?.addEventListener("click", confirmNewMatch);
els.openHistoryBtn?.addEventListener("click", openSessionHistory);
els.closeHistory?.addEventListener("click", () => els.historyDialog.close());

els.sidebarToggle?.addEventListener("click", toggleSidebar);
els.systemStatus?.addEventListener("click", () => setView("health"));
els.opModeMatch?.addEventListener("click", () => setOperatorMode("match"));
els.opModeEngineer?.addEventListener("click", () => setOperatorMode("engineer"));
els.settingsModeMatch?.addEventListener("click", () => setOperatorMode("match"));
els.settingsModeEngineer?.addEventListener("click", () => setOperatorMode("engineer"));
els.calmToggle?.addEventListener("click", () => setCalm(!document.body.classList.contains("calm")));
els.settingsCollapse?.addEventListener("click", toggleSidebar);
els.settingsModeAnalysis?.addEventListener("click", toggleMode);

document.querySelectorAll("[data-ai-command]").forEach((button) => {
  button.addEventListener("click", () => runAiDevelopmentCommand(button.dataset.aiCommand));
});
document.querySelectorAll("[data-ai-import]").forEach((button) => {
  button.addEventListener("click", () => importDataset(button.dataset.aiImport === "activate"));
});
document.querySelectorAll("[data-open-development-folder]").forEach((button) => {
  button.addEventListener("click", async () => {
    const kind = button.dataset.openDevelopmentFolder;
    els.aiDevelopmentOutput.textContent = `Opening ${kind} folder...`;
    const result = await window.drs.openDevelopmentFolder(kind);
    els.aiDevelopmentOutput.textContent = result.ok
      ? `Opened ${result.path}`
      : `Open folder failed: ${result.message || "unknown error"}`;
  });
});
// View router: sidebar nav + inline back buttons only. Crucially EXCLUDE the
// `<section class="view" data-view="...">` containers — binding navigation to
// them meant any click inside a view (e.g. the LBW "Replay" button) bubbled up
// and instantly setView'd back to that section, silently undoing the button's
// own navigation. That was the "dashboard Replay button does nothing" bug.
document.querySelectorAll("[data-view]:not(.view)").forEach((item) => {
  item.addEventListener("click", () => setView(item.dataset.view));
});

els.openSettings?.addEventListener("click", () => setView("settings"));
// Camera-in-use selection — owned by the Cameras page.
els.camerasInUse?.addEventListener("click", (event) => {
  const chip = event.target.closest?.("[data-inuse-cam]");
  if (!chip) return;
  event.stopPropagation();
  const id = Number(chip.getAttribute("data-inuse-cam"));
  const detected = preflightDetectedCameras();
  const current = new Set(preflightSelectedCameras());
  if (current.has(id)) current.delete(id);
  else current.add(id);
  if (current.size === 0) return; // never allow zero cameras selected
  const next = detected.filter((camId) => current.has(camId));
  state.preflightSelected = next.length === detected.length ? null : next;
  store.set("drs.preflightCameras", state.preflightSelected);
  // Toggle in place — rebuilding innerHTML here would drop the just-clicked chip.
  chip.classList.toggle("on", current.has(id));
  chip.setAttribute("aria-pressed", String(current.has(id)));
});
// Checklist deep-links: navigate to the owning page, never fix in place.
// stopPropagation so the bubbling click doesn't hit the section[data-view]
// router (which would immediately setView back to the checklist).
els.preflightGrid?.addEventListener("click", (event) => {
  const target = event.target.getAttribute?.("data-pf-goto");
  if (target) {
    event.stopPropagation();
    setView(target);
  }
});
if (els.primaryFeed) {
  const placeholder = document.querySelector(".d-feed .primary-placeholder");
  els.primaryFeed.addEventListener("load", () => { els.primaryFeed.style.opacity = "1"; if (placeholder) placeholder.style.display = "none"; });
  els.primaryFeed.addEventListener("error", () => { els.primaryFeed.style.opacity = "0"; if (placeholder) placeholder.style.display = "grid"; });
}
if (els.replayFeed) {
  const replayPlaceholder = document.querySelector(".replay-stage .primary-placeholder");
  els.replayFeed.addEventListener("load", () => { els.replayFeed.style.opacity = "1"; if (replayPlaceholder) replayPlaceholder.style.display = "none"; });
  els.replayFeed.addEventListener("error", () => { els.replayFeed.style.opacity = "0"; if (replayPlaceholder) replayPlaceholder.style.display = "grid"; });
}
document.addEventListener("click", (event) => {
  if (!event.target.closest(".cam-role")) closeRoleMenus();
});

window.drs?.onDecision((decision) => renderDecision(decision));
window.addEventListener("keydown", (event) => {
  if (event.target.matches("input, textarea")) return;
  const key = event.key.toLowerCase();
  // "R" only requests from the dashboard while genuinely idle — mirror the button's
  // own guards (hidden/disabled) so a double-press during the in-flight POST or a
  // press from another view can't fire a second appeal.
  if (key === "r" && !state.activeAppeal && state.view === "dashboard"
      && els.requestReview && !els.requestReview.disabled && !els.requestReview.hidden) {
    requestReview();
  }
  if (key === "o" && state.activeAppeal) confirmDecision("OUT");
  if (key === "n" && state.activeAppeal) confirmDecision("NOT_OUT");
});

function applyEngineInfo(status) {
  if (status?.engine) { state.engineInfo = status.engine; renderSystemStatus(); }
  if (status?.testingPlatform?.status === "unavailable") {
    // Optional dev-tool status — log it; never hijack the Decision card's explanation.
    console.info("[DRS] Testing platform:", status.testingPlatform.message);
  }
}
window.drs?.onStartupStatus?.(applyEngineInfo);
// Also pull the current status on load (the push may have fired before we listened),
// so the Developer-Mode / synthetic-backend banner shows immediately.
window.drs?.getStartupStatus?.().then(applyEngineInfo).catch(() => {});

// initial UI state from persisted preferences
applySidebarState();
setOperatorMode(state.operatorMode);
setCalm(store.get("drs.calm", false));
buildReviewSelector();
loadReviewTypes();   // backend capability contracts refine/extend the static registry
applyReviewType();
renderDecisionState("WAITING");
renderQueue();
setView(state.view);

initDashboardModules();
if (state.view === "calibration") state.calibrationModal.activate();
connectWebSockets();
refreshHealth();
refreshSystemHealth();
refreshCameraStatus();
refreshPreflight();      // the dashboard's readiness line, before the 15s timer
refreshDecision();
// Resume the current match (name + review queue) — but never the active review,
// which the backend keeps at WAITING on launch.
loadCurrentMatch();
timers.health = setInterval(refreshHealth, 15000);
timers.system = setInterval(() => { refreshSystemHealth(); refreshPreflight(); }, 15000);
timers.cameras = setInterval(refreshCameraStatus, 10000);
timers.frames = setInterval(refreshCameraFrames, 1000);
timers.decision = setInterval(refreshDecision, 5000);
