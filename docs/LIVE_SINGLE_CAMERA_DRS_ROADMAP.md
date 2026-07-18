# Live Single-Camera Cricket DRS Roadmap

This roadmap turns the current repository into a demonstrable single-camera live Cricket DRS desktop application. It is intentionally implementation-oriented: every phase names the files to touch, the files to add, dependencies, real-data blockers, expected accuracy gain, and expected completion percentage.

## Current Baseline

Estimated current product completion: 55%.

What already exists:
- Offline upload DRS pipeline: `core/testing_pipeline.py`
- Ball detector wrapper and tracker integration: `core/ball_detector.py`, `core/ball_association.py`, `core/tracker.py`, `core/ball_tracker.py`
- LBW decision service and Law 36 logic: `core/drs_decision.py`, `core/lbw.py`, `core/lbw_engine.py`, `core/decision_mapper.py`
- WebSocket/API foundation: `core/api_server.py`, `core/testing_api.py`, `core/ws_hub.py`
- Existing Electron shell and dashboard: `dashboard/electron/main.js`, `dashboard/electron/preload.js`, `dashboard/electron/renderer/*`
- Existing calibration scaffolding: `core/calibration.py`, `core/pitch_calibration.py`, `scripts/calibrate.py`, `dashboard/electron/renderer/components/CalibrationModal.js`
- Existing training script: `scripts/train_yolo_drs.py`
- Available local footage: `12345/*.MTS`
- Available pitch reference images: `E:\PRIVATE\AVCHD\BDMV\PITCH img\*.jpeg`

Shortest critical path:
1. Build reliable calibration and pixel-to-pitch mapping.
2. Prepare/label MTS frames and train a cricket-specific YOLO model.
3. Replace guessed stump/pad boxes with detector/calibration-backed geometry.
4. Add single-camera live pipeline with replay buffer and appeal trigger.
5. Connect Electron dashboard to the live pipeline and package it.

## Modules Reused Unchanged

These should be reused as-is unless a phase test proves otherwise:
- `core/ball_association.py`
- `core/ball_tracker.py`
- `core/tracker.py`
- `core/decision_mapper.py`
- `core/drs_decision.py`
- `core/lbw.py`
- `core/lbw_engine.py`
- `core/readiness.py`
- `core/ws_hub.py`
- `core/audio_edge.py`
- `core/audio_analyzer.py`
- `core/hotspot.py`
- `core/tracking_quality.py`
- `core/testing_api.py`
- `core/testing_database.py`
- `utils/helpers.py`
- `utils/logger.py`

## Modules Reused With Extension

These remain structurally useful but need new functions/classes:
- `core/calibration.py`: add single-camera solvePnP extrinsics and world projection.
- `core/pitch_calibration.py`: keep manual profiles, add bridge to 3D extrinsic profile.
- `core/trajectory.py`: replace gravity-only ODE with drag/Magnus-aware prediction while keeping old method signature.
- `core/testing_pipeline.py`: replace static object fallbacks with detectors and calibration projection.
- `core/ball_detector.py`: keep wrapper, point it to trained cricket DRS model and class names.
- `core/api_server.py`: add single-camera live state, live appeal endpoints, dashboard payloads.
- `drs_app.py`: add `--single-camera-live` and `--camera`.
- `config/settings.py`: add config paths and live mode defaults.
- `dashboard/electron/main.js`: launch live backend mode and harden process controls.
- `dashboard/electron/preload.js`: expose safe live-mode IPC methods.
- `dashboard/electron/renderer/index.html`, `renderer.js`, `styles.css`: convert from multi-camera status wall to one-camera DRS operator dashboard.

## Modules To Rewrite Or Replace

These are currently placeholders, heuristics, or the wrong product shape:
- `DeliveryTestingPipeline._estimate_static_objects` in `core/testing_pipeline.py`: rewrite; hardcoded boxes cannot drive LBW gates.
- Current dashboard camera grid logic in `dashboard/electron/renderer/renderer.js`: rewrite for single-camera primary feed plus review panels.
- Current Electron backend launch in `dashboard/electron/main.js`: replace generic API spawn with selectable live/testing backend startup.
- Current root `main.js`: empty placeholder; either delete from packaging config or replace with a redirect to `dashboard/electron/main.js`.
- Any COCO sports-ball-only inference path: replace with trained cricket-ball/stump/pad model readiness checks.

## Real-Data Blockers

These cannot be solved honestly with code alone:
- Labeled cricket-ball boxes from `12345/*.MTS`, especially release, bounce, pad-impact, and post-impact frames.
- Labeled stump boxes from the real pitch setup.
- Labeled pad boxes from batters wearing real pads in match lighting.
- At least one calibrated camera frame from the actual match-day position.
- Ground-truth pitch landmarks clicked on real pitch images/video frames.
- Real camera FPS, shutter speed, exposure, resolution, and lens distortion profile.
- Real ball type/color, lighting, shadows, and background clutter samples.
- At least 20 real LBW/non-LBW delivery clips with known umpire/coach labels for validation.
- Real laptop performance data with the intended GPU/capture device.

## Phase 1: Calibration, Coordinate Mapping, Dataset Preparation

Estimated effort: 4-6 engineering days plus 1-2 days of data labeling setup.

Dependencies:
- Calibration must land before trustworthy trajectory, stump projection, impact gates, or dashboard pitch overlays.
- Dataset preparation can run in parallel after video paths are confirmed.

### Task 1.1 Calibration System

Why needed: Single-camera DRS lives or dies on mapping pixels to real pitch geometry. Homography-only mapping is useful for the ground plane, but LBW needs stump height and camera pose.

Modify:
- `core/calibration.py`
- `core/pitch_calibration.py`
- `scripts/calibrate.py`
- `dashboard/electron/renderer/components/CalibrationModal.js` (standalone calibration.html/.js/.css removed 2026-07-17; the workspace is the only calibration UI)
- `config/settings.py`
- `tests/test_pitch_calibration.py`

Create:
- `core/single_camera_calibration.py`
- `config/drs_config.yaml`
- `config/camera_extrinsics.example.json`
- `scripts/capture_calibration_frame.py`
- `scripts/verify_calibration_overlay.py`
- `tests/test_single_camera_calibration.py`

Implementation notes:
- Add known 3D pitch landmarks: bowling crease, popping crease, stump bases, stump tops.
- Add `cv2.solvePnP` calibration profile with `camera_matrix`, `distortion_coeffs`, `rvec`, `tvec`, RMS reprojection error, and image size.
- Save profiles to `data/calibration/single_camera_profile.json`.
- Calibration UI must load a frozen camera frame or selected pitch image and guide the operator through landmark clicks.
- Use pitch images from `E:\PRIVATE\AVCHD\BDMV\PITCH img` as dashboard/calibration reference inputs, but copy selected working assets into repo-owned `data/calibration/reference_images/` before packaging.

Expected accuracy gain: +15 to +25 percentage points in geometric reliability.

Expected completion after implementation: 63%.

### Task 1.2 Single-Camera Coordinate Mapping

Why needed: The tracker currently produces image-space points. LBW needs pitch-space `x/y/z`, bounce location, impact line, and wicket-plane intersection.

Modify:
- `core/calibration.py`
- `core/pitch_map.py`
- `core/testing_pipeline.py`
- `core/api_server.py`
- `dashboard/electron/renderer/renderer.js`
- `tests/test_trajectory.py`
- `tests/test_lbw_engine.py`

Create:
- `core/single_camera_mapper.py`
- `tests/test_single_camera_mapper.py`

Implementation notes:
- Implement ray-plane intersection for `pixel_to_world(pixel_x, pixel_y, z_m=0)`.
- Add `project_world_to_pixel(x, y, z)` for dashboard overlays.
- Add a calibrated approximation for ball height from radius/trajectory phase when true depth is unavailable.
- Keep confidence explicit: ground-plane confidence high near pitch landmarks, lower for airborne points.

Expected accuracy gain: +10 to +18 percentage points in bounce, impact, and wicket-plane localization.

Expected completion after implementation: 68%.

### Task 1.3 Dataset Preparation From MTS Videos

Why needed: COCO sports-ball detection is not enough for a small fast cricket ball. The system needs cricket-specific ball/stump/pad data from the real camera and pitch.

Modify:
- `scripts/train_yolo_drs.py`
- `training/drs_yolo_dataset.yaml`
- `.gitignore`
- `README.md`
- `docs/ACCURACY_PLAYBOOK.md`

Create:
- `scripts/prepare_mts_dataset.py`
- `scripts/extract_pitch_reference_images.py`
- `scripts/split_yolo_dataset.py`
- `training/classes.txt`
- `training/README.md`
- `tests/test_dataset_preparation.py`

Implementation notes:
- Input folders: `12345/*.MTS` and optionally `E:\PRIVATE\AVCHD\BDMV\STREAM`.
- Extract every Nth frame, plus high-motion candidate frames around the ball.
- Generate YOLO folders: `training/images/train`, `training/images/val`, `training/labels/train`, `training/labels/val`.
- Pre-annotate with existing YOLO model only as weak hints; human labels are required.
- Classes: `cricket_ball`, `stump`, `pad`.

Expected accuracy gain: +10 to +20 percentage points after labels are produced and training begins.

Expected completion after implementation: 72%.

## Phase 2: Detection Training, Stumps, Pads, Validation

Estimated effort: 5-8 engineering days plus 2-5 days labeling/training iteration.

Dependencies:
- Needs Phase 1 dataset script.
- Stump/pad detectors can start before final YOLO training using calibration-backed fallbacks.

### Task 2.1 YOLO Cricket-Ball Training

Why needed: The ball is the core signal. Missed detections produce bad tracks, bad physics, and bad decisions.

Modify:
- `scripts/train_yolo_drs.py`
- `scripts/evaluate_yolo_drs.py`
- `core/ball_detector.py`
- `core/model_selector.py`
- `models/model_evaluation.json`
- `models/public_assets_manifest.json`
- `tests/test_accuracy_gates.py`

Create:
- `training/experiments/README.md`
- `scripts/export_yolo_model.py`
- `tests/test_model_readiness.py`

Implementation notes:
- Train with `yolo11l.pt` as base.
- Required model target: `models/cricket_drs_yolo.pt`.
- Gate release: ball recall >= 0.85 on validation frames and >= 0.65 on high-blur frames.

Expected accuracy gain: +18 to +30 percentage points in ball detection/tracking reliability.

Expected completion after implementation: 78%.

### Task 2.2 Stump Detection

Why needed: Wicket prediction must know where the wickets are in image and pitch coordinates. Guessed boxes will produce false OUT/NOT OUT calls.

Modify:
- `core/testing_pipeline.py`
- `core/api_server.py`
- `dashboard/electron/renderer/renderer.js`
- `tests/test_testing_platform.py`

Create:
- `core/stump_detector.py`
- `tests/test_stump_detector.py`

Implementation notes:
- Use YOLO class when available.
- Fallback to calibration-projected stump geometry.
- Optional HSV/contour detector for white stumps should be treated as assistive, not authoritative.

Expected accuracy gain: +8 to +14 percentage points in wicket gate correctness.

Expected completion after implementation: 81%.

### Task 2.3 Pad Detection

Why needed: LBW impact is defined by where the ball hits the batter. Without pad localization, the impact gate is still a guess.

Modify:
- `core/testing_pipeline.py`
- `core/drs_decision.py`
- `core/lbw_engine.py`
- `dashboard/electron/renderer/renderer.js`
- `tests/test_lbw_engine.py`

Create:
- `core/pad_detector.py`
- `tests/test_pad_detector.py`

Implementation notes:
- Prefer YOLO pad class.
- Add temporal smoothing so pad boxes do not jump frame to frame.
- Use contour fallback only when confidence is clearly marked below detector confidence.

Expected accuracy gain: +8 to +16 percentage points in impact gate correctness.

Expected completion after implementation: 84%.

### Task 2.4 Validation Pipeline

Why needed: A demo is only credible if failures are visible before the operator trusts a decision.

Modify:
- `core/testing_pipeline.py`
- `core/readiness.py`
- `scripts/evaluate_yolo_drs.py`
- `docs/ACCURACY_PLAYBOOK.md`
- `tests/test_accuracy_gates.py`
- `tests/test_e2e_decisions.py`

Create:
- `core/validation_pipeline.py`
- `scripts/validate_delivery_set.py`
- `data/validation/README.md`
- `tests/test_validation_pipeline.py`

Implementation notes:
- Validate ball detection recall, track continuity, calibration reprojection error, stump/pad detection confidence, and LBW decision agreement.
- Store reports under `data/validation/reports/`.
- Dashboard should show readiness as `Ready`, `Degraded`, or `Demo only`.

Expected accuracy gain: +5 to +10 percentage points by preventing invalid decisions from being presented as confident.

Expected completion after implementation: 87%.

## Phase 3: Physics, Prediction, LBW Confidence

Estimated effort: 4-7 engineering days.

Dependencies:
- Needs reliable mapped tracks from Phase 1.
- Benefits strongly from Phase 2 validation clips.

### Task 3.1 Improved Trajectory Physics

Why needed: Gravity-only prediction is acceptable for toy clips but weak for real cricket deliveries with drag, bounce energy loss, spin, swing, and noisy single-camera depth.

Modify:
- `core/trajectory.py`
- `config/settings.py`
- `tests/test_trajectory.py`

Create:
- `core/trajectory_physics.py`
- `tests/test_trajectory_physics.py`

Implementation notes:
- Add drag, configurable ball mass/radius, air density, restitution, and optional Magnus term.
- Use RK4 or stable fixed-step integration.
- Keep the current `TrajectoryPredictor.predict_from_world_points` public interface.

Expected accuracy gain: +5 to +12 percentage points in projected wicket/intercept accuracy.

Expected completion after implementation: 89%.

### Task 3.2 Impact Prediction

Why needed: Impact should be found by intersection with detected pad geometry and trajectory timing, not only by whether a tracked point landed inside a box.

Modify:
- `core/testing_pipeline.py`
- `core/lbw_engine.py`
- `core/drs_decision.py`
- `tests/test_lbw_engine.py`

Create:
- `core/impact_predictor.py`
- `tests/test_impact_predictor.py`

Implementation notes:
- Intersect the 3D ball path with pad volume projected into calibrated pitch space.
- Preserve uncertainty when the ball is hidden by the batter.

Expected accuracy gain: +6 to +12 percentage points in impact gate correctness.

Expected completion after implementation: 91%.

### Task 3.3 Wicket Prediction

Why needed: The final LBW gate must answer whether the ball would hit the stumps at legal height, with uncertainty bands.

Modify:
- `core/trajectory.py`
- `core/lbw_engine.py`
- `core/drs_decision.py`
- `dashboard/electron/renderer/renderer.js`
- `tests/test_lbw_engine.py`
- `tests/test_drs_decision.py`

Create:
- `core/wicket_predictor.py`
- `tests/test_wicket_predictor.py`

Implementation notes:
- Use calibrated stump volume, not a single line.
- Add uncertainty radius from tracking quality, calibration RMS, detector confidence, and extrapolation distance.
- Output `hitting`, `missing`, or `umpires_call_zone`.

Expected accuracy gain: +6 to +10 percentage points in wicket gate correctness.

Expected completion after implementation: 93%.

### Task 3.4 LBW Confidence Scoring

Why needed: Single-camera DRS should avoid overclaiming. Confidence scoring makes the demo honest and defensible.

Modify:
- `core/drs_decision.py`
- `core/lbw_engine.py`
- `core/readiness.py`
- `dashboard/electron/renderer/renderer.js`
- `tests/test_drs_decision.py`
- `tests/test_accuracy_gates.py`

Create:
- `core/lbw_confidence.py`
- `tests/test_lbw_confidence.py`

Implementation notes:
- Score ball detection, tracking continuity, calibration error, stump/pad confidence, impact uncertainty, and wicket uncertainty.
- Make low-confidence outputs say `INCONCLUSIVE` or `UMPIRE'S CALL`, not fake certainty.

Expected accuracy gain: +5 to +8 percentage points in trustworthy recommendations.

Expected completion after implementation: 95%.

## Phase 4: Live Mode, Replay, DRS Animation, Electron Dashboard

Estimated effort: 6-10 engineering days.

Dependencies:
- Needs Phase 1 calibration and Phase 2/3 outputs for credible live decisions.
- Dashboard can be redesigned in parallel using mocked API payloads.

### Task 4.1 Live Camera Mode

Why needed: The current app has headless multi-camera and API modes, but not a dedicated low-latency single-camera DRS loop.

Modify:
- `drs_app.py`
- `core/api_server.py`
- `core/integration.py`
- `core/camera_manager.py`
- `config/settings.py`
- `tests/test_api.py`
- `tests/test_live_api_dashboard.py`

Create:
- `core/single_camera_live.py`
- `core/live_state.py`
- `tests/test_single_camera_live.py`

Implementation notes:
- Add `python drs_app.py --single-camera-live --camera 0`.
- Pipeline: capture -> detect -> track -> map -> predict -> readiness -> publish.
- Keyboard/API appeal trigger freezes the replay buffer and runs full decision.
- Target 25 fps minimum on GPU, 15 fps degraded CPU mode.

Expected accuracy gain: 0 to +5 percentage points directly; major product completion gain.

Expected completion after implementation: 96%.

### Task 4.2 Replay Buffer

Why needed: DRS is review-based. The operator needs the last few seconds around an appeal, not just the current frame.

Modify:
- `core/api_server.py`
- `core/ws_hub.py`
- `dashboard/electron/renderer/renderer.js`
- `dashboard/electron/renderer/index.html`
- `dashboard/electron/renderer/styles.css`
- `tests/test_ws_hub.py`

Create:
- `core/replay_buffer.py`
- `tests/test_replay_buffer.py`

Implementation notes:
- Keep 3-5 seconds of frames, detections, tracks, mapped points, and timestamps.
- Support seek, play, pause, export, and appeal snapshot.

Expected accuracy gain: +3 to +6 percentage points by enabling frame-accurate appeal review.

Expected completion after implementation: 97%.

### Task 4.3 DRS Animation

Why needed: A real demo needs the recognizable DRS view: ball path, pitch map, impact, wickets, and decision reveal.

Modify:
- `core/testing_pipeline.py`
- `dashboard/electron/renderer/renderer.js`
- `dashboard/electron/renderer/styles.css`
- `dashboard/electron/renderer/index.html`
- `tests/test_live_api_dashboard.py`

Create:
- `core/drs_animation.py`
- `dashboard/electron/renderer/pitch_assets.js`
- `tests/test_drs_animation.py`

Implementation notes:
- Reuse the existing Three.js scene but drive it from live `trajectory`, `predicted_extension`, `impact`, and `wicket_prediction` payloads.
- Add pitch image/background options from selected assets copied from `E:\PRIVATE\AVCHD\BDMV\PITCH img`.
- Render broadcast decision states: `PITCHING`, `IMPACT`, `WICKETS`, `OUT`, `NOT OUT`, `UMPIRE'S CALL`, `INCONCLUSIVE`.

Expected accuracy gain: 0 directly; major demo clarity gain.

Expected completion after implementation: 98%.

### Task 4.4 Electron Integration And Dashboard Rewrite

Why needed: The current dashboard is a good prototype but still shaped around six-camera monitoring. The live product needs a single-camera operator cockpit.

Modify:
- `dashboard/electron/main.js`
- `dashboard/electron/preload.js`
- `dashboard/electron/package.json`
- `dashboard/electron/renderer/index.html`
- `dashboard/electron/renderer/renderer.js`
- `dashboard/electron/renderer/styles.css`
- `dashboard/electron/renderer/components/CalibrationModal.js` (standalone calibration.html/.js/.css removed 2026-07-17)
- `README.md`
- `QUICK_START.md`

Create:
- `dashboard/electron/renderer/live_dashboard.js`
- `dashboard/electron/renderer/live_dashboard.css`
- `dashboard/electron/renderer/assets/README.md`
- `scripts/copy_pitch_dashboard_assets.ps1`
- `tests/test_electron_contract.py`

Implementation notes:
- Primary first viewport: live camera feed with ball trail, stump/pad overlays, appeal button, replay controls.
- Secondary panels: readiness gates, confidence, DRS animation, calibration status, model status.
- Add safe IPC: `startBackend`, `stopBackend`, `getBackendLogs`, `selectCalibrationImage`, `startLiveMode`.
- Use `sandbox: true`, `contextIsolation: true`, no direct `ipcRenderer` exposure.
- Avoid hardcoded local absolute paths in packaged app; development-only paths can be discovered from repo root.

Expected accuracy gain: 0 directly; major usability and deployability gain.

Expected completion after implementation: 98.5%.

## Phase 5: Startup, Packaging, Installer, Deployment Testing

Estimated effort: 4-8 engineering days.

Dependencies:
- Needs a stable Phase 4 app and known model/calibration assets.

### Task 5.1 One-Click Startup

Why needed: Match-day operation cannot depend on opening terminals and remembering commands.

Modify:
- `scripts/run_working_demo.ps1`
- `dashboard/electron/main.js`
- `dashboard/electron/package.json`
- `QUICK_START.md`

Create:
- `start_drs_live.ps1`
- `scripts/check_drs_environment.py`
- `tests/test_startup_contract.py`

Implementation notes:
- Verify Python venv, model file, calibration file, camera availability, GPU availability, free disk, and ports.
- Start Electron and backend from one command/button.

Expected accuracy gain: 0; operational reliability gain.

Expected completion after implementation: 99%.

### Task 5.2 EXE Packaging

Why needed: A desktop app should run without the operator knowing the repo layout.

Modify:
- `dashboard/electron/package.json`
- `dashboard/electron/main.js`
- `.gitignore`
- `README.md`

Create:
- `packaging/electron-builder.yml`
- `packaging/backend_manifest.json`
- `scripts/build_backend_bundle.ps1`
- `scripts/package_electron.ps1`

Implementation notes:
- Bundle backend code, model, config templates, dashboard assets, and startup checks.
- Keep large generated artifacts out of git.

Expected accuracy gain: 0; deployability gain.

Expected completion after implementation: 99.3%.

### Task 5.3 Installer Generation

Why needed: District deployment needs repeatable installation on a Windows laptop.

Modify:
- `dashboard/electron/package.json`
- `README.md`
- `QUICK_START.md`

Create:
- `packaging/installer.nsh`
- `scripts/make_installer.ps1`
- `docs/DEPLOYMENT_RUNBOOK.md`

Implementation notes:
- Create NSIS installer through electron-builder.
- Add post-install checks for model/calibration directories.

Expected accuracy gain: 0; deployability gain.

Expected completion after implementation: 99.6%.

### Task 5.4 Deployment Testing

Why needed: The real risk is not code compiling; it is the app surviving camera, light, model, and operator conditions on a pitch.

Modify:
- `docs/DEPLOYMENT_RUNBOOK.md`
- `docs/ACCURACY_PLAYBOOK.md`
- `scripts/check_drs_environment.py`

Create:
- `docs/FIELD_TEST_CHECKLIST.md`
- `scripts/run_deployment_smoke.ps1`
- `data/deployment_tests/README.md`

Implementation notes:
- Test cold start, camera connect/disconnect, calibration, appeal, replay export, offline upload fallback, and app restart.
- Record at least 20 real deliveries and compare with human-labeled outcomes.

Expected accuracy gain: +3 to +8 percentage points through calibration/model tuning.

Expected completion after implementation: 100% demo-ready prototype.

## Dependency Graph

Phase 1 calibration -> coordinate mapper -> trajectory/wicket/impact prediction -> live dashboard overlays.

Phase 1 dataset prep -> Phase 2 YOLO training -> ball/stump/pad confidence -> validation gates -> live appeal confidence.

Phase 2 detectors -> Phase 3 impact/wicket prediction -> Phase 4 DRS animation and decision reveal.

Phase 4 live backend -> Electron dashboard -> one-click startup -> packaging/installer.

## Shortest Path To A Real-Pitch Demonstration

The shortest path is not full packaging first. It is:

1. Implement single-camera calibration and mapper.
2. Extract frames from `12345/*.MTS`, label about 300-500 high-value frames, and train `models/cricket_drs_yolo.pt`.
3. Add calibration-backed stump detection and YOLO-backed pad detection.
4. Add `--single-camera-live --camera 0` with a 5-second replay buffer and appeal trigger.
5. Rewrite the Electron dashboard around one live feed, calibration readiness, replay, and DRS animation.
6. Demonstrate from a fixed camera on a real pitch with a pre-run calibration profile and a known validation clip fallback.

That path can produce a credible real-pitch demo before installer work. Packaging should start only after live calibration, detection, and replay are stable.
