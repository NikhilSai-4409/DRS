const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("drs", {
  onDecision: (cb) => ipcRenderer.on("decision-update", (_event, decision) => cb(decision)),
  onStartupStatus: (cb) => ipcRenderer.on("startup-status", (_event, status) => cb(status)),
  requestReview: (data) => ipcRenderer.invoke("request-review", data),
  getHealth: () => ipcRenderer.invoke("get-health"),
  getStartupStatus: () => ipcRenderer.invoke("get-startup-status"),
  getAiDevelopmentStatus: () => ipcRenderer.invoke("get-ai-development-status"),
  runAiDevelopmentCommand: (name) => ipcRenderer.invoke("run-ai-development-command", name),
  openVisionStudio: (opts) => ipcRenderer.invoke("open-vision-studio", opts),
  pickModelFile: () => ipcRenderer.invoke("pick-model-file"),
  importMatchRecordings: (opts) => ipcRenderer.invoke("import-match-recordings", opts),
  openDevelopmentFolder: (kind) => ipcRenderer.invoke("open-development-folder", kind),
  importDataset: (opts) => ipcRenderer.invoke("import-dataset", opts),
  listDatasets: () => ipcRenderer.invoke("list-datasets"),
  getSystemHealth: () => ipcRenderer.invoke("get-system-health"),
  getReviews: () => ipcRenderer.invoke("get-reviews"),
  getCalibrationProfiles: () => ipcRenderer.invoke("get-calibration-profiles"),
  getTestingPlatformUrl: () => ipcRenderer.invoke("get-testing-platform-url"),
  setAnalysisMode: (data) => ipcRenderer.invoke("set-analysis-mode", data),
  saveCalibrationProfile: (data) => ipcRenderer.invoke("save-calibration-profile", data),
  command: (name) => ipcRenderer.invoke("operator-command", name),
  // Save-dialog-first broadcast export: main asks where, backend renders there.
  exportBroadcast: (opts) => ipcRenderer.invoke("export-broadcast", opts),
  // TV Output window (clean review screen for the live stream) + its relay.
  openProgramOutput: () => ipcRenderer.invoke("open-program-output"),
  sendProgramCommand: (cmd) => ipcRenderer.invoke("program-command", cmd),
  onProgramCommand: (cb) => ipcRenderer.on("program-command", (_event, cmd) => cb(cmd)),
  onProgramOutputClosed: (cb) => ipcRenderer.on("program-output-closed", () => cb()),
});
