import * as THREE from "../node_modules/three/build/three.module.js";
import { OrbitControls } from "./vendor/OrbitControls.js";
import { CalibrationWorkspace } from "./components/CalibrationModal.js";
import { DRSAnimationSequencer } from "./components/DRSAnimationSequencer.js";
import { ResultsPanel } from "./components/ResultsPanel.js";
import { StatusPanel } from "./components/StatusPanel.js";
import { TestingPanel } from "./components/TestingPanel.js";
import { ValidationPanel } from "./components/ValidationPanel.js";
import { ModelManagerPanel } from "./components/ModelManagerPanel.js";
import { ReviewPlayer } from "./overlay/overlay-renderer.js";
import { BroadcastReview } from "./components/BroadcastReview.js";
import { EvidencePanel } from "./components/EvidencePanel.js";
import { resultsToBroadcastDecision } from "./components/resultsToBroadcast.js";

const API_BASE = "http://localhost:8765";
const WS_BASE = "ws://localhost:8765";
const MAX_CAMERAS = 6;

/* ===================== config-driven review engine (registry) ===================== */
const REVIEW_MODULES = {
  lbw: {
    label: "LBW", role: "Ball Tracking", panel: "lbw",
    feedTitle: "Ball Tracking", detailTitle: "LBW Review", detailSub: "Ball-tracking trajectory",
    stages: ["Release", "Pitch", "Impact", "Prediction", "Decision"],
  },
  wide: {
    label: "Wide", role: "Wide Camera", panel: "wide",
    feedTitle: "Wide Camera", detailTitle: "Wide Review", detailSub: "Off / leg-side wide line",
    stages: ["Release", "Passing Batter", "Wide Line", "Decision"],
  },
  noball: {
    label: "No Ball", role: "Front Foot", panel: "noball",
    feedTitle: "Front Foot Camera", detailTitle: "Front Foot No Ball", detailSub: "Popping-crease overstep",
    stages: ["Release", "Landing", "Front Foot", "Decision"],
  },
  edge: {
    label: "Edge", role: "Stump Camera", panel: "edge",
    feedTitle: "Stump Camera", detailTitle: "Edge Review", detailSub: "UltraEdge + HotSpot",
    stages: ["Release", "Spike", "HotSpot", "Decision"],
  },
  runout: {
    label: "Run Out", role: "Stump", panel: "runout",
    feedTitle: "Run-Out Camera", detailTitle: "Run Out", detailSub: "Crease / bat / bails",
    stages: ["Appeal", "Crease", "Bat", "Bails", "Decision"],
  },
  stumping: {
    label: "Stumping", role: "Stump", panel: "stumping",
    feedTitle: "Stump Camera", detailTitle: "Stumping", detailSub: "Gloves / bails / bat position",
    stages: ["Appeal", "Gloves", "Bails", "Bat", "Decision"],
  },
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
  replayFrame: 0,
  scene: null,
  replayTimer: null,
  panelMode: "live",
  testingPanel: null,
  statusPanel: null,
  resultsPanel: null,
  animationSequencer: null,
  calibrationModal: null,
  activeVideoInfo: null,
  lastStatus: "WAITING",
  revealing: false,
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
  detailTitle: document.getElementById("detail-title"),
  detailSub: document.getElementById("detail-sub"),
  lbwTools: document.getElementById("lbw-tools"),
  decisionReviewType: document.getElementById("decision-review-type"),
  timelineType: document.getElementById("timeline-type"),
  badge: document.getElementById("decision-badge"),
  decisionState: document.getElementById("decision-state"),
  stateResult: document.getElementById("state-result"),
  title: document.getElementById("decision-title"),
  overlay: document.getElementById("broadcast-overlay"),
  overall: document.getElementById("overall-confidence"),
  impact: document.getElementById("impact-location"),
  wicket: document.getElementById("wicket-zone"),
  speed: document.getElementById("ball-speed"),
  explanation: document.getElementById("decision-explanation"),
  trajectoryStatus: document.getElementById("trajectory-status"),
  sceneHost: document.getElementById("trajectory-scene"),
  timeline: document.getElementById("decision-timeline"),
  confidenceBreakdown: document.getElementById("confidence-breakdown"),
  hotspotMode: document.getElementById("hotspot-mode"),
  hotspotView: document.getElementById("hotspot-view"),
  ultraedge: document.getElementById("ultraedge-canvas"),
  edgeProbability: document.getElementById("edge-probability"),
  edgeContact: document.getElementById("edge-contact"),
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
  confirmOut: document.getElementById("confirm-out"),
  confirmNotOut: document.getElementById("confirm-not-out"),
  exportReview: document.getElementById("export-review"),
  resetReview: document.getElementById("reset-review"),
  calibrationButton: document.getElementById("calibration-button"),
  resetCamera: document.getElementById("reset-camera"),
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
  wideOverlay: document.getElementById("wide-overlay"),
  noballOverlay: document.getElementById("noball-overlay"),
  wideVerdict: document.getElementById("wide-verdict"),
  wideDistance: document.getElementById("wide-distance"),
  wideDistance2: document.getElementById("wide-distance-2"),
  wideCentre: document.getElementById("wide-centre"),
  wideRadius: document.getElementById("wide-radius"),
  wideBatter: document.getElementById("wide-batter"),
  wideConfidence: document.getElementById("wide-confidence"),
  tvBall: document.getElementById("tv-ball"),
  tvDistLine: document.getElementById("tv-distline"),
  tvDistLabel: document.getElementById("tv-distlabel"),
  tvOverlap: document.getElementById("tv-overlap"),
  noballVerdict: document.getElementById("noball-verdict"),
  noballDistance: document.getElementById("noball-distance"),
  noballMeter: document.getElementById("noball-meter"),
  noballFoot: document.getElementById("noball-foot"),
  noballFootPos: document.getElementById("noball-foot-pos"),
  noballFootCm: document.getElementById("noball-foot-cm"),
  noballConfidence: document.getElementById("noball-confidence"),
  runoutVerdict: document.getElementById("runout-verdict"),
  runoutDistance: document.getElementById("runout-distance"),
  runoutMeter: document.getElementById("runout-meter"),
  runoutBatCrease: document.getElementById("runout-batcrease"),
  runoutFrame: document.getElementById("runout-frame"),
  runoutBails: document.getElementById("runout-bails"),
  runoutConfidence: document.getElementById("runout-confidence"),
  stumpingVerdict: document.getElementById("stumping-verdict"),
  stumpingDistance: document.getElementById("stumping-distance"),
  stumpingMeter: document.getElementById("stumping-meter"),
  stumpingGloves: document.getElementById("stumping-gloves"),
  stumpingBatCrease: document.getElementById("stumping-batcrease"),
  stumpingFrame: document.getElementById("stumping-frame"),
  stumpingBails: document.getElementById("stumping-bails"),
  // review summary
  rsType: document.getElementById("rs-type"),
  rsPrediction: document.getElementById("rs-prediction"),
  rsConfidence: document.getElementById("rs-confidence"),
  rsTime: document.getElementById("rs-time"),
  rsTrajectory: document.getElementById("rs-trajectory"),
  rsEdge: document.getElementById("rs-edge"),
  rsHotspot: document.getElementById("rs-hotspot"),
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
  if (view === "dashboard") requestAnimationFrame(resizeThree);
  if (view === "testing") ensureTestingPanel();
  if (view === "validation") ensureValidationPanel();
  if (view === "models") ensureModelManagerPanel();
  if (view === "development") refreshAiDevelopmentStatus();
  if (view === "checklist") refreshPreflight();
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
  // Match Mode hides the engineer evidence views (SVG animation / 3D) — if the
  // operator was parked on one, snap back to the primary pipeline replay.
  if (!engineer && (state.lbwView === "broadcast" || state.lbwView === "3d")) {
    document.getElementById("lbw-observed-btn")?.click();
  }
  // Re-render the review summary so diagnostic rows appear/disappear immediately.
  if (state.decision) renderReviewSummary(state.decision);
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
  setTimeout(resizeThree, 300);
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
  document.querySelectorAll(".review-panel").forEach((panel) => { panel.hidden = panel.dataset.reviewPanel !== type; });
  if (els.wideOverlay) els.wideOverlay.hidden = type !== "wide";
  if (els.noballOverlay) els.noballOverlay.hidden = type !== "noball";
  if (els.lbwTools) els.lbwTools.hidden = type !== "lbw";
  if (els.feedTitle) els.feedTitle.textContent = mod.feedTitle;
  if (els.feedSub) els.feedSub.textContent = `${mod.label} primary feed`;
  if (els.detailTitle) els.detailTitle.textContent = mod.detailTitle;
  if (els.detailSub) els.detailSub.textContent = mod.detailSub;
  if (els.decisionReviewType) els.decisionReviewType.textContent = mod.label;
  if (els.timelineType) els.timelineType.textContent = `${mod.label} sequence`;
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
  renderTimeline();
  renderReviewPanels(state.decision || {});
  if (state.evidencePanel) state.evidencePanel.update(type, state.decision || {});
  if (type === "lbw" && state.view === "dashboard") requestAnimationFrame(resizeThree);
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
  if (els.hotspotMode) els.hotspotMode.textContent = state.mode.id === "thermal_demo" ? "Demo overlay - simulated" : "Visible-spectrum approximation";
  if (els.hotspotView) {
    els.hotspotView.textContent = state.mode.id === "thermal_demo"
      ? "Presentation heat colors are simulated and explicitly not real thermal data."
      : "Mode A uses frame differencing and motion-energy approximation.";
    els.hotspotView.classList.toggle("thermal", state.mode.id === "thermal_demo");
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
  // Suppress the poll during the confirm→reset handoff and the staged reveal, so
  // a GET dispatched just before the reset can't resolve late and resurrect the
  // verdict (re-firing the whole reveal). The explicit renders drive those phases.
  if (state.confirmHold || state.revealing) return;
  try {
    const decision = await jsonFetch("/api/decision/current");
    renderDecision(decision);
  } catch {}
}

function renderDecision(decision) {
  state.decision = decision;
  const status = decision.status || "WAITING";
  state.activeAppeal = status !== "WAITING";
  const justResolved = status !== "WAITING" && state.lastStatus === "WAITING";
  const nowResolved = status === "OUT" || status === "NOT_OUT";
  const wasResolved = state.lastStatus === "OUT" || state.lastStatus === "NOT_OUT";
  if (nowResolved && !wasResolved) {
    state.reviewElapsed = state.reviewStartMs ? (Date.now() - state.reviewStartMs) / 1000 : null;
  }
  state.lastStatus = status;
  if (justResolved && !state.revealing) {
    playDecisionReveal(status, decision);
  } else if (!state.revealing) {
    els.badge.className = `badge ${statusClass(status)}`;
    els.badge.textContent = displayStatus(status);
    els.title.textContent = decision.outcome || statusText(status);
    els.overlay.className = `broadcast-overlay ${statusClass(status)}`;
    els.overlay.textContent = status === "PROCESSING" ? "REVIEWING" : broadcastText(status, decision);
    renderDecisionState(status);
  }
  if (nowResolved && !wasResolved) resolveQueue(status);
  els.overall.textContent = pct(decision.overall_confidence ?? decision.ball_confidence);
  if (els.kpiModel) els.kpiModel.textContent = pct(decision.overall_confidence ?? decision.ball_confidence);
  els.impact.textContent = formatPoint(decision.impact_marker || decision.impact_point);
  els.wicket.textContent = decision.wicket_zone_status || "--";
  els.speed.textContent = decision.ball_speed_kmh ? `${Number(decision.ball_speed_kmh).toFixed(1)} km/h` : "--";
  els.explanation.textContent = decision.explanation || "Awaiting appeal sequence.";
  if (els.trajectoryStatus) { const n = trajectoryPoints(decision.trajectory).length; els.trajectoryStatus.textContent = n ? `${n} tracked points` : "Waiting for review data"; }
  if (!state.revealing) renderTimeline();
  renderConfidence(decision);
  renderReviewPanels(decision);
  renderReviewSummary(decision);
  updateTrajectory(decision);
  if (state.broadcastReview) state.broadcastReview.update(decision);
  if (state.evidencePanel) state.evidencePanel.update(state.reviewType, decision);
  if (state.view === "replay") applyReplayMode();
  drawUltraEdge(decision);
  renderHotspot(decision);
  if (ReviewMode.active) ReviewMode.update(decision);
}

/* decision state machine + state-driven layout + contextual buttons (items 1, 5) */
function setReviewLayoutState(phase, status) {
  if (els.dashGrid) els.dashGrid.dataset.state = phase;
  if (els.rtState) {
    els.rtState.textContent = phase === "result" ? displayStatus(status || state.lastStatus)
      : phase === "processing" ? "Reviewing" : "Waiting";
  }
  requestAnimationFrame(resizeThree);
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
  const hosts = canonicalHosts();
  if (!hosts.observed && !hosts.clean) return;
  state.canonical = null;
  syncCanonicalSurfaces();                                 // clear the badges for the new appeal
  const jobId = decision?.canonical_job_id;
  if (!jobId) {
    // honesty rule: name the reason, never fabricate a replay
    const why = decision?.canonical_skip_reason
      ? `No DRS replay — ${decision.canonical_skip_reason}`
      : "No pipeline replay for this review type.";
    setCanonicalNotes(`<div class="cr-note quiet">${why}</div>`);
    if (ReviewMode.active) {
      ["rm-observed", "rm-broadcast"].forEach((id) => { const b = document.getElementById(id); if (b) b.innerHTML = `<div class="rm2-noreplay">${why}</div>`; });
      ReviewMode.setStep("bt", "failed");
    }
    return;
  }
  _canonicalJobWatching = jobId;
  setCanonicalNotes(`<div class="cr-note">DRS analysis running… (ball tracking + replay render)</div>`);
  setCanonicalChip("Replay rendering …", false);
  const started = Date.now();
  const poll = async () => {
    if (_canonicalJobWatching !== jobId) return;           // superseded by a newer appeal
    if (Date.now() - started > 5 * 60 * 1000) {
      setCanonicalNotes(`<div class="cr-note">DRS analysis timed out — check the backend log.</div>`);
      return;
    }
    try {
      const res = await fetch(`${API_BASE}/api/analyze/${jobId}/results`);
      if (!res.ok) {
        // Still processing — show the job's REAL progress (percent + step) in both
        // evidence tabs and the Review Mode chip, instead of a static note.
        try {
          const st = await fetch(`${API_BASE}/api/analyze/${jobId}/status`).then((r) => r.json());
          if (_canonicalJobWatching === jobId) {
            const pct = Number(st.progress ?? st.percent ?? 0);
            const step = st.current_step || st.step || "processing";
            setCanonicalNotes(`<div class="cr-note">DRS analysis ${pct ? `${pct}% — ` : "running… "}${step}</div>`);
            setCanonicalChip(`Replay rendering ${pct ? `${pct}%` : "…"}`, false);
            if (ReviewMode.active) ReviewMode.renderPending(`Rendering replay… ${pct ? pct + "%" : ""}`);
          }
        } catch { /* status endpoint unavailable — keep the previous note */ }
        setTimeout(poll, 3000); return;
      }
      const results = await res.json();
      state.canonical = { jobId, results };
      if (hosts.observed) renderCanonicalReview(hosts.observed, jobId, results, "players");
      if (hosts.clean) renderCanonicalReview(hosts.clean, jobId, results, "review");
      if (ReviewMode.active) ReviewMode.setCanonical(jobId, results);   // the single review workspace
      syncCanonicalSurfaces();                             // badge the tabs + mirror into Replay
    } catch { setTimeout(poll, 3000); }
  };
  setTimeout(poll, 3000);
}

// The two pipeline replays live in their OWN primary tabs: Observed Trajectory
// (#canonical-observed, tracked footage) and Broadcast Replay (#canonical-review,
// clean DRS render). Both hosts get identical pending/failure notes so whichever
// tab the operator is on tells the truth.
function canonicalHosts() {
  return {
    observed: document.getElementById("canonical-observed"),
    clean: document.getElementById("canonical-review"),
  };
}

function setCanonicalNotes(html) {
  const { observed, clean } = canonicalHosts();
  if (observed) observed.innerHTML = html;
  if (clean) clean.innerHTML = html;
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

// Mirror the canonical replays into the Replay workspace (LBW mode) and badge the
// LBW "DRS Replay" toggle so the operator knows the videos are ready.
function syncCanonicalSurfaces() {
  const replayHost = document.getElementById("replay-canonical");
  const c = state.canonical;
  const ready = Boolean(c && c.results && (c.results.exports || {}).replay_players);
  document.getElementById("lbw-observed-btn")?.classList.toggle("ready", ready);
  document.getElementById("lbw-clean-btn")?.classList.toggle("ready", ready && Boolean((c.results.exports || {}).replay_review));
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
  // The Ball-Tracking stage depends on the async gates — refresh the Review Mode
  // protocol from the single state machine now that canonical results have landed.
  if (ReviewMode.active) ReviewMode.renderProtocol();
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
  els.decisionState.querySelectorAll(".state-node").forEach((node) => {
    const st = node.dataset.state;
    node.className = "state-node";
    if (phase === "waiting" && st === "waiting") node.classList.add("active");
    else if (phase === "processing") {
      if (st === "waiting") node.classList.add("done");
      if (st === "processing") node.classList.add("active");
    } else if (phase === "result") {
      if (st === "waiting" || st === "processing") node.classList.add("done");
      if (st === "result") node.classList.add("result", statusClass(status));
    }
  });
  els.stateResult.textContent = phase === "result" ? displayStatus(status) : "DECISION";
  // Contextual buttons. Confirm is available once the system is reviewing (so the
  // umpire can call an inconclusive review), but everything is hidden mid-reveal.
  const reviewing = state.revealing;
  els.requestReview.hidden = reviewing || phase !== "waiting";
  els.confirmOut.hidden = reviewing || phase === "waiting";
  els.confirmNotOut.hidden = reviewing || phase === "waiting";
  els.exportReview.hidden = reviewing || phase === "waiting";
  els.resetReview.hidden = reviewing || phase === "waiting";
  els.confirmOut.disabled = false;
  els.confirmNotOut.disabled = false;
}

// Adaptive timeline driven by the active module's stages (item 10)
function renderTimeline() {
  const stages = REVIEW_MODULES[state.reviewType].stages;
  const status = state.lastStatus;
  const resolved = status === "OUT" || status === "NOT_OUT";
  const processing = status === "PROCESSING";
  const source = stages.map((label, index) => {
    let st = "pending";
    if (resolved) st = "complete";
    else if (processing) st = index < stages.length - 1 ? "complete" : "active";
    else if (state.activeAppeal && index === 0) st = "active";
    return { label, status: st };
  });
  els.timeline.innerHTML = source.map((item) => `
    <div class="timeline-row ${item.status}">
      <i></i><span>${item.label}</span>
    </div>
  `).join("");
}

function paintTimelineProgress(activeIndex) {
  els.timeline.querySelectorAll(".timeline-row").forEach((row, index) => {
    row.className = "timeline-row " + (index < activeIndex ? "complete" : index === activeIndex ? "active" : "pending");
  });
}

function renderConfidence(decision) {
  const rows = [
    ["Ball detection", decision.ball_confidence],
    ["Tracking", decision.tracking_confidence],
    ["Calibration", decision.calibration_confidence],
    ["Prediction", decision.prediction_confidence],
    ["Model", decision.model_confidence],
  ];
  els.confidenceBreakdown.innerHTML = rows.map(([label, value]) => `
    <div class="confidence-row">
      <span>${label}</span><strong>${pct(value)}</strong>
      <div><i style="width:${Math.round(Number(value || 0) * 100)}%"></i></div>
    </div>
  `).join("");
}

// ── LBW protocol state machine — the SINGLE source of truth ──────────────────
// Every stage is exactly one of: "waiting" | "processing" | "passed" | "failed".
// The Review Mode UI is driven ONLY from this object, so the display is
// deterministic — never inferred ad hoc from whichever backend field arrives
// first (which is what made steps read "waiting" while actually processing).
//   waiting    — not started yet
//   processing — the review is live but this stage's result hasn't arrived
//   passed     — the check completed successfully
//   failed     — the check completed but couldn't run (no camera / mic / track)
const PROTOCOL_STAGES = ["frontFoot", "ultraEdge", "ballTracking"];

function computeProtocol(decision) {
  decision = decision || {};
  const status = decision.status || state.lastStatus || "WAITING";
  // The review is "live" whenever Review Mode is open — not only when the last
  // polled status says PROCESSING. During the appeal round-trip the poll can still
  // report the stale pre-appeal WAITING decision; without this, pending stages
  // would wrongly read "waiting" while the review is actually running.
  const reviewLive = typeof ReviewMode !== "undefined" && ReviewMode.active;
  const live = reviewLive || status === "PROCESSING" || status === "OUT" || status === "NOT_OUT";
  const pending = live ? "processing" : "waiting";

  // ① Front Foot — front-foot no-ball check
  const nb = decision.no_ball_analysis || decision.noball || {};
  let frontFoot;
  if (nb.is_no_ball === true || nb.is_no_ball === false) frontFoot = "passed";
  else if (nb.reason && /no .*camera|not available|unavailable|not detected/i.test(nb.reason)) frontFoot = "failed";
  else frontFoot = pending;

  // ② UltraEdge — stump-mic audio analysis
  const edge = decision.edge_analysis || {};
  let ultraEdge;
  if (edge.available === false) ultraEdge = "failed";
  else if (edge.available === true || (edge.events && edge.events.length) || edge.inconclusive != null || edge.edge_probability != null) ultraEdge = "passed";
  else ultraEdge = pending;

  // ③ Ball Tracking — the async trajectory/replay pipeline (canonical job)
  let ballTracking;
  const canon = state.canonical;
  if (decision.canonical_skip_reason) ballTracking = "failed";
  else if (canon && canon.results) {
    const g = canon.results.reconstruction && canon.results.reconstruction.gates;
    const hasReplay = Boolean((canon.results.exports || {}).replay_players);
    ballTracking = (g || hasReplay) ? "passed" : "failed";
  } else if (decision.canonical_job_id || (canon && !canon.results)) {
    ballTracking = "processing";     // job created / trajectory rendering
  } else ballTracking = pending;

  return { frontFoot, ultraEdge, ballTracking };
}

// Review summary shown on result (commercial-style record)
function renderReviewSummary(decision) {
  const status = decision.status || state.lastStatus;
  const resolved = status === "OUT" || status === "NOT_OUT";
  const mod = REVIEW_MODULES[state.reviewType] || {};
  if (els.rsType) els.rsType.textContent = mod.label || "--";
  if (els.rsPrediction) els.rsPrediction.textContent = resolved ? displayStatus(status) : "--";
  if (els.rsConfidence) els.rsConfidence.textContent = pct(decision.overall_confidence ?? decision.ball_confidence);
  if (els.rsTime) els.rsTime.textContent = state.reviewElapsed != null ? `${state.reviewElapsed.toFixed(1)} sec` : "--";

  // Per-type rows: the module's typed measurements when the analysis has run
  // (Wide → "Outside 18.3 cm", Edge → "Edge probability 4%", Run Out → "Bat to
  // crease 12.0 cm"), else the contract's decision-card labels as "--" placeholders.
  // No review type ever sees another type's fields.
  const grid = document.getElementById("rs-grid");
  if (!grid) return;
  grid.querySelectorAll("[data-rs-dynamic]").forEach((n) => n.remove());
  const rr = decision.review_result || {};
  const measurements = Array.isArray(rr.measurements) && rr.measurements.length
    ? rr.measurements
    : (mod.decisionCard || []).filter((label) => label !== "Decision").map((label) => ({ label, value: "--" }));
  // Match Mode shows only decision-relevant rows — tracking diagnostics (detection
  // rate, frames tracked, confidence, model) are engineering detail → Engineer Mode.
  const DIAGNOSTIC_ROWS = /detection rate|frames? tracked|confidence|model|fps|tracking/i;
  const engineerMode = state.operatorMode === "engineer";
  const visibleMeasurements = engineerMode ? measurements : measurements.filter((m) => !DIAGNOSTIC_ROWS.test(m.label || ""));
  for (const m of visibleMeasurements.slice(0, 6)) {
    const row = document.createElement("div");
    row.dataset.rsDynamic = "1";
    const labelEl = document.createElement("span");
    labelEl.textContent = m.label;
    const valueEl = document.createElement("strong");
    valueEl.textContent = m.value ?? "--";
    if (m.flag) valueEl.classList.add("rs-flag");
    row.append(labelEl, valueEl);
    grid.appendChild(row);
  }
  // The unified verdict (WIDE / NO BALL / EDGE / OUT…) is richer than the
  // OUT/NOT_OUT status vocabulary — prefer it while the review is unresolved.
  if (rr.verdict && els.rsPrediction && !resolved && rr.verdict !== "AWAITING") {
    els.rsPrediction.textContent = rr.verdict;
  }
}

/* ----- per-review-type detail panels (items 6, 7) ----- */
function renderReviewPanels(decision) {
  renderWideReview(decision.wide_analysis || decision.wide || {});
  renderNoBallReview(decision.no_ball_analysis || decision.noball || {});
  renderEdgeReview(decision.edge_analysis || {});
  renderRunOutReview(decision.run_out_analysis || {});
  renderStumpingReview(decision.stumping_analysis || {});
}

// Shared crease-distance painter for the two crease reviews (Run Out / Stumping):
// >0 cm = grounded behind the crease (SAFE), <0 = short of the line (OUT).
function paintCreaseReview(analysis, ui, safeWord, outWord) {
  const hasData = analysis.distance_cm != null || analysis.is_out != null;
  const cm = Number(analysis.distance_cm);
  const distance = Number.isFinite(cm) ? `${Math.abs(cm).toFixed(1)} cm` : "--";
  if (ui.distance) ui.distance.textContent = distance;
  if (ui.batCrease) ui.batCrease.textContent = Number.isFinite(cm)
    ? `${Math.abs(cm).toFixed(1)} cm ${cm < 0 ? "short" : "behind"}` : "--";
  if (ui.frame) ui.frame.textContent = analysis.frame_number != null ? `#${analysis.frame_number}` : "--";
  if (ui.bails) ui.bails.textContent = analysis.bails_status ? String(analysis.bails_status).replace(/_/g, " ") : "--";
  if (ui.confidence) ui.confidence.textContent = pct(analysis.confidence);
  if (ui.meter) {
    const fill = Number.isFinite(cm) ? Math.max(2, Math.min(100, 50 + cm * 2)) : 50;
    ui.meter.style.width = `${fill}%`;
    ui.meter.classList.toggle("over", Boolean(analysis.is_out));
  }
  if (ui.verdict) {
    if (!hasData) { ui.verdict.textContent = "AWAITING"; ui.verdict.className = "big-verdict waiting"; }
    else if (analysis.is_out) { ui.verdict.textContent = outWord; ui.verdict.className = "big-verdict out"; }
    else { ui.verdict.textContent = safeWord; ui.verdict.className = "big-verdict not-out"; }
  }
}

function renderRunOutReview(analysis) {
  paintCreaseReview(analysis, {
    verdict: els.runoutVerdict, distance: els.runoutDistance, meter: els.runoutMeter,
    batCrease: els.runoutBatCrease, frame: els.runoutFrame, bails: els.runoutBails,
    confidence: els.runoutConfidence,
  }, "NOT OUT", "OUT");
}

function renderStumpingReview(analysis) {
  paintCreaseReview(analysis, {
    verdict: els.stumpingVerdict, distance: els.stumpingDistance, meter: els.stumpingMeter,
    batCrease: els.stumpingBatCrease, frame: els.stumpingFrame, bails: els.stumpingBails,
  }, "NOT OUT", "OUT");
  if (els.stumpingGloves) {
    els.stumpingGloves.textContent = analysis.gloves_detected == null
      ? "Manual check" : (analysis.gloves_detected ? "Detected" : "Not detected");
  }
}

function renderWideReview(wide) {
  const hasData = wide.distance_cm != null || wide.is_wide != null;
  const distance = wide.distance_cm != null ? `${Number(wide.distance_cm).toFixed(1)} cm` : "--";
  if (els.wideDistance) els.wideDistance.textContent = distance;
  if (els.wideDistance2) els.wideDistance2.textContent = distance;
  if (els.wideCentre) els.wideCentre.textContent = formatPoint(wide.ball_centre);
  if (els.wideRadius) els.wideRadius.textContent = wide.ball_radius_px != null ? `${Number(wide.ball_radius_px).toFixed(0)} px` : "--";
  if (els.wideBatter) els.wideBatter.textContent = wide.batter_movement || "--";
  if (els.wideConfidence) els.wideConfidence.textContent = pct(wide.confidence);
  if (els.tvBall && els.tvDistLine && els.tvDistLabel) {
    const cm = Number(wide.distance_cm);
    const ballX = Number.isFinite(cm) ? Math.max(42, Math.min(120, 40 + cm * 1.6)) : 70;
    els.tvBall.setAttribute("cx", String(ballX));
    els.tvDistLine.setAttribute("x2", String(ballX));
    els.tvDistLabel.textContent = Number.isFinite(cm) ? `${cm.toFixed(1)} cm` : "--";
    if (els.tvOverlap) els.tvOverlap.setAttribute("width", String(Math.max(0, ballX - 40)));
  }
  if (els.wideVerdict) {
    if (!hasData) { els.wideVerdict.textContent = "AWAITING"; els.wideVerdict.className = "big-verdict waiting"; }
    else if (wide.is_wide) { els.wideVerdict.textContent = "WIDE"; els.wideVerdict.className = "big-verdict out"; }
    else { els.wideVerdict.textContent = "NOT WIDE"; els.wideVerdict.className = "big-verdict not-out"; }
  }
}

function renderNoBallReview(noball) {
  const hasData = noball.distance_past_cm != null || noball.is_no_ball != null;
  const distance = noball.distance_past_cm != null ? `${Number(noball.distance_past_cm).toFixed(1)} cm` : "--";
  if (els.noballDistance) els.noballDistance.textContent = distance;
  if (els.noballFootCm) els.noballFootCm.textContent = distance;
  if (els.noballFootPos) els.noballFootPos.textContent = noball.foot_position || (hasData ? (noball.is_no_ball ? "Past line" : "Behind line") : "--");
  if (els.noballConfidence) els.noballConfidence.textContent = pct(noball.confidence);
  if (els.noballMeter) {
    const cm = Number(noball.distance_past_cm || 0);
    const pctFill = Math.max(2, Math.min(100, 50 + cm * 2));
    els.noballMeter.style.width = `${pctFill}%`;
    els.noballMeter.classList.toggle("over", Boolean(noball.is_no_ball));
  }
  if (els.noballFoot) els.noballFoot.classList.toggle("flagged", Boolean(noball.is_no_ball));
  if (els.noballVerdict) {
    if (!hasData) { els.noballVerdict.textContent = "AWAITING"; els.noballVerdict.className = "big-verdict waiting"; }
    else if (noball.is_no_ball) { els.noballVerdict.textContent = "NO BALL"; els.noballVerdict.className = "big-verdict out"; }
    else { els.noballVerdict.textContent = "LEGAL"; els.noballVerdict.className = "big-verdict not-out"; }
  }
}

function renderEdgeReview(edge) {
  if (els.edgeProbability) els.edgeProbability.textContent = pct(edge.edge_probability);
  if (els.edgeContact) els.edgeContact.textContent = (edge.events || []).length ? "Detected" : (edge.edge_probability != null ? "None" : "--");
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
  if (entry) { entry.status = "Completed"; entry.verdict = displayStatus(status); }
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
  state.resultsPanel = new ResultsPanel(els);
  state.animationSequencer = new DRSAnimationSequencer({
    overlay: els.overlay,
    title: els.title,
    frameLabel: els.frameLabel,
    replayTimeline: els.frameTimeline,
  });
  state.calibrationModal = new CalibrationWorkspace(els.calibrationRoot, {
    onRoleChange: (cameraId, role) => setCameraRole(cameraId, role),
    getRole: (cameraId) => cameraRoleFor(cameraId),
  });
  const broadcastHost = document.getElementById("broadcast-review");
  if (broadcastHost) state.broadcastReview = new BroadcastReview(broadcastHost);
  const evidenceHost = document.getElementById("decision-evidence");
  if (evidenceHost) state.evidencePanel = new EvidencePanel(evidenceHost);
  setupLbwViewToggle();
  // Fullscreen on every camera/replay stage (Review ball-tracking feed + Replay Workspace).
  document.querySelectorAll(".live-stage").forEach((stage) => addFullscreenButton(stage));
}

// Toggle between the technical 3D trajectory and the broadcast replay inside the
// LBW Review card. Both share the same decision data; neither is removed.
function setupLbwViewToggle() {
  const toggle = document.getElementById("lbw-view-toggle");
  if (!toggle) return;
  // Primary evidence = the pipeline replays; the SVG animation and 3D scene are
  // engineer-only views and never the default.
  state.lbwView = "observed";
  toggle.querySelectorAll("button[data-lbw-view]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const view = btn.dataset.lbwView;
      state.lbwView = view;
      toggle.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === btn));
      document.querySelectorAll('.review-panel[data-review-panel="lbw"] .lbw-view').forEach((panel) => {
        panel.hidden = panel.dataset.lbwView !== view;
      });
      if (view === "3d") requestAnimationFrame(resizeThree);
      else if (view === "broadcast" && state.broadcastReview && state.decision) state.broadcastReview.play();
    });
  });
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

function renderAnalysisResults(results) {
  state.panelMode = "results";
  state.statusPanel.summary(results);
  state.resultsPanel.render(results);
  const decision = dashboardDecisionFromResults(results);
  renderDecision(decision);
  state.animationSequencer.play(results);
}

function dashboardDecisionFromResults(results) {
  const verdict = results.decision?.verdict || "UMPIRES_CALL";
  // BroadcastReview's input comes from the ONE shared mapper (resultsToBroadcastDecision),
  // so the broadcast card is fed the IDENTICAL object the Testing page uses — same
  // canonical trajectory, same pitching/impact/wickets gate statuses. The dashboard then
  // layers on the extra fields ONLY its other widgets need (3D view, confidence
  // breakdown, badge copy), and keeps its own status vocabulary (umpire's call ->
  // PROCESSING) for the badge/overlay state machine.
  const points = results.trajectory?.points || [];
  return {
    ...resultsToBroadcastDecision(results),
    status: verdict === "NOT_OUT" ? "NOT_OUT" : verdict === "OUT" ? "OUT" : "PROCESSING",
    outcome: verdict.replace("_", " "),
    ball_confidence: results.ball_tracking?.avg_confidence,
    tracking_confidence: results.ball_tracking?.detection_rate,
    calibration_confidence: results.lbw_gates?.pitching?.confidence,
    prediction_confidence: results.lbw_gates?.wickets?.confidence,
    model_confidence: results.ball_tracking?.avg_confidence,
    impact_marker: results.trajectory?.impact_point,
    wicket_zone_status: results.lbw_gates?.wickets?.result || "--",
    predicted_extension: points.slice(-3),
    wicket_prediction: results.trajectory?.predicted_stumps,
    explanation: results.decision?.explanation,
  };
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
  // Enter Review Mode IMMEDIATELY so the operator gets instant feedback (freeze +
  // "Reviewing"), then replay the full animation once the decision arrives.
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
  try {
    const response = window.drs?.requestReview
      ? await window.drs.requestReview(payload)
      : await jsonFetch("/api/appeal/request", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
    const decision = response.decision || response;
    renderDecision(decision);
    ReviewMode.play(decision);   // real overlay + verdict → replay the animation
    watchCanonicalReview(decision);   // canonical pipeline replays (same jobs as Testing)
  } catch (err) {
    ReviewMode.exit();
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
  state.reviewStartMs = null;
  state.reviewElapsed = null;
  state.revealing = false;
  updateDevelopmentGuard();
  clearInterval(state.revealTimer);
  if (ReviewMode.active) ReviewMode.exit();
}

/* ============================ REVIEW MODE ============================ */
// The single review workspace: one screen from Request Review to Decision.
// Top = protocol progress (① Front Foot ② UltraEdge ③ Ball Tracking), middle =
// both pipeline replays always visible, bottom = gates + verdict + decision.
// No separate replay page, no tabs, no hidden evidence.
const ReviewMode = {
  el: null, active: false, jobId: null,
  ensure() {
    if (this.el) return;
    this.el = document.getElementById("review-mode");
    // Back to Dashboard = cancel this review cleanly (reset the backend to WAITING),
    // never leave a zombie review running that blocks the next Request Review.
    document.getElementById("rm-back").addEventListener("click", () => { if (this.active) resetReview(); });
    document.getElementById("rm-confirm-out").addEventListener("click", () => confirmDecision("OUT"));
    document.getElementById("rm-confirm-not-out").addEventListener("click", () => confirmDecision("NOT_OUT"));
  },
  setStep(key, cls) {
    const el = this.el && this.el.querySelector(`#rm-steps .rm2-step[data-step="${key}"]`);
    if (el) el.className = `rm2-step ${cls}`;
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
      const t = results.trajectory || {};
      const why = t.valid === false
        ? `trajectory rejected: ${(t.reasons || []).join("; ") || t.observed?.end_reason || "invalid"}`
        : "replay not available for this delivery";
      box.innerHTML = `<div class="rm2-noreplay">No replay — ${why}</div>`;
      return;
    }
    box.innerHTML = `<video muted playsinline controls autoplay loop src="${API_BASE}/api/testing/jobs/${jobId}/exports/${key}"></video>`;
  },
  // Drive the three step chips ENTIRELY from the protocol state machine — the one
  // source of truth. Maps {frontFoot,ultraEdge,ballTracking} → the ff/ue/bt chips.
  renderProtocol(decision) {
    // The 3-step protocol + gates are LBW-specific — only LBW drives them.
    if (state.reviewType !== "lbw") return;
    const p = computeProtocol(decision || state.decision || {});
    this.setStep("ff", p.frontFoot);
    this.setStep("ue", p.ultraEdge);
    this.setStep("bt", p.ballTracking);
  },
  // Called when the canonical job's results land (from watchCanonicalReview).
  setCanonical(jobId, results) {
    if (!this.active || !results) return;
    this.renderVideo("rm-observed", jobId, results, "replay_players");
    this.renderVideo("rm-broadcast", jobId, results, "replay_review");
    const g = results.reconstruction && results.reconstruction.gates;
    document.getElementById("rm-gate-pitching").textContent = (g && g.pitching) || "—";
    document.getElementById("rm-gate-impact").textContent = (g && g.impact) || "—";
    document.getElementById("rm-gate-wickets").textContent = (g && g.wickets) || "—";
    this.renderProtocol();   // ball-tracking stage → passed/failed per the state machine
  },
  enter(decision) {
    this.ensure();
    this.active = true;
    this.jobId = null;
    document.body.classList.add("review-active");
    this.el.classList.add("open");
    this.el.setAttribute("aria-hidden", "false");
    const mod = REVIEW_MODULES[decision.review_type || state.reviewType];
    document.getElementById("rm-type").textContent = `${(mod && mod.label) || "Review"} REVIEW`.toUpperCase();
    // Protocol steps + ball-tracking gates are LBW-only; hide them for other types.
    const isLbw = (decision.review_type || state.reviewType) === "lbw";
    const steps = this.el.querySelector("#rm-steps");
    const gates = this.el.querySelector(".rm2-gates");
    if (steps) steps.hidden = !isLbw;
    if (gates) gates.hidden = !isLbw;
    document.getElementById("rm-gate-pitching").textContent = "—";
    document.getElementById("rm-gate-impact").textContent = "—";
    document.getElementById("rm-gate-wickets").textContent = "—";
    this.renderPending("Rendering replay…");
    this.update(decision);
  },
  // Kept for call-site compatibility (requestReview): real decision arrived.
  play(decision) { if (!this.active) return this.enter(decision); this.update(decision); },
  update(decision) {
    if (!this.active) return;
    this.renderProtocol(decision);
    const status = decision.status || "PROCESSING";
    const rr = decision.review_result || {};
    const resolved = status === "OUT" || status === "NOT_OUT";
    const v = document.getElementById("rm-verdict");
    v.className = "rm-verdict " + (status === "OUT" ? "out" : status === "NOT_OUT" ? "not-out" : "reviewing");
    v.textContent = resolved ? displayStatus(status) : (rr.verdict && rr.verdict !== "AWAITING" ? rr.verdict : "REVIEWING");
    document.getElementById("rm-confirm-out").hidden = resolved;
    document.getElementById("rm-confirm-not-out").hidden = resolved;
  },
  exit() {
    this.active = false;
    this.jobId = null;
    document.body.classList.remove("review-active");
    if (this.el) {
      this.el.classList.remove("open");
      this.el.setAttribute("aria-hidden", "true");
      // Release any playing <video> so decoders/timers don't linger after close.
      this.el.querySelectorAll("video").forEach((v) => { try { v.pause(); v.removeAttribute("src"); v.load(); } catch {} });
    }
  },
};
window.__reviewMode = ReviewMode;

function initThree() {
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x07100d);
  const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100);
  camera.position.set(8, 7, 12);
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  els.sceneHost.appendChild(renderer.domElement);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.target.set(0, 0, 0.55);

  scene.add(new THREE.HemisphereLight(0xffffff, 0x163126, 1.4));
  const key = new THREE.DirectionalLight(0xffffff, 1.8);
  key.position.set(-4, 8, 6);
  scene.add(key);
  buildPitch(scene);

  state.scene = { scene, camera, renderer, controls, dynamic: new THREE.Group() };
  scene.add(state.scene.dynamic);
  resizeThree();
  renderer.setAnimationLoop(() => {
    controls.update();
    renderer.render(scene, camera);
  });
}

function buildPitch(scene) {
  const pitch = new THREE.Mesh(
    new THREE.BoxGeometry(20.12, 3.05, 0.04),
    new THREE.MeshStandardMaterial({ color: 0x8f7d55, roughness: 0.8 })
  );
  pitch.position.z = -0.02;
  scene.add(pitch);
  const turf = new THREE.GridHelper(24, 24, 0x2a6b49, 0x204532);
  turf.rotation.x = Math.PI / 2;
  turf.position.z = -0.04;
  scene.add(turf);
  const stumpMaterial = new THREE.MeshStandardMaterial({ color: 0xf2e6bd });
  [-0.23, 0, 0.23].forEach((y) => {
    const stump = new THREE.Mesh(new THREE.CylinderGeometry(0.035, 0.035, 0.72, 16), stumpMaterial);
    stump.rotation.x = Math.PI / 2;
    stump.position.set(7.1, y, 0.36);
    scene.add(stump);
  });
}

function updateTrajectory(decision) {
  if (!state.scene) return;
  const group = state.scene.dynamic;
  group.clear();
  const points = normalizeTrajectory(trajectoryPoints(decision.trajectory));
  if (points.length > 1) {
    const geometry = new THREE.BufferGeometry().setFromPoints(points);
    group.add(new THREE.Line(geometry, new THREE.LineBasicMaterial({ color: 0xf8f7ef, linewidth: 4 })));
    addTube(group, points, 0x42d895, 0.035);
    addConfidenceVolumes(group, points, decision);
  }
  addMarker(group, normalizePoint(decision.bounce_point), 0xffd45c, "bounce");
  addMarker(group, normalizePoint(decision.impact_marker || decision.impact_point), 0xe24b4a, "impact");
  const predicted = normalizeTrajectory(decision.predicted_extension || []);
  if (predicted.length > 1) addTube(group, predicted, 0x37b7d8, 0.025);
  addWicketPrediction(group, decision.wicket_prediction);
}

function addTube(group, points, color, radius) {
  const curve = new THREE.CatmullRomCurve3(points);
  const tube = new THREE.Mesh(
    new THREE.TubeGeometry(curve, 64, radius, 12, false),
    new THREE.MeshStandardMaterial({ color, emissive: color, emissiveIntensity: 0.22 })
  );
  group.add(tube);
}

function addConfidenceVolumes(group, points) {
  points.forEach((point, index) => {
    const mesh = new THREE.Mesh(
      new THREE.SphereGeometry(0.18 + index * 0.018, 24, 12),
      new THREE.MeshStandardMaterial({ color: 0x37b7d8, transparent: true, opacity: 0.12, depthWrite: false })
    );
    mesh.position.copy(point);
    group.add(mesh);
  });
}

function addMarker(group, point, color) {
  if (!point) return;
  const marker = new THREE.Mesh(
    new THREE.SphereGeometry(0.14, 24, 16),
    new THREE.MeshStandardMaterial({ color, emissive: color, emissiveIntensity: 0.35 })
  );
  marker.position.copy(point);
  group.add(marker);
}

function addWicketPrediction(group, prediction) {
  const zone = new THREE.Mesh(
    new THREE.BoxGeometry(0.62, 0.72, 0.72),
    new THREE.MeshStandardMaterial({ color: 0xef9f27, transparent: true, opacity: 0.16 })
  );
  zone.position.set(7.1, 0, 0.36);
  group.add(zone);
  if (prediction?.collision) addMarker(group, normalizePoint(prediction.collision), 0xef9f27);
}

// The dashboard decision now carries the canonical trajectory OBJECT (matching the
// Testing page), but the live websocket path may still deliver a bare points array.
// The 3D technical view and the point-count labels want the observed points either way,
// so this normalises both shapes to a flat array — no consumer needs to know which.
function trajectoryPoints(trajectory) {
  if (Array.isArray(trajectory)) return trajectory;
  if (Array.isArray(trajectory?.points)) return trajectory.points;
  if (Array.isArray(trajectory?.observed?.points)) return trajectory.observed.points;
  return [];
}

function normalizeTrajectory(points) {
  return points.map(normalizePoint).filter(Boolean);
}

function normalizePoint(point) {
  if (!point) return null;
  const x = Number(point.x);
  const y = Number(point.y);
  const z = Number(point.z ?? 0.2);
  if ([x, y, z].some((value) => Number.isNaN(value))) return null;
  return new THREE.Vector3(x, y, z);
}

function resizeThree() {
  if (!state.scene) return;
  const rect = els.sceneHost.getBoundingClientRect();
  if (rect.width < 1 || rect.height < 1) return;
  state.scene.camera.aspect = rect.width / Math.max(1, rect.height);
  state.scene.camera.updateProjectionMatrix();
  state.scene.renderer.setSize(rect.width, rect.height, false);
}

function resetThreeCamera() {
  if (!state.scene?.camera || !state.scene?.controls) return;
  state.scene.camera.position.set(8, 7, 12);
  state.scene.controls.target.set(0, 0, 0.55);
  state.scene.controls.update();
}

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

const ueState = { peak: 0, targetPeak: 0, hasEvents: false };

function drawUltraEdge(decision) {
  ueState.targetPeak = Number(decision?.edge_analysis?.edge_probability || 0);
  ueState.hasEvents = (decision?.edge_analysis?.events || []).length > 0;
}

let ueFrame = 0;
function ultraEdgeLoop() {
  requestAnimationFrame(ultraEdgeLoop);
  if ((ueFrame += 1) % 2 !== 0) return;
  const canvas = els.ultraedge;
  if (canvas && canvas.getContext) {
    const ctx = canvas.getContext("2d");
    const { width, height } = canvas;
    const now = performance.now();
    const cursor = (now / 7) % width;
    ueState.peak += (ueState.targetPeak - ueState.peak) * 0.06;
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = "#080d0f";
    ctx.fillRect(0, 0, width, height);
    ctx.strokeStyle = "rgba(96,165,250,0.75)";
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    for (let x = 0; x < width; x += 1) {
      const base = Math.sin((x + now / 40) * 0.09) * 7 + Math.sin((x + now / 22) * 0.21) * 3.5;
      const spike = ueState.hasEvents && Math.abs(x - cursor) < 18
        ? Math.sin((x - cursor) / 2) * 46 * Math.max(ueState.peak, 0.4) : 0;
      const y = height / 2 + base - spike;
      if (x === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();
    ctx.strokeStyle = "rgba(34,197,94,0.9)";
    ctx.beginPath();
    ctx.moveTo(cursor, 6);
    ctx.lineTo(cursor, height - 6);
    ctx.stroke();
    if (ueState.peak > 0.2) {
      ctx.fillStyle = "rgba(245,158,11,0.9)";
      ctx.fillRect(width * 0.58, 14, 2, height - 28);
      ctx.fillStyle = "rgba(245,158,11,0.95)";
      ctx.font = "bold 13px Inter, sans-serif";
      ctx.fillText("Edge detected", width * 0.58 + 8, 26);
    }
  }
}
requestAnimationFrame(ultraEdgeLoop);

// VAR-style staged reveal: light the adaptive timeline up stage by stage (item 4).
// On completion it re-renders against the *current* decision, so a verdict that
// arrives (or an umpire confirmation made) during the animation is reflected.
function playDecisionReveal(triggerStatus, decision) {
  state.revealing = true;
  renderDecisionState("PROCESSING");
  const stages = REVIEW_MODULES[state.reviewType].stages;
  renderTimeline();
  els.badge.className = "badge processing";
  els.badge.textContent = "REVIEWING";
  els.title.textContent = "Reviewing…";
  let i = 0;
  clearInterval(state.revealTimer);
  state.revealTimer = setInterval(() => {
    if (i < stages.length) {
      paintTimelineProgress(i);
      els.overlay.className = "";
      void els.overlay.offsetWidth;
      els.overlay.className = "broadcast-overlay processing";
      els.overlay.textContent = stages[i].toUpperCase();
      i += 1;
    } else {
      clearInterval(state.revealTimer);
      state.revealing = false;
      paintTimelineProgress(stages.length);
      const latest = state.decision || decision;
      const finalStatus = latest.status || triggerStatus;
      renderDecision(latest);
      if (finalStatus === "OUT" || finalStatus === "NOT_OUT") {
        showToast(`Decision: ${displayStatus(finalStatus)}`, statusClass(finalStatus));
      }
    }
  }, 400);
}

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

function renderHotspot(decision) {
  if (!els.hotspotView) return;
  const hotspot = decision?.hotspot_analysis || {};
  if (hotspot.contact_detected) {
    els.hotspotView.textContent = `Contact detected (${pct(hotspot.confidence)}) · ${hotspot.reason || "Optical-flow proxy"}`;
    els.hotspotView.classList.add("active");
  } else {
    els.hotspotView.textContent = hotspot.reason || "No contact heatmap yet";
    els.hotspotView.classList.remove("active");
  }
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

async function refreshPreflight() {
  if (state.view !== "checklist" || !els.preflightGrid) return;
  const ids = preflightSelectedCameras();
  const params = new URLSearchParams();
  if (ids.length) params.set("cameras", ids.join(","));
  try {
    renderPreflight(await jsonFetch(`/api/preflight?${params.toString()}`));
  } catch {
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
      <thead><tr><th>ID</th><th>Type</th><th>Decision</th><th>Confidence</th><th>Model</th><th>Replay</th><th>Time</th><th></th></tr></thead>
      <tbody>
        ${rows.map((review) => {
          const decision = String(review.decision || "--");
          const cls = decision === "OUT" ? "out" : decision === "NOT OUT" ? "not-out" : decision === "INTERRUPTED" ? "interrupted" : "";
          const conf = review.confidence != null ? `${Math.round(Number(review.confidence) * 100)}%` : "--";
          const type = String(review.review_type || review.type || "—").toUpperCase();
          const model = (review.provenance && review.provenance.model) || "—";
          const hasReplay = review.review_id ? "✓" : "—";
          const time = review.time ? new Date(Number(review.time)).toLocaleTimeString() : "--";
          return `<tr>
            <td>${review.id || "--"}</td>
            <td>${type}</td>
            <td><span class="rev-dec ${cls}">${decision}</span></td>
            <td>${conf}</td>
            <td class="rev-model">${model}</td>
            <td>${hasReplay}</td>
            <td>${time}</td>
            <td><button type="button" class="rev-export" data-export-review="${review.id || ""}">Export</button></td>
          </tr>`;
        }).join("")}
      </tbody>
    </table>`;
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

function statusText(status) {
  if (status === "OUT") return "OUT";
  if (status === "NOT_OUT") return "NOT OUT";
  return status === "PROCESSING" ? "Processing review" : "Waiting for appeal";
}

function broadcastText(status, decision) {
  if (status === "OUT") return "OUT";
  if (status === "NOT_OUT") return "NOT OUT";
  if (decision?.wicket_prediction?.umpire_call) return "UMPIRE'S CALL";
  return "WAITING";
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
els.confirmOut?.addEventListener("click", () => confirmDecision("OUT"));
els.confirmNotOut?.addEventListener("click", () => confirmDecision("NOT_OUT"));
els.resetReview?.addEventListener("click", resetReview);
els.exportReview?.addEventListener("click", async () => {
  // ONE export, browser-download style: a save dialog asks WHERE, the backend
  // renders the broadcast review clip (UltraEdge scene + verdict) to that path.
  const btn = els.exportReview;
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
els.resetCamera?.addEventListener("click", resetThreeCamera);
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
window.addEventListener("resize", resizeThree);
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
initThree();
connectWebSockets();
refreshHealth();
refreshSystemHealth();
refreshCameraStatus();
refreshDecision();
// Resume the current match (name + review queue) — but never the active review,
// which the backend keeps at WAITING on launch.
loadCurrentMatch();
timers.health = setInterval(refreshHealth, 15000);
timers.system = setInterval(() => { refreshSystemHealth(); refreshPreflight(); }, 15000);
timers.cameras = setInterval(refreshCameraStatus, 10000);
timers.frames = setInterval(refreshCameraFrames, 1000);
timers.decision = setInterval(refreshDecision, 5000);
