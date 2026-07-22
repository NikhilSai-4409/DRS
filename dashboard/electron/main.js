const { app, BrowserWindow, dialog, ipcMain, shell } = require("electron");
const { spawn, execFile, execFileSync } = require("child_process");
const crypto = require("crypto");
const fs = require("fs");
const http = require("http");
const path = require("path");
const { pathToFileURL } = require("url");

const ENGINE_PORT = 8765;
const TESTING_PLATFORM_PORT = 5173;
const ENGINE_URL = `http://localhost:${ENGINE_PORT}`;
const TESTING_PLATFORM_URL = `http://127.0.0.1:${TESTING_PLATFORM_PORT}`;
const DEV_REPO_ROOT = path.resolve(__dirname, "..", "..");
const BACKEND_ROOT = app.isPackaged ? path.join(process.resourcesPath, "backend") : DEV_REPO_ROOT;
const TESTING_PLATFORM_ROOT = path.join(DEV_REPO_ROOT, "dashboard", "testing-platform");
const LOG_PREFIX = "[DRS Electron]";

app.disableHardwareAcceleration();
app.commandLine.appendSwitch("disable-gpu");
app.commandLine.appendSwitch("disable-gpu-compositing");
app.commandLine.appendSwitch("disable-gpu-sandbox");
app.commandLine.appendSwitch("disable-accelerated-2d-canvas");
app.commandLine.appendSwitch("disable-accelerated-video-decode");
app.commandLine.appendSwitch("disable-features", "VizDisplayCompositor");
app.commandLine.appendSwitch("in-process-gpu");
if (!app.isPackaged) {
  app.setPath("userData", path.join(DEV_REPO_ROOT, "data", "electron-user-data"));
  app.setPath("cache", path.join(DEV_REPO_ROOT, "data", "electron-cache"));
}

let engineProcess = null;
let testingPlatformProcess = null;
let mainWindow = null;
let programWindow = null;

const startupState = {
  engine: { status: "pending", message: "Starting backend..." },
  testingPlatform: { status: "skipped", message: "Optional — not started yet" },
};

function log(message, ...args) {
  console.log(LOG_PREFIX, message, ...args);
}

function logError(message, ...args) {
  console.error(LOG_PREFIX, message, ...args);
}

process.on("unhandledRejection", (reason) => {
  logError("Unhandled promise rejection:", reason);
});

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 960,
    minWidth: 1100,
    minHeight: 720,
    backgroundColor: "#090b10",
    show: true,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  loadLoadingScreen(startupState.engine.message);
  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

// Broadcast "Program Output" — a second window holding only the clean 16:9
// broadcast frame (live / UltraEdge / decision scenes). OBS captures this window
// for the stream; every operator control stays in the dashboard and drives it
// through the program-command relay registered below.
function createProgramWindow() {
  if (programWindow) {
    programWindow.focus();
    return;
  }
  programWindow = new BrowserWindow({
    width: 1280,
    height: 720,
    useContentSize: true,
    backgroundColor: "#000000",
    autoHideMenuBar: true,
    title: "DRS Program Output",
    show: true,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  const file = path.join(__dirname, "renderer", "program-output.html");
  programWindow.loadURL(pathToFileURL(file).toString()).catch((error) => {
    logError("Program output load failed:", error.message);
  });
  programWindow.on("closed", () => {
    programWindow = null;
    mainWindow?.webContents.send("program-output-closed");
  });
}

// loadURL rejects with `ERR_ABORTED (-3) loading '<url>'` whenever a navigation is
// superseded by a later loadURL on the same window. Our startup intentionally
// replaces the loading screen with the dashboard, so that abort is expected and
// benign — swallow it. Critically, the abort event carries the *previous* URL, so
// without this guard the dashboard load would reject citing the loading-screen URL.
// Any other failure is re-thrown for the caller to surface.
function safeLoadURL(url) {
  if (!mainWindow) return Promise.resolve();
  return mainWindow.loadURL(url).catch((error) => {
    const aborted = error && (error.code === "ERR_ABORTED" || /\(-3\)/.test(error.message || ""));
    if (aborted) {
      log("Ignoring superseded navigation:", url);
      return;
    }
    throw error;
  });
}

// Show the startup splash from a real file (never a data: URL — Electron blocks
// top-frame data: navigations). The stage text rides along in the URL hash.
function loadLoadingScreen(detail) {
  if (!mainWindow) return Promise.resolve();
  const file = path.join(__dirname, "renderer", "loading.html");
  const text = detail || "Starting DRS engine...";
  const url = `${pathToFileURL(file).toString()}#${encodeURIComponent(text)}`;
  return safeLoadURL(url);
}

function attachProcessLogging(label, child) {
  if (!child) return;
  child.stdout?.on("data", (chunk) => log(`${label} stdout:`, String(chunk).trim()));
  child.stderr?.on("data", (chunk) => logError(`${label} stderr:`, String(chunk).trim()));
  child.on("error", (error) => {
    logError(`${label} spawn error:`, error.message);
  });
  child.on("exit", (code, signal) => {
    log(`${label} exited`, { code, signal });
  });
}

function spawnOptions(cwd) {
  const options = {
    cwd,
    env: { ...process.env },
    windowsHide: true,
    stdio: ["ignore", "pipe", "pipe"],
  };
  return options;
}

function pythonCandidates() {
  const candidates = [];
  if (process.env.DRS_PYTHON) candidates.push(process.env.DRS_PYTHON);
  if (process.platform === "win32") {
    candidates.push(path.join(DEV_REPO_ROOT, ".venv", "Scripts", "python.exe"));
    // Fallback: the sibling CVB venv (carries the DRS vision + backend deps).
    // Resolved relative to the repo so it follows the drive letter if it changes.
    candidates.push(path.join(DEV_REPO_ROOT, "..", "vision studio", ".venv", "Scripts", "python.exe"));
    candidates.push("py");
    candidates.push("python");
  } else {
    candidates.push(path.join(DEV_REPO_ROOT, ".venv", "bin", "python"));
    candidates.push("python3");
    candidates.push("python");
  }
  return candidates;
}

// A candidate is only usable if it actually runs AND can import the backend's
// core deps. This rejects a broken venv (a python.exe stub whose base Python was
// removed) and a bare interpreter without the DRS packages installed.
function pythonWorks(exe) {
  try {
    execFileSync(exe, ["-c", "import cv2, fastapi"], { stdio: "ignore", timeout: 20000 });
    return true;
  } catch (error) {
    log(`Python candidate rejected: ${exe} (${error.message})`);
    return false;
  }
}

function resolvePython() {
  for (const candidate of pythonCandidates()) {
    if (path.isAbsolute(candidate) && !fs.existsSync(candidate)) continue;
    if (pythonWorks(candidate)) return candidate;
  }
  return null;
}

function healthCheckAsync() {
  return new Promise((resolve) => {
    healthCheck(resolve);
  });
}

// Fetch and parse GET /api/health; resolves null if unreachable / non-200 / bad JSON.
function fetchHealthJson() {
  return new Promise((resolve) => {
    const request = http.get(`${ENGINE_URL}/api/health`, (response) => {
      if (response.statusCode !== 200) { response.resume(); return resolve(null); }
      let body = "";
      response.setEncoding("utf8");
      response.on("data", (chunk) => { body += chunk; });
      response.on("end", () => { try { resolve(JSON.parse(body)); } catch { resolve(null); } });
    });
    request.on("error", () => resolve(null));
    request.setTimeout(1500, () => { request.destroy(); resolve(null); });
  });
}

// Content hash of the backend Python sources — MUST match core/api_server.py's
// _compute_code_version() (same file set + ordering): core/**/*.py sorted by posix
// relpath, then drs_app.py, then config/settings.py; each contributes relpath\0bytes\0.
function computeCodeVersion() {
  const root = BACKEND_ROOT;
  const toRel = (file) => path.relative(root, file).split(path.sep).join("/");
  const coreFiles = [];
  const walk = (dir) => {
    let entries;
    try { entries = fs.readdirSync(dir, { withFileTypes: true }); } catch { return; }
    for (const entry of entries) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) walk(full);
      else if (entry.name.endsWith(".py")) coreFiles.push(full);
    }
  };
  walk(path.join(root, "core"));
  coreFiles.sort((a, b) => { const ra = toRel(a), rb = toRel(b); return ra < rb ? -1 : ra > rb ? 1 : 0; });
  const ordered = [...coreFiles, path.join(root, "drs_app.py"), path.join(root, "config", "settings.py")];
  const digest = crypto.createHash("sha1");
  const NUL = Buffer.from([0]);
  for (const file of ordered) {
    let bytes;
    try { bytes = fs.readFileSync(file); } catch { continue; }
    digest.update(toRel(file)); digest.update(NUL);
    digest.update(bytes); digest.update(NUL);
  }
  return digest.digest("hex").slice(0, 16);
}

// Kill whatever process is listening on the port (a stale/foreign backend we don't own).
function killBackendOnPort(port) {
  return new Promise((resolve) => {
    if (process.platform === "win32") {
      execFile("cmd", ["/c", `netstat -ano | findstr :${port}`], { windowsHide: true }, (_err, stdout) => {
        const pids = new Set();
        String(stdout || "").split(/\r?\n/).forEach((line) => {
          const match = line.trim().match(/LISTENING\s+(\d+)\s*$/i);
          if (match) pids.add(match[1]);
        });
        if (!pids.size) return resolve(false);
        let remaining = pids.size;
        pids.forEach((pid) => {
          execFile("taskkill", ["/PID", pid, "/T", "/F"], { windowsHide: true }, () => {
            if (--remaining === 0) resolve(true);
          });
        });
      });
    } else {
      execFile("sh", ["-c", `lsof -ti tcp:${port} | xargs -r kill -9`], () => resolve(true));
    }
  });
}

// Poll until nothing answers /api/health (the old process has released the port).
function waitForPortFree(timeoutMs = 6000) {
  const started = Date.now();
  return new Promise((resolve) => {
    const tick = () => {
      healthCheck((alive) => {
        if (!alive) return resolve(true);
        if (Date.now() - started > timeoutMs) return resolve(false);
        setTimeout(tick, 250);
      });
    };
    tick();
  });
}

async function startEngine() {
  startupState.engine = { status: "starting", message: "Launching FastAPI backend..." };

  // Version handshake: reuse an existing backend ONLY if it is running the current
  // code. Otherwise a stale process bound to 8765 would silently mask code changes,
  // so we replace it. This is the guard for the recurring "my fix didn't take effect".
  const existing = await fetchHealthJson();
  if (existing) {
    const expected = computeCodeVersion();
    if (existing.code_version && existing.code_version === expected) {
      startupState.engine = { status: "online", message: "Using existing backend on port 8765" };
      log("Backend already running with matching code — reusing", { code_version: expected });
      return true;
    }
    log("Stale backend on 8765 — replacing", { running: existing.code_version || "unknown", expected });
    startupState.engine = { status: "restarting", message: "Replacing stale backend on port 8765..." };
    await killBackendOnPort(ENGINE_PORT);
    await waitForPortFree();
  }

  const pythonExe = resolvePython();
  if (!pythonExe) {
    const message = "Python executable not found. Set DRS_PYTHON or create .venv.";
    startupState.engine = { status: "failed", message };
    logError(message);
    return false;
  }

  try {
    engineProcess = spawn(
      pythonExe,
      ["drs_app.py", "--testing-api", "--cameras", "0,1,2,3,4,5", "--host", "127.0.0.1", "--port", String(ENGINE_PORT)],
      spawnOptions(BACKEND_ROOT)
    );

    attachProcessLogging("engine", engineProcess);
    startupState.engine = { status: "started", message: "Backend process launched" };
    log("Engine spawn ok", { python: pythonExe, command: "drs_app.py --testing-api", cwd: BACKEND_ROOT });
    return true;
  } catch (error) {
    startupState.engine = { status: "failed", message: error.message };
    logError("Engine spawn failed:", error.message);
    return false;
  }
}

function testingPlatformReady() {
  return fs.existsSync(path.join(TESTING_PLATFORM_ROOT, "package.json"))
    && fs.existsSync(path.join(TESTING_PLATFORM_ROOT, "node_modules"));
}

function startTestingPlatform() {
  if (!testingPlatformReady()) {
    startupState.testingPlatform = {
      status: "unavailable",
      message: "React testing platform not installed (optional). Run: cd dashboard/testing-platform && npm install",
    };
    log("Skipping testing platform — package.json or node_modules missing");
    return false;
  }

  startupState.testingPlatform = { status: "starting", message: "Launching React dev server (optional)..." };

  try {
    const npmExe = process.platform === "win32" ? "npm.cmd" : "npm";
    testingPlatformProcess = spawn(npmExe, ["run", "dev"], spawnOptions(TESTING_PLATFORM_ROOT));
    attachProcessLogging("testing-platform", testingPlatformProcess);
    startupState.testingPlatform = {
      status: "started",
      message: `Optional testing UI starting at ${TESTING_PLATFORM_URL}`,
      url: TESTING_PLATFORM_URL,
    };
    log("Testing platform spawn ok", { cwd: TESTING_PLATFORM_ROOT });
    return true;
  } catch (error) {
    startupState.testingPlatform = {
      status: "failed",
      message: `Testing platform failed to start: ${error.message}`,
      url: null,
    };
    logError("Testing platform spawn failed:", error.message);
    return false;
  }
}

function waitForEngine(timeoutMs = 45000) {
  const started = Date.now();
  return new Promise((resolve, reject) => {
    const timer = setInterval(() => {
      healthCheck((ready) => {
        if (ready) {
          clearInterval(timer);
          startupState.engine = { status: "online", message: "Backend connected" };
          resolve();
        } else if (Date.now() - started > timeoutMs) {
          clearInterval(timer);
          startupState.engine = {
            status: "offline",
            message: `Backend not reachable at ${ENGINE_URL} after ${timeoutMs / 1000}s`,
          };
          reject(new Error(startupState.engine.message));
        }
      });
    }, 500);
  });
}

function healthCheck(callback) {
  const request = http.get(`${ENGINE_URL}/api/health`, (response) => {
    response.resume();
    callback(response.statusCode === 200);
  });
  request.on("error", () => callback(false));
  request.setTimeout(450, () => {
    request.destroy();
    callback(false);
  });
}

async function loadDashboardUi() {
  if (!mainWindow) return;
  const indexPath = path.join(__dirname, "renderer", "index.html");
  if (!fs.existsSync(indexPath)) {
    throw new Error(`Dashboard UI missing: ${indexPath}`);
  }
  await safeLoadURL(pathToFileURL(indexPath).toString());
}

async function bootstrap() {
  createWindow();

  const engineSpawned = await startEngine();
  startupState.testingPlatform = {
    status: "skipped",
    message: "Separate React testing platform skipped; testing is integrated into the main dashboard.",
  };

  try {
    if (engineSpawned) {
      await loadLoadingScreen("Waiting for FastAPI on port 8765...");
      await waitForEngine();
    } else {
      await dialog.showMessageBox(mainWindow, {
        type: "warning",
        title: "DRS backend unavailable",
        message: "The Python backend could not be started.",
        detail: startupState.engine.message,
      });
    }
  } catch (error) {
    logError("Backend health check failed:", error.message);
    await dialog.showMessageBox(mainWindow, {
      type: "warning",
      title: "DRS backend offline",
      message: "The dashboard will open in offline mode.",
      detail: `${error.message}\n\nYou can still use calibration UI. Upload testing requires the backend.`,
    });
  }

  try {
    await loadDashboardUi();
    if (mainWindow) {
      mainWindow.webContents.send("startup-status", startupState);
    }
    log("Dashboard loaded", startupState);
  } catch (error) {
    logError("Dashboard load failed:", error.message);
    dialog.showErrorBox("Dashboard failed to load", error.message);
  }
}

app.whenReady().then(bootstrap).catch((error) => {
  logError("Bootstrap failed:", error.message);
  dialog.showErrorBox("DRS failed to start", error.message);
});

function killChild(child, label) {
  if (!child || child.killed) return;
  try {
    if (process.platform === "win32") {
      spawn(`taskkill /pid ${child.pid} /T /F`, [], { shell: true, windowsHide: true });
    } else {
      child.kill("SIGTERM");
    }
    log(`Stopped ${label}`);
  } catch (error) {
    logError(`Failed to stop ${label}:`, error.message);
  }
}

app.on("will-quit", () => {
  killChild(engineProcess, "engine");
  killChild(testingPlatformProcess, "testing-platform");
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    bootstrap().catch((error) => logError("Re-activate bootstrap failed:", error.message));
  }
});

ipcMain.handle("operator-command", async (_event, command) => {
  return { ok: true, command, timestamp: Date.now() };
});

ipcMain.handle("get-startup-status", async () => startupState);
ipcMain.handle("get-ai-development-status", async () => runDevelopmentJson(["status"]));
ipcMain.handle("run-ai-development-command", async (_event, name) => runDevelopmentCommand(name));
ipcMain.handle("open-vision-studio", async (_event, opts = {}) => openVisionStudio(opts));
ipcMain.handle("open-program-output", async () => {
  createProgramWindow();
  return { open: true };
});
ipcMain.handle("reveal-path", async (_event, target) => {
  if (typeof target === "string" && target) shell.showItemInFolder(path.resolve(target));
  return { ok: true };
});
ipcMain.handle("program-command", async (_event, command) => {
  if (!programWindow) return { delivered: false };
  programWindow.webContents.send("program-command", command);
  return { delivered: true };
});
ipcMain.handle("pick-model-file", async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: "Select a .pt model",
    properties: ["openFile"],
    filters: [{ name: "PyTorch model", extensions: ["pt"] }],
  });
  return result.canceled || !result.filePaths.length ? null : result.filePaths[0];
});
ipcMain.handle("import-match-recordings", async (_event, opts = {}) => importMatchRecordings(opts));
ipcMain.handle("open-development-folder", async (_event, kind) => openDevelopmentFolder(kind));
ipcMain.handle("list-datasets", async () => {
  try {
    const dbPath = path.join(DEV_REPO_ROOT, "training", "datasets", "database.json");
    if (!fs.existsSync(dbPath)) return { datasets: {} };
    return JSON.parse(fs.readFileSync(dbPath, "utf8"));
  } catch (error) {
    return { datasets: {}, error: error.message };
  }
});
ipcMain.handle("import-dataset", async (_event, opts = {}) => {
  let zipPath = opts && opts.path;
  if (!zipPath) {
    const picked = await dialog.showOpenDialog(mainWindow, {
      title: "Select YOLO dataset export (.zip)",
      properties: ["openFile"],
      filters: [{ name: "YOLO export", extensions: ["zip"] }],
    });
    if (picked.canceled || !picked.filePaths.length) {
      return { ok: false, canceled: true, message: "Import canceled." };
    }
    zipPath = picked.filePaths[0];
  }
  return runDatasetImport(zipPath, Boolean(opts && opts.activate));
});

ipcMain.handle("get-health", async () => getJson("/api/health"));
ipcMain.handle("get-system-health", async () => getJson("/api/system/health"));
ipcMain.handle("get-reviews", async () => getJson("/api/reviews"));
ipcMain.handle("get-calibration-profiles", async () => getJson("/api/calibration/profiles"));
ipcMain.handle("get-testing-platform-url", async () => ({
  url: TESTING_PLATFORM_URL,
  status: startupState.testingPlatform.status,
  message: startupState.testingPlatform.message,
  available: startupState.testingPlatform.status === "started",
}));

ipcMain.handle("request-review", async (_event, data) => postJson("/api/appeal/request", data, true));
ipcMain.handle("set-analysis-mode", async (_event, data) => postJson("/api/analysis-mode", data));
ipcMain.handle("save-calibration-profile", async (_event, data) => postJson("/api/calibration/save", data));

function runDevelopmentJson(args) {
  return new Promise((resolve, reject) => {
    const pythonExe = resolvePython();
    if (!pythonExe) {
      reject(new Error("Python executable not found. Set DRS_PYTHON or create .venv."));
      return;
    }
    execFile(
      pythonExe,
      ["development/dashboard_api.py", ...args],
      { cwd: DEV_REPO_ROOT, windowsHide: true, maxBuffer: 1024 * 1024 * 4 },
      (error, stdout, stderr) => {
        if (error) {
          reject(new Error(stderr || error.message));
          return;
        }
        try {
          resolve(JSON.parse(stdout));
        } catch (parseError) {
          reject(new Error(`AI-development command returned invalid JSON: ${parseError.message}`));
        }
      }
    );
  });
}

function runDatasetImport(zipPath, activate) {
  return new Promise((resolve) => {
    const pythonExe = resolvePython();
    if (!pythonExe) {
      resolve({ ok: false, message: "Python executable not found. Set DRS_PYTHON or create .venv." });
      return;
    }
    const args = ["scripts/import_annotations.py", zipPath, "--json"];
    if (activate) args.push("--activate");
    execFile(
      pythonExe,
      args,
      { cwd: DEV_REPO_ROOT, windowsHide: true, maxBuffer: 1024 * 1024 * 8 },
      (error, stdout, stderr) => {
        // The importer prints a JSON result on both success (exit 0) and
        // validation failure (exit 1), so parse stdout regardless of exit code.
        try {
          resolve(JSON.parse(String(stdout || "").trim()));
        } catch (parseError) {
          resolve({ ok: false, message: stderr || (error && error.message) || "Import returned no JSON output." });
        }
      }
    );
  });
}

function readVisionStudioPid(pidFile) {
  try {
    if (!pidFile || !fs.existsSync(pidFile)) return null;
    const pid = Number(fs.readFileSync(pidFile, "utf8").trim());
    return Number.isFinite(pid) && pid > 0 ? pid : null;
  } catch {
    return null;
  }
}

function isPidRunning(pid) {
  if (!pid) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function writeVisionStudioPid(pidFile, pid) {
  if (!pidFile || !pid) return;
  fs.mkdirSync(path.dirname(pidFile), { recursive: true });
  fs.writeFileSync(pidFile, String(pid), "utf8");
}

function bringProcessToFront(pid) {
  if (!pid || process.platform !== "win32") return Promise.resolve(false);
  const script = `
    Add-Type @"
    using System;
    using System.Runtime.InteropServices;
    public class Win32 {
      [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
      [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr hWnd, int nCmdShow);
    }
"@
    $p = Get-Process -Id ${Number(pid)} -ErrorAction SilentlyContinue
    if ($p -and $p.MainWindowHandle -ne 0) {
      [Win32]::ShowWindowAsync($p.MainWindowHandle, 3) | Out-Null
      [Win32]::SetForegroundWindow($p.MainWindowHandle) | Out-Null
      "ok"
    } else {
      "no-window"
    }
  `;
  return new Promise((resolve) => {
    execFile("powershell.exe", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], {
      windowsHide: true,
      maxBuffer: 1024 * 64,
    }, (error, stdout) => resolve(!error && String(stdout).includes("ok")));
  });
}

function findExistingVisionStudioPid(studio) {
  if (process.platform !== "win32") return Promise.resolve(null);
  const exeBase = studio.executable ? path.basename(studio.executable, path.extname(studio.executable)) : "VisionStudio";
  const script = `
    $p = Get-Process -ErrorAction SilentlyContinue |
      Where-Object { $_.MainWindowTitle -like '*Vision Studio*' -or $_.ProcessName -eq '${exeBase.replace(/'/g, "''")}' } |
      Select-Object -First 1
    if ($p) { $p.Id }
  `;
  return new Promise((resolve) => {
    execFile("powershell.exe", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], {
      windowsHide: true,
      maxBuffer: 1024 * 64,
    }, (_error, stdout) => {
      const pid = Number(String(stdout || "").trim());
      resolve(Number.isFinite(pid) && pid > 0 ? pid : null);
    });
  });
}

function resolveVisionStudioLaunch(studio) {
  const executable = studio.executable;
  const entryPoint = studio.entry_point;
  const projectPath = studio.project_path || (entryPoint ? path.dirname(entryPoint) : DEV_REPO_ROOT);
  if (executable && fs.existsSync(executable)) {
    return { command: executable, args: [], cwd: path.dirname(executable), type: "executable" };
  }

  if (!entryPoint || !fs.existsSync(entryPoint)) {
    return { error: `Vision Studio entry point is not configured: ${entryPoint || "missing"}` };
  }

  const projectPython = process.platform === "win32"
    ? path.join(projectPath, ".venv", "Scripts", "python.exe")
    : path.join(projectPath, ".venv", "bin", "python");
  const pythonExe = fs.existsSync(projectPython) ? projectPython : resolvePython();
  if (!pythonExe) return { error: "Python executable not found. Set DRS_PYTHON or create .venv." };
  return { command: pythonExe, args: [entryPoint], cwd: projectPath, type: "python" };
}

// Launch Vision Studio like any other desktop app: if a window is already open,
// bring it to the front; otherwise start it. A plain open passes NO arguments, so
// Vision Studio opens on its own last-used workspace and never force-creates folders
// (the empty-folder / crash behaviour). Only "Import Match Recordings" passes args.
async function openVisionStudio(opts = {}) {
  try {
    const status = await runDevelopmentJson(["status"]);
    const studio = status?.vision_studio || {};
    // Detect a running instance by its live window/process — no PID file (it went stale).
    const existingPid = await findExistingVisionStudioPid(studio);
    if (isPidRunning(existingPid)) {
      const focused = await bringProcessToFront(existingPid);
      return { ok: true, alreadyRunning: true, focused, pid: existingPid };
    }

    const launch = resolveVisionStudioLaunch(studio);
    if (launch.error) return { ok: false, message: launch.error };
    const args = [...launch.args];
    if (opts.importRecordings) {
      if (studio.workspace) args.push("--workspace", studio.workspace);
      args.push("--import-recordings", opts.importRecordings);
    }

    const child = spawn(launch.command, args, {
      cwd: launch.cwd,
      env: { ...process.env },
      detached: true,
      windowsHide: false,
      stdio: "ignore",
    });
    child.unref();
    await delay(1800);
    if (!isPidRunning(child.pid)) {
      return {
        ok: false,
        message: `Vision Studio started and then exited immediately. Check the packaged build or rebuild ${path.join(studio.project_path || "", ".venv")}.`,
        pid: child.pid,
        path: launch.command,
        args,
        launchType: launch.type,
      };
    }
    setTimeout(() => bringProcessToFront(child.pid), 100);
    return { ok: true, alreadyRunning: false, pid: child.pid, path: launch.command, args, launchType: launch.type };
  } catch (error) {
    return { ok: false, message: error.message };
  }
}

async function importMatchRecordings(opts = {}) {
  try {
    const recordings = opts.recordingsPath || findLatestRecordingsFolder();
    if (!recordings) return { ok: false, message: "No match recordings folder found." };
    return openVisionStudio({ workspace: opts.workspace, importRecordings: recordings });
  } catch (error) {
    return { ok: false, message: error.message };
  }
}

function findLatestRecordingsFolder() {
  const roots = [
    path.join(DEV_REPO_ROOT, "data", "matches"),
    path.join(DEV_REPO_ROOT, "data", "recordings"),
  ];
  const candidates = [];
  for (const root of roots) {
    if (!fs.existsSync(root)) continue;
    for (const item of fs.readdirSync(root, { withFileTypes: true })) {
      const full = path.join(root, item.name);
      if (item.isDirectory()) candidates.push(full);
      else if (item.isFile()) candidates.push(root);
    }
  }
  if (!candidates.length) return null;
  candidates.sort((a, b) => fs.statSync(b).mtimeMs - fs.statSync(a).mtimeMs);
  return candidates[0];
}

async function openDevelopmentFolder(kind) {
  try {
    const status = await runDevelopmentJson(["status"]);
    const studio = status?.vision_studio || {};
    const folders = {
      workspace: studio.workspace,
      dataset: studio.dataset_folder || status?.dataset?.root,
      training: studio.training_folder,
      models: studio.models_folder,
      exports: studio.exports_folder,
    };
    const folder = folders[String(kind)];
    if (!folder) return { ok: false, message: `Unknown development folder: ${kind}` };
    const resolved = path.resolve(folder);
    const repoRoot = path.resolve(DEV_REPO_ROOT);
    if (!fs.existsSync(resolved)) {
      if (!resolved.toLowerCase().startsWith(repoRoot.toLowerCase() + path.sep)) {
        return { ok: false, message: `Folder is not available: ${resolved}`, path: resolved };
      }
      fs.mkdirSync(resolved, { recursive: true });
    }
    const result = await shell.openPath(resolved);
    return result ? { ok: false, message: result, path: resolved } : { ok: true, path: resolved };
  } catch (error) {
    return { ok: false, message: error.message };
  }
}

function runDevelopmentCommand(name) {
  return new Promise((resolve) => {
    const pythonExe = resolvePython();
    if (!pythonExe) {
      resolve({ ok: false, output: "Python executable not found. Set DRS_PYTHON or create .venv." });
      return;
    }
    const child = spawn(
      pythonExe,
      ["development/dashboard_api.py", "run", String(name)],
      spawnOptions(DEV_REPO_ROOT)
    );
    let output = "";
    child.stdout?.on("data", (chunk) => { output += String(chunk); });
    child.stderr?.on("data", (chunk) => { output += String(chunk); });
    child.on("error", (error) => resolve({ ok: false, output: error.message }));
    child.on("close", (code) => resolve({ ok: code === 0, code, output: output.trim() }));
  });
}

function getJson(route) {
  return new Promise((resolve, reject) => {
    const request = http.get(`${ENGINE_URL}${route}`, (response) => {
      let body = "";
      response.setEncoding("utf8");
      response.on("data", (chunk) => {
        body += chunk;
      });
      response.on("end", () => {
        try {
          if (response.statusCode && response.statusCode >= 400) {
            reject(new Error(`HTTP ${response.statusCode} for ${route}`));
            return;
          }
          resolve(JSON.parse(body));
        } catch (error) {
          reject(error);
        }
      });
    });
    request.on("error", reject);
    request.setTimeout(5000, () => {
      request.destroy();
      reject(new Error(`Timeout requesting ${route}`));
    });
  });
}

function postJson(route, payload, emitDecision = false) {
  return new Promise((resolve, reject) => {
    const body = JSON.stringify(payload || {});
    const request = http.request(`${ENGINE_URL}${route}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Content-Length": Buffer.byteLength(body),
      },
    }, (response) => {
      let data = "";
      response.setEncoding("utf8");
      response.on("data", (chunk) => {
        data += chunk;
      });
      response.on("end", () => {
        try {
          const parsed = data ? JSON.parse(data) : {};
          if (response.statusCode && response.statusCode >= 400) {
            reject(new Error(parsed.detail || `HTTP ${response.statusCode}`));
            return;
          }
          if (emitDecision && mainWindow) {
            mainWindow.webContents.send("decision-update", parsed.decision || parsed);
          }
          resolve(parsed);
        } catch (error) {
          reject(error);
        }
      });
    });
    request.on("error", reject);
    request.setTimeout(10000, () => {
      request.destroy();
      reject(new Error(`Timeout posting ${route}`));
    });
    request.write(body);
    request.end();
  });
}
