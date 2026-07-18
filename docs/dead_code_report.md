# Dead Code Report

Generated: 2026-06-12

Scope: source files, dashboard files, scripts, configuration, docs, tests, and known generated DRS runtime folders. Large generated datasets are summarized by folder to keep the report usable.

## Cleanup Performed

| Path | Action | Reason |
|---|---|---|
| `data/testing/*` | Deleted contents, recreated empty `uploads/` and `outputs/` | Generated testing videos, uploads, reports, animation exports, and local SQLite testing DB. Recreated automatically by backend. |
| `data/training_smoke/` | Deleted | Smoke-test generated dataset, not source data. |
| `data/electron-cache/` | Deleted | Electron runtime cache. |
| `data/electron-user-data/` | Deleted | Electron runtime state/cache. |
| `C:\Users\nikhi\OneDrive\Desktop\Cricket DRS Testing Platform.lnk` | Deleted | Old DRS desktop launcher shortcut. |
| `C:\Users\nikhi\OneDrive\Desktop\Cricket DRS.lnk` | Deleted | Old DRS desktop launcher shortcut. |

Kept:
- `C:\Users\nikhi\OneDrive\Desktop\DRS`
- `C:\Users\nikhi\OneDrive\Desktop\DRS.worktrees`
- `C:\Users\nikhi\OneDrive\Desktop\DRS.zip`
- `C:\Users\nikhi\OneDrive\Desktop\DRS (2).zip`

No unrelated Desktop files were deleted.

## Report

| File | Purpose | Status | Reason | Safe to delete? |
|------|---------|--------|--------|-----------------|
| `drs_app.py` | Main CLI entry point for dashboard, API, testing API, training app, and camera scan modes. | ACTIVE | Primary startup command uses this file. | NO |
| `main.js` | Root-level JavaScript placeholder. | UNUSED | File is empty and Electron uses `dashboard/electron/main.js`. | REVIEW |
| `setup.py` | Python package setup metadata. | ACTIVE | Supports editable/package install workflows. | NO |
| `pyproject.toml` | Python tooling/package metadata. | ACTIVE | Used by packaging/tooling. | NO |
| `requirements.txt` | Main Python dependency list. | ACTIVE | Required for setup and match-day install. | NO |
| `requirements-gpu.txt` | GPU-specific dependency list. | ACTIVE | Useful for RTX/GPU deployment. | NO |
| `.env.example` | Environment template. | ACTIVE | Documents runtime env settings. | NO |
| `.env` | Local runtime environment file. | ACTIVE | Local config; do not delete without checking secrets/settings. | NO |
| `README.md` | Project overview and setup docs. | ACTIVE | User-facing docs. | NO |
| `QUICK_START.md` | Startup instructions. | ACTIVE | Needs updating after dashboard consolidation, but still useful. | NO |
| `CAMERA_GUIDE.md` | Camera/deployment guide. | ACTIVE | Useful match-day setup information. | NO |
| `TESTING_GUIDE.md` | Testing instructions. | ACTIVE | Useful for test execution. | NO |
| `core/testing_api.py` | Offline/dashboard testing FastAPI backend. | ACTIVE | Main testing workflow depends on it. | NO |
| `core/testing_pipeline.py` | Offline delivery analysis pipeline. | ACTIVE | `/api/analyze` and testing jobs use it. | NO |
| `core/testing_database.py` | SQLite persistence for testing jobs. | ACTIVE | Testing API uses it. | NO |
| `core/ws_hub.py` | WebSocket broadcast hub. | ACTIVE | Dashboard and job progress channels use it. | NO |
| `core/ball_detector.py` | YOLO detector wrapper and model readiness. | ACTIVE | Testing and live pipelines use it. | NO |
| `core/ball_association.py` | Offline single-ball association/tracking. | ACTIVE | Testing pipeline uses it. | NO |
| `core/ball_tracker.py` | Live Kalman tracker. | ACTIVE | Live/integration path uses it. | NO |
| `core/tracker.py` | Extended tracker implementation. | UNCERTAIN | Referenced by `tests/test_tracker.py`; not used by main testing/live path. | REVIEW |
| `core/lbw_engine.py` | Authoritative MCC Law 36 LBW engine. | ACTIVE | `core/drs_decision.py` imports it. | NO |
| `core/lbw.py` | Older/alternate LBW helper. | DUPLICATE | Secondary LBW logic outside `lbw_engine.py`; not in main decision service. | REVIEW |
| `core/drs_decision.py` | DRS decision aggregation service. | ACTIVE | Testing pipeline uses it. | NO |
| `core/readiness.py` | Nine-gate readiness contract. | ACTIVE | Testing decisions use it. | NO |
| `core/decision_mapper.py` | Maps pipeline summaries to dashboard decision payloads. | ACTIVE | Testing API uses it. | NO |
| `core/trajectory.py` | Ball trajectory prediction. | ACTIVE | Testing and decision code use it. | NO |
| `core/tracking_quality.py` | Tracking quality metrics. | ACTIVE | Testing pipeline uses it. | NO |
| `core/pitch_calibration.py` | Manual pitch marker/homography calibration. | ACTIVE | Testing API/readiness use it. | NO |
| `core/calibration.py` | Checkerboard/multi-camera calibration utilities. | ACTIVE | Calibration script uses it; phase work will extend it. | NO |
| `core/synchronization.py` | Live sync verification. | ACTIVE | `core/integration.py` and `core/api_server.py` use it. | NO |
| `core/sync.py` | Older sync manager. | DUPLICATE | Separate sync/frame logic; only `tests/test_sync.py` references it. | REVIEW |
| `core/api_server.py` | Live camera FastAPI backend. | ACTIVE | Live workflow uses it. | NO |
| `core/integration.py` | Live multi-camera DRS pipeline. | ACTIVE | `drs_app.py --api/headless` uses it. | NO |
| `core/camera_manager.py` | Camera capture and replay controller. | ACTIVE | Live API and integration use it. | NO |
| `core/hotspot.py` | Optical contact/HotSpot approximation. | ACTIVE | Testing pipeline optional evidence uses it. | NO |
| `core/audio_edge.py` | Lightweight audio edge proxy. | ACTIVE | Testing pipeline optional edge analysis uses it. | NO |
| `core/audio_analyzer.py` | Audio analysis utility. | ACTIVE | Tests cover it; potential live evidence path. | NO |
| `core/appeal_manager.py` | Appeal state handling. | ACTIVE | Tests cover it; useful live workflow support. | NO |
| `core/model_selector.py` | Detector model selection/readiness. | ACTIVE | Ball detector uses it. | NO |
| `core/pitch_map.py` | Pitch geometry mapping helpers. | ACTIVE | Useful calibration/overlay support. | NO |
| `core/spin_detector.py` | Spin estimation helper. | UNCERTAIN | Not currently in main path; may be future physics feature. | REVIEW |
| `core/noball_detector.py` | No-ball detection helper. | UNCERTAIN | Not currently in main testing path. | REVIEW |
| `core/database.py` | General database helper. | UNCERTAIN | Testing uses `testing_database.py`; review imports before deleting. | REVIEW |
| `config/settings.py` | Central settings. | ACTIVE | Imported across runtime. | NO |
| `config/calibration_profiles.json` | Dashboard calibration profile store. | ACTIVE | Electron/testing API reads it. | NO |
| `config/calibration_profiles/.gitkeep` | Placeholder for calibration profile directory. | ACTIVE | Needed for file-based profile directory once created. | NO |
| `training/data.yaml` | YOLO training dataset config. | ACTIVE | Training command uses it. | NO |
| `training/drs_yolo_dataset.yaml` | Older YOLO dataset config. | SUPERSEDED | New pipeline uses `training/data.yaml`; keep until docs/tests updated. | REVIEW |
| `training/images/` | Extracted training images. | ACTIVE | Needed for model training if labels are being curated. | NO |
| `training/labels/` | YOLO labels. | ACTIVE | Needed for model training. | NO |
| `training/review/` | Frames needing manual review. | ACTIVE | Needed for label cleanup. | NO |
| `models/cricket_ball_yolov8.pt` | Cricket-ball detector model. | ACTIVE | Runtime detector uses it. | NO |
| `models/best.pt` | Alternate/best detector checkpoint. | ACTIVE | Validation/training default may use it. | NO |
| `models/model_evaluation.json` | Model readiness metrics. | ACTIVE | Model selector/readiness uses it. | NO |
| `models/public_assets_manifest.json` | Public model/baseline manifest. | ACTIVE | Bootstrap/readiness docs support. | NO |
| `scripts/prepare_training_data.py` | Extracts frames and prelabels YOLO training data. | ACTIVE | Needed for training pipeline. | NO |
| `scripts/train_yolo_drs.py` | Trains and validates YOLO detector. | ACTIVE | Needed for model training. | NO |
| `scripts/evaluate_yolo_drs.py` | Evaluates YOLO detector. | ACTIVE | Useful for validation. | NO |
| `scripts/validate_detector.py` | Detector validation script. | PLANNED | Not yet created in this phase. | NO |
| `scripts/validate_full_pipeline.py` | Full offline pipeline validation script. | PLANNED | Not yet created in this phase. | NO |
| `scripts/run_calibration.py` | CLI calibration runner. | PLANNED | Not yet created in this phase. | NO |
| `scripts/health_check.py` | Match-day health checker. | PLANNED | Not yet created in this phase. | NO |
| `scripts/calibrate.py` | Existing checkerboard calibration CLI. | ACTIVE | Uses `core/calibration.py`. | NO |
| `scripts/create_default_calibration_profile.py` | Creates ICC default calibration profile. | ACTIVE | Useful setup script. | NO |
| `scripts/write_calibration_readiness.py` | Writes calibration readiness info. | ACTIVE | Useful deployment/readiness script. | NO |
| `scripts/bootstrap_model_metrics.py` | Bootstraps model metrics. | ACTIVE | Useful model readiness setup. | NO |
| `scripts/bootstrap_public_assets.py` | Bootstraps public asset manifest. | ACTIVE | Useful model/baseline setup. | NO |
| `scripts/discover_cameras.py` | Camera discovery tool. | ACTIVE | Useful match-day setup. | NO |
| `scripts/benchmark_cameras.py` | Camera benchmarking tool. | ACTIVE | Useful hardware validation. | NO |
| `scripts/run_camera_tests.py` | Camera tests. | ACTIVE | Useful hardware validation. | NO |
| `scripts/generate_test_video.py` | Synthetic test video generator. | ACTIVE | Tests/demo fixtures may need it. | NO |
| `scripts/run_working_demo.ps1` | Old demo launcher. | SUPERSEDED | References old testing-platform path; review after Electron testing is final. | REVIEW |
| `scripts/start_testing_platform.ps1` | Old React testing platform launcher. | SUPERSEDED | Electron integrated testing supersedes it. | REVIEW |
| `scripts/create_testing_platform_shortcut.ps1` | Creates old Desktop shortcut. | SUPERSEDED | Desktop shortcuts were removed; do not recreate unless needed. | REVIEW |
| `dashboard/electron/main.js` | Electron main process. | ACTIVE | Desktop app uses it. | NO |
| `dashboard/electron/preload.js` | Safe IPC bridge. | ACTIVE | Renderer uses it. | NO |
| `dashboard/electron/package.json` | Electron app package config. | ACTIVE | `npm start`/packaging use it. | NO |
| `dashboard/electron/renderer/index.html` | Current Electron dashboard HTML. | ACTIVE | Main process loads it. | NO |
| `dashboard/electron/renderer/renderer.js` | Current Electron dashboard logic. | ACTIVE | Main dashboard uses it. | NO |
| `dashboard/electron/renderer/styles.css` | Current Electron dashboard styles. | ACTIVE | Main dashboard uses it. | NO |
| `dashboard/electron/renderer/components/TestingPanel.js` | Integrated testing panel. | ACTIVE | Renderer imports it. | NO |
| `dashboard/electron/renderer/components/ResultsPanel.js` | Integrated results panel. | ACTIVE | Renderer imports it. | NO |
| `dashboard/electron/renderer/components/DRSAnimationSequencer.js` | Dashboard DRS animation coordinator. | ACTIVE | Renderer imports it. | NO |
| `dashboard/electron/renderer/components/CalibrationModal.js` | Calibration modal. | ACTIVE | Renderer imports it. | NO |
| `dashboard/electron/renderer/components/StatusPanel.js` | Context-aware left/status panel. | ACTIVE | Renderer imports it. | NO |
| `dashboard/electron/renderer/components/PitchMap2D.js` | 2D pitch map renderer. | ACTIVE | Results panel imports it. | NO |
| `dashboard/electron/renderer/hooks/useAnalysisJob.js` | Job WebSocket/polling helper. | ACTIVE | Testing panel imports it. | NO |
| `dashboard/electron/renderer/hooks/useCalibrationProfiles.js` | Calibration CRUD helper. | ACTIVE | Testing/calibration UI imports it. | NO |
| `dashboard/electron/renderer/calibration.html` | Older standalone calibration page. | REMOVED | Deleted 2026-07-17; CalibrationWorkspace (CalibrationModal.js) is the only calibration UI. | DONE |
| `dashboard/electron/renderer/calibration.js` | Older standalone calibration page logic. | REMOVED | Deleted 2026-07-17 with calibration.html. | DONE |
| `dashboard/electron/renderer/calibration.css` | Older standalone calibration page styles. | REMOVED | Deleted 2026-07-17 with calibration.html. | DONE |
| `dashboard/electron/index.html` | Older root-level Electron page. | SUPERSEDED | Current app loads `renderer/index.html`. | REVIEW |
| `dashboard/electron/renderer.js` | Older root-level Electron renderer. | SUPERSEDED | Current app loads `renderer/renderer.js`. | REVIEW |
| `dashboard/electron/styles.css` | Older root-level Electron styles. | SUPERSEDED | Current app loads `renderer/styles.css`. | REVIEW |
| `dashboard/testing-platform/` | Separate React testing platform. | SUPERSEDED | Integrated Electron testing is intended to replace it; keep until click-through verified. | REVIEW |
| `ui/dashboard.py` | Tkinter dashboard. | SUPERSEDED | Default `drs_app.py` still can launch it; keep until Electron fully replaces it. | REVIEW |
| `ui/training_app.py` | Desktop YOLO training app. | ACTIVE | `drs_app.py --training-app` uses it. | NO |
| `utils/helpers.py` | Shared JSON/filesystem helpers. | ACTIVE | Calibration code uses it. | NO |
| `utils/logger.py` | Shared logging setup. | ACTIVE | Backend modules use it. | NO |
| `tests/` | Test suite and fixtures. | ACTIVE | Required for stabilization. | NO |
| `data/testing/` | Runtime testing uploads/outputs/DB. | GENERATED | Contents deleted and directories recreated. | YES, contents only |
| `data/calibration/` | Calibration runtime data. | ACTIVE | Do not delete calibration data. | NO |
| `data/validation_results/` | Detector validation output. | GENERATED | Safe to clear after exporting reports. | YES, contents only |
| `logs/` | Runtime logs. | GENERATED | Safe to clear when debugging is complete. | YES, contents only |
| `12345/` | Local copied `.MTS` footage. | ACTIVE DATA | DRS training/source footage; keep outside git if needed. | NO |
| `DRS.zip`, `DRS (2).zip` on Desktop | Backup archives. | ACTIVE BACKUP | User explicitly asked to keep zips. | NO |

## Phase 0 Result

PHASE 0 COMPLETE - report at `docs/dead_code_report.md`

