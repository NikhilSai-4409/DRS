import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Activity,
  BarChart3,
  Camera,
  CheckCircle2,
  Download,
  FileJson,
  FileText,
  Gauge,
  ImageDown,
  RefreshCw,
  UploadCloud,
  Video,
} from "lucide-react";
import "./styles.css";
import "./drs-widgets.css";
import DrsReviewAnimation from "./DrsReviewAnimation";
import WicketImpactPanel from "./WicketImpactPanel";

const API = "http://127.0.0.1:8765";

const optionDefs = [
  ["ball_detection", "Ball detection"],
  ["ball_tracking", "Ball tracking"],
  ["trajectory_prediction", "Trajectory prediction"],
  ["lbw_analysis", "LBW analysis"],
  ["edge_detection", "Edge detection analysis"],
  ["replay_generation", "Replay generation"],
];

const defaultOptions = Object.fromEntries(optionDefs.map(([key]) => [key, true]));

function App() {
  const [files, setFiles] = useState([]);
  const [options, setOptions] = useState(defaultOptions);
  const [job, setJob] = useState(null);
  const [showBroadcast, setShowBroadcast] = useState(false);
  const [progress, setProgress] = useState(0);
  const [dragging, setDragging] = useState(false);
  const [backend, setBackend] = useState("checking");
  const [calibration, setCalibration] = useState(null);
  const pollRef = useRef(null);
  const calibrationInputRef = useRef(null);

  useEffect(() => {
    fetch(`${API}/api/health`)
      .then((res) => res.ok ? setBackend("online") : setBackend("offline"))
      .catch(() => setBackend("offline"));
    refreshCalibrationStatus();
  }, []);

  useEffect(() => () => clearInterval(pollRef.current), []);

  const mode = files.length === 2 ? "Dual camera mode" : "Single camera mode";
  const result = job?.result;
  const summary = result?.summary;
  const fps = Number(result?.cameras?.[0]?.fps || 30);

  const upload = async () => {
    if (files.length < 1 || files.length > 2) return;
    setProgress(8);
    const form = new FormData();
    form.append("video_a", files[0]);
    if (files[1]) form.append("video_b", files[1]);
    form.append("options_json", JSON.stringify(options));

    const response = await fetch(`${API}/api/test/upload`, { method: "POST", body: form });
    if (!response.ok) throw new Error("Upload failed");
    const created = await response.json();
    setJob(created);
    setProgress(35);
    clearInterval(pollRef.current);
    pollRef.current = setInterval(() => pollJob(created.job_id), 1400);
  };

  const pollJob = async (jobId) => {
    const response = await fetch(`${API}/api/test/jobs/${jobId}`);
    if (!response.ok) return;
    const updated = await response.json();
    setJob(updated);
    if (updated.status === "processing") setProgress((old) => Math.min(88, old + 8));
    if (["completed", "review_inconclusive", "complete", "failed"].includes(updated.status)) {
      setProgress(updated.status === "failed" ? 0 : 100);
      clearInterval(pollRef.current);
    }
  };

  const refreshCalibrationStatus = async () => {
    const response = await fetch(`${API}/api/calibration/status`);
    if (response.ok) setCalibration(await response.json());
  };

  const importCalibration = async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    const response = await fetch(`${API}/api/calibration/import`, { method: "POST", body: form });
    if (response.ok) await refreshCalibrationStatus();
    event.target.value = "";
  };

  const onDrop = (event) => {
    event.preventDefault();
    setDragging(false);
    const selected = Array.from(event.dataTransfer.files).filter((file) => file.type.startsWith("video/")).slice(0, 2);
    setFiles(selected);
  };

  const exportUrl = (name) => result ? `${API}/api/testing/jobs/${job.job_id}/exports/${name}` : "#";

  return (
    <main className="app-shell">
      <CalibrationBanner
        status={calibration}
        onImport={() => calibrationInputRef.current?.click()}
      />
      <input
        ref={calibrationInputRef}
        className="hidden-input"
        type="file"
        accept=".json,application/json"
        onChange={importCalibration}
      />
      <header className="topbar">
        <div className="brand">
          <div className="logo">DRS</div>
          <div>
            <h1>Cricket DRS Testing Platform</h1>
            <p>Offline delivery upload analysis for single and dual camera workflows</p>
          </div>
        </div>
        <div className={`backend ${backend}`}>
          <span />
          Backend {backend}
        </div>
      </header>

      <section className="workspace">
        <aside className="left-panel">
          <Panel title="Upload section" icon={<UploadCloud size={18} />}>
            <div
              className={`dropzone ${dragging ? "dragging" : ""}`}
              onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
              onDragLeave={() => setDragging(false)}
              onDrop={onDrop}
            >
              <UploadCloud size={34} />
              <strong>Upload 1 or 2 cricket delivery videos</strong>
              <span>Drag and drop MP4/MOV files, or choose from disk</span>
              <input
                type="file"
                accept="video/*"
                multiple
                onChange={(event) => setFiles(Array.from(event.target.files).slice(0, 2))}
              />
            </div>
            <div className="file-list">
              {files.length === 0 && <span>No videos selected</span>}
              {files.map((file, index) => (
                <div className="file-row" key={`${file.name}-${index}`}>
                  <Video size={16} />
                  <span>Camera {index + 1}</span>
                  <strong>{file.name}</strong>
                </div>
              ))}
            </div>
            <div className="progress">
              <div style={{ width: `${progress}%` }} />
            </div>
            <button className="primary" disabled={files.length === 0 || backend !== "online"} onClick={upload}>
              Process delivery
            </button>
          </Panel>

          <Panel title="Analysis options" icon={<Activity size={18} />}>
            <div className="option-grid">
              {optionDefs.map(([key, label]) => (
                <label className="check" key={key}>
                  <input
                    type="checkbox"
                    checked={options[key]}
                    onChange={(event) => setOptions({ ...options, [key]: event.target.checked })}
                  />
                  <span>{label}</span>
                </label>
              ))}
            </div>
          </Panel>

          <Panel title="Testing mode" icon={<Camera size={18} />}>
            <div className="mode-card">
              <strong>{mode}</strong>
              <p>{files.length === 2 ? "Synchronizes two feeds and fuses tracking confidence." : "Analyzes one feed with approximate depth and trajectory uncertainty."}</p>
            </div>
          </Panel>
        </aside>

        <section className="center-panel">
          <Panel title="Output dashboard" icon={<BarChart3 size={18} />} fill>
            {result?.geometry_source === "heuristic" && (
              <div className="geometry-warning">
                ⚠ Using estimated geometry -- import calibration data for accurate LBW decisions.
              </div>
            )}
            <div className="broadcast-preview-bar">
              <button
                type="button"
                className={`broadcast-preview-btn ${showBroadcast ? "on" : ""}`}
                onClick={() => setShowBroadcast((v) => !v)}
              >
                {showBroadcast ? "▾ Hide broadcast replay" : "▸ Preview broadcast replay"}
              </button>
            </div>
            {showBroadcast && <DrsReviewAnimation summary={summary} />}
            <ReviewPlayer
              originalFile={files[0]}
              analyzedSrc={result ? exportUrl("video") : null}
              fps={fps}
            />
            <div className="evidence-grid">
              <Metric label="Bounce point" value={formatPoint(summary?.pitching_location)} />
              <Metric label="Impact point" value={formatPoint(summary?.impact_location)} />
              <Metric label="Stump collision" value={summary?.predicted_wicket_impact || "waiting"} />
              <Metric label="Reliability" value={summary?.reliability || "waiting"} />
              <Metric label="Slow motion replay" value={result ? "generated" : "waiting"} />
              <Metric label="Frame review" value={result ? `${result.cameras?.[0]?.frames_processed || 0} frames` : "waiting"} />
              <Metric label="Sync error" value={formatMs(result?.sync_status?.sync_error_ms)} />
              <Metric label="Replay fps" value={result?.sync_status?.replay_fps ? `${result.sync_status.replay_fps} fps` : "--"} />
            </div>
          </Panel>
        </section>

        <aside className="right-panel">
          <Panel title="DRS result panel" icon={<Gauge size={18} />}>
            <div className="decision">
              <span>LBW recommendation</span>
              <strong className={summary?.lbw_recommendation === "OUT" ? "out" : ""}>{formatDecision(summary?.lbw_recommendation)}</strong>
              <div className="confidence">
                <div style={{ width: `${Math.round((summary?.confidence_score || 0) * 100)}%` }} />
              </div>
              <small>Confidence {summary ? `${Math.round(summary.confidence_score * 100)}%` : "--"}</small>
            </div>
            <Metric label="Ball speed" value={summary ? `${summary.ball_speed_kmh} km/h` : "--"} />
            <Metric label="Pitching location" value={formatPoint(summary?.pitching_location)} />
            <Metric label="Impact location" value={formatPoint(summary?.impact_location)} />
            <Metric label="Uncertainty" value={summary ? `${Math.round(summary.uncertainty * 100)}%` : "--"} />
            <Metric label="Tracking reliability" value={summary?.reliability || "--"} />
            <Metric label="Failed gates" value={summary?.gate?.failed_gates?.length ? summary.gate.failed_gates.join(", ") : "none"} />
          </Panel>

          <Panel title="Wickets · ball impact" icon={<CheckCircle2 size={18} />}>
            <WicketImpactPanel summary={summary} />
          </Panel>

          <Panel title="Export features" icon={<Download size={18} />}>
            <div className="export-grid">
              <ExportButton href={exportUrl("video")} disabled={!result} icon={<Video size={16} />} label="Analyzed video" />
              <ExportButton href={exportUrl("animation")} disabled={!result} icon={<Video size={16} />} label="Clean DRS animation" />
              <ExportButton href={exportUrl("pdf")} disabled={!result} icon={<FileText size={16} />} label="DRS report PDF" />
              <ExportButton href={exportUrl("json")} disabled={!result} icon={<FileJson size={16} />} label="Tracking JSON" />
              <ExportButton href={exportUrl("csv")} disabled={!result} icon={<FileText size={16} />} label="Tracking CSV" />
            </div>
          </Panel>

          <Panel title="Accuracy gates" icon={<Activity size={18} />}>
            <div className="quality-list">
              {(summary?.tracking_quality || []).map((item, index) => (
                <div className="quality-card" key={index}>
                  <strong>Camera {index + 1}: {item.reliability || "unknown"}</strong>
                  <span>Coverage {Math.round((item.detection_coverage || 0) * 100)}% | Max gap {item.max_missing_gap || 0} | Smooth {Math.round((item.trajectory_smoothness || 0) * 100)}%</span>
                  <span>Avg conf {Math.round((item.mean_confidence || 0) * 100)}% | Jump rejects {item.jump_rejections || 0}</span>
                  {(item.warnings || []).map((warning) => <p key={warning}>{warning}</p>)}
                </div>
              ))}
              {!summary && <span className="muted">Quality gates appear after analysis.</span>}
            </div>
          </Panel>

          <Panel title="Readiness" icon={<CheckCircle2 size={18} />}>
            <Readiness title="Detector model" item={result?.model_status} type="model" />
            <Readiness title="Camera calibration" item={result?.calibration_status} type="calibration" />
            <Readiness title="Camera sync / replay" item={result?.sync_status} type="sync" />
          </Panel>
        </aside>
      </section>

      <footer className="bottom-bar">
        <div>
          <strong>Status</strong>
          <span>{job ? `${job.job_id} | ${job.status}` : "Waiting for delivery upload"}</span>
        </div>
        <div>
          <strong>Pipeline</strong>
          <span>Detect ball, bat, pads, stumps, track, sync, predict, export</span>
        </div>
        <button onClick={() => job?.job_id && pollJob(job.job_id)}>
          <RefreshCw size={16} /> Refresh
        </button>
      </footer>
    </main>
  );
}

function Panel({ title, icon, children, fill }) {
  return (
    <section className={`panel ${fill ? "fill" : ""}`}>
      <header>
        <div>{icon}<span>{title}</span></div>
      </header>
      {children}
    </section>
  );
}

function CalibrationBanner({ status, onImport }) {
  if (!status) return null;
  if (!status.calibrated) {
    return (
      <div className="calibration-banner warn">
        <span>Calibration data missing -- geometry estimates only.</span>
        <button type="button" onClick={onImport}>Import calibration</button>
      </div>
    );
  }
  return (
    <div className="calibration-banner good">
      <span>Calibrated · {status.camera_count} cameras · {formatDate(status.last_calibrated)}</span>
    </div>
  );
}

function ReviewPlayer({ originalFile, analyzedSrc, fps }) {
  const originalRef = useRef(null);
  const analyzedRef = useRef(null);
  const [sideBySide, setSideBySide] = useState(false);
  const [speed, setSpeed] = useState(1);
  const originalUrl = useMemo(() => originalFile ? URL.createObjectURL(originalFile) : null, [originalFile]);
  const frameStep = 1 / (Number(fps) || 30);
  const primaryRef = sideBySide ? analyzedRef : (analyzedSrc ? analyzedRef : originalRef);

  useEffect(() => () => {
    if (originalUrl) URL.revokeObjectURL(originalUrl);
  }, [originalUrl]);

  const stepFrame = (direction) => {
    [originalRef.current, analyzedRef.current].filter(Boolean).forEach((video) => {
      video.currentTime = Math.max(0, video.currentTime + (direction * frameStep));
    });
  };

  const setPlaybackSpeed = (value) => {
    setSpeed(value);
    [originalRef.current, analyzedRef.current].filter(Boolean).forEach((video) => {
      video.playbackRate = value;
    });
  };

  const saveFrame = () => {
    const video = primaryRef.current;
    if (!video || video.readyState < 2) return;
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const context = canvas.getContext("2d");
    context.drawImage(video, 0, 0, canvas.width, canvas.height);
    canvas.toBlob((blob) => {
      if (!blob) return;
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = `frame_${Math.round(video.currentTime * 1000)}.png`;
      link.click();
      URL.revokeObjectURL(link.href);
    }, "image/png");
  };

  const syncTime = (source, target) => {
    if (!sideBySide || !source || !target) return;
    if (Math.abs(target.currentTime - source.currentTime) > 0.08) {
      target.currentTime = source.currentTime;
    }
  };

  const syncPlay = (playing) => {
    if (!sideBySide) return;
    const videos = [originalRef.current, analyzedRef.current].filter(Boolean);
    videos.forEach((video) => {
      if (playing && video.paused) video.play();
      if (!playing && !video.paused) video.pause();
    });
  };

  return (
    <div className="review-player">
      <div className="review-controls">
        <button type="button" onClick={() => stepFrame(-1)}>◀ Prev frame</button>
        <button type="button" onClick={() => stepFrame(1)}>Next frame ▶</button>
        <div className="speed-controls">
          {[0.25, 0.5, 1, 2].map((value) => (
            <button
              key={value}
              type="button"
              className={speed === value ? "active" : ""}
              onClick={() => setPlaybackSpeed(value)}
            >
              {value}x
            </button>
          ))}
        </div>
        <button type="button" onClick={saveFrame}><ImageDown size={16} /> Save frame</button>
        <button type="button" className={sideBySide ? "active" : ""} onClick={() => setSideBySide(!sideBySide)}>
          Side by side
        </button>
      </div>
      <div className="video-grid two">
        <VideoPane
          title="Original video"
          src={originalUrl}
          refProp={originalRef}
          onTimeUpdate={() => syncTime(originalRef.current, analyzedRef.current)}
          onPlay={() => syncPlay(true)}
          onPause={() => syncPlay(false)}
        />
        <VideoPane
          title="Ball tracking + trajectory overlay"
          src={analyzedSrc}
          refProp={analyzedRef}
          onTimeUpdate={() => syncTime(analyzedRef.current, originalRef.current)}
          onPlay={() => syncPlay(true)}
          onPause={() => syncPlay(false)}
        />
      </div>
    </div>
  );
}

function VideoPane({ title, src, refProp, onTimeUpdate, onPlay, onPause }) {
  return (
    <div className="video-pane">
      <div>{title}</div>
      {src ? (
        <video
          ref={refProp}
          src={src}
          controls
          muted
          onTimeUpdate={onTimeUpdate}
          onPlay={onPlay}
          onPause={onPause}
        />
      ) : (
        <div className="empty-video">Video appears after upload/analysis</div>
      )}
    </div>
  );
}

function Metric({ label, value }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function ExportButton({ href, disabled, icon, label }) {
  return (
    <a className={`export ${disabled ? "disabled" : ""}`} href={disabled ? undefined : href} download>
      {icon}
      <span>{label}</span>
    </a>
  );
}

function Readiness({ title, item, type }) {
  const usable = Boolean(item?.usable);
  return (
    <div className="readiness">
      <strong>{title}</strong>
      <span className={usable ? "good" : "warn"}>
        {item ? (usable ? "Usable" : "Not ready") : "Waiting"}
      </span>
      <ReadinessMetrics item={item} type={type} />
      <p>{item?.reason || item?.message || "Run an analysis to see readiness status."}</p>
    </div>
  );
}

function ReadinessMetrics({ item, type }) {
  if (!item) return null;
  if (type === "model") {
    return <small>mAP50 {fmtPct(item.map50)} | Ball recall {fmtPct(item.ball_recall)} | Precision {fmtPct(item.precision)}</small>;
  }
  if (type === "calibration") {
    return <small>Reproj {fmtNum(item.reprojection_error_px)}px | Homography {fmtNum(item.homography_error_cm)}cm | Pitch {fmtNum(item.pitch_coordinate_error_cm)}cm</small>;
  }
  if (type === "sync") {
    return <small>Sync {formatMs(item.sync_error_ms)} | Replay {item.replay_fps || "--"} fps | Drops {item.dropped_frames ?? "--"}</small>;
  }
  return null;
}

function formatPoint(point) {
  if (!point) return "--";
  if (Array.isArray(point)) return `${Math.round(point[0])}, ${Math.round(point[1])} px`;
  return String(point);
}

function formatMs(value) {
  return value === null || value === undefined ? "--" : `${Number(value).toFixed(2)} ms`;
}

function fmtPct(value) {
  return value === null || value === undefined ? "--" : `${Math.round(Number(value) * 100)}%`;
}

function fmtNum(value) {
  return value === null || value === undefined ? "--" : Number(value).toFixed(2);
}

function formatDate(value) {
  if (!value) return "date unknown";
  return new Date(value).toLocaleString();
}

function formatDecision(value) {
  if (!value) return "Pending";
  return String(value).toLowerCase().replaceAll("_", " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

createRoot(document.getElementById("root")).render(<App />);
