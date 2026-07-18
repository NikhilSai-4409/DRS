import React from "react";

// Static stump-impact panel. Shows the resolved wicket state straight from the
// pipeline summary (wicket_status + lbw_engine). No animation.

const STATES = {
  hitting: { v: "HITTING", c: "#e0483a", badge: "OUT", bt: "#ff6a5a", dx: 172, dy: 118, show: true, px: "M300,40 L172,118" },
  umpire: { v: "UMPIRE'S CALL", c: "#f4b13a", badge: "UMPIRE'S CALL", bt: "#ffcc66", dx: 196, dy: 120, show: true, px: "M300,44 L196,120" },
  missing: { v: "MISSING", c: "#2fbf6a", badge: "NOT OUT", bt: "#5fe89a", dx: 236, dy: 126, show: true, px: "M300,52 L236,126" },
  waiting: { v: "WAITING", c: "#7f95a5", badge: "PENDING", bt: "#9fb3c2", dx: 172, dy: 118, show: false, px: "M300,40 L172,118" },
};

function resolveState(summary) {
  const raw = String(summary?.wicket_status || summary?.predicted_wicket_impact || "").toUpperCase();
  if (raw.includes("HIT")) return "hitting";
  if (raw.includes("UMPIRE")) return "umpire";
  if (raw.includes("MISS")) return "missing";
  return "waiting";
}

function titleCase(v) {
  if (!v) return "--";
  return String(v).charAt(0).toUpperCase() + String(v).slice(1).toLowerCase();
}

export default function WicketImpactPanel({ summary }) {
  const key = resolveState(summary);
  const s = STATES[key];
  const lbw = summary?.lbw_engine || {};
  const stump = lbw.stump_hit_zone && lbw.stump_hit_zone !== "UNKNOWN" ? titleCase(lbw.stump_hit_zone) : "--";
  const heightMm = lbw.impact_height_mm ?? lbw.impact_height ?? null;
  const height = key === "waiting" || heightMm == null ? "--" : `${(Number(heightMm) / 1000).toFixed(2)} m`;
  const prob = lbw.stump_hit_probability != null ? lbw.stump_hit_probability : summary?.confidence_score;
  const conf = key === "waiting" || prob == null ? "--" : `${Math.round(Number(prob) * 100)}%`;

  return (
    <div className="wip">
      <div className="wip-head">
        <span className="wip-eyebrow">WICKETS · BALL IMPACT</span>
        <span className="wip-badge" style={{ borderColor: s.c, color: s.bt }}>{s.badge}</span>
      </div>
      <div className="wip-view">
        <svg viewBox="0 0 320 180" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Front-on stump impact">
          <rect x="0" y="0" width="320" height="180" rx="8" fill="#0c1116" />
          <line x1="40" y1="150" x2="280" y2="150" stroke="#2a3742" strokeWidth="2" />
          <rect x="120" y="52" width="80" height="98" rx="4" fill="#ffffff08" stroke="#3aa0ff" strokeWidth="1.4" strokeDasharray="4 4" />
          <g strokeLinecap="round">
            <line x1="140" y1="150" x2="140" y2="66" stroke="#e9e3d2" strokeWidth="7" />
            <line x1="160" y1="150" x2="160" y2="64" stroke="#efe9d8" strokeWidth="7" />
            <line x1="180" y1="150" x2="180" y2="66" stroke="#e9e3d2" strokeWidth="7" />
            <line x1="133" y1="64" x2="167" y2="64" stroke="#d8d2c0" strokeWidth="4" />
            <line x1="153" y1="63" x2="187" y2="63" stroke="#d8d2c0" strokeWidth="4" />
          </g>
          {s.show && (
            <>
              <path d={s.px} fill="none" stroke={s.c} strokeWidth="2.4" strokeDasharray="7 6" opacity="0.9" />
              <circle cx={s.dx} cy={s.dy} r="22" fill={s.c} opacity="0.22" />
              <circle cx={s.dx} cy={s.dy} r="9" fill={s.c} />
              <circle cx={s.dx} cy={s.dy} r="9" fill="none" stroke="#ffffffbb" strokeWidth="2" />
            </>
          )}
        </svg>
      </div>
      <div className="wip-verdict" style={{ color: s.c }}>{s.v}</div>
      <div className="wip-tiles">
        <div className="wip-tile"><span>STUMP</span><b>{stump}</b></div>
        <div className="wip-tile"><span>HEIGHT</span><b>{height}</b></div>
        <div className="wip-tile"><span>CONFIDENCE</span><b>{conf}</b></div>
      </div>
    </div>
  );
}
