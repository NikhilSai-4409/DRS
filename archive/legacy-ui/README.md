# Archived legacy UI

The Cricket DRS workstation keeps **one** UI: the Electron Review Workstation at
`dashboard/electron/renderer/`. Everything here is a superseded UI moved out of the
live tree. Nothing in this folder is loaded, packaged, or imported by the running app.

## Archived (safe — moved, not deleted)

- `electron-root/index.html`, `electron-root/renderer.js`, `electron-root/styles.css`
  — the old root-level Electron "Command Center" UI. `main.js` loads
  `renderer/index.html`; electron-builder's `files` glob only ships `renderer/**`, so
  these three were never loaded or packaged. Confirmed dead before moving.

## Still in the tree — remove in a coordinated follow-up (each has a live reference)

These were left in place on purpose: deleting them blind would break a working path.
Each needs its reference updated in the same change, then a re-test.

1. **`ui/dashboard.py` (Tkinter dashboard)** — still the *default* mode of
   `drs_app.py` (`python drs_app.py` with no flags calls `run_dashboard`). To retire:
   change the default branch in `drs_app.py:main()` to print guidance (or launch the
   API) instead of the Tkinter window, then move this file here.

2. **`dashboard/testing-platform/` (old React platform)** — superseded by the
   integrated Testing view. Referenced by `scripts/run_working_demo.ps1`,
   `scripts/start_testing_platform.ps1`, `README.md`/`QUICK_START.md`, and dead
   plumbing in `dashboard/electron/main.js` (`startTestingPlatform()` — never called;
   `TESTING_PLATFORM_*` constants; the `#testing-platform-dialog` iframe in
   `renderer/index.html` + `testingFrame` in `renderer.js`). Retire by removing that
   plumbing, updating the two scripts + docs, then moving the folder here.

3. **`dashboard/electron/main.js` `operator-command` IPC handler** — a no-op stub
   (`ipcMain.handle("operator-command", … {ok:true, command})`). Remove together with
   its `preload.js` bridge entry once confirmed unused by `renderer.js`.
