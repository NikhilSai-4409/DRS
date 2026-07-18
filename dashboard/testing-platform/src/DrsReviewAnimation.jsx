import React, { useEffect, useRef } from "react";

// Broadcast-style DRS review animation. The path geometry is a stylised
// perspective representation; all numbers/statuses/decision are driven by the
// real pipeline summary. (The exact bend can later be mapped from
// summary.trajectory_3d — kept canonical here so it always reads clearly.)

const NS = "http://www.w3.org/2000/svg";
const MASTER_D = "M700,828 C740,700 820,560 900,452 C860,360 800,268 760,208";
const F_IMPACT = 0.55; // impact fraction along the master path

function metersAlong(mm) {
  if (!mm || mm.along_mm == null) return null;
  return `${(Math.abs(mm.along_mm) / 1000).toFixed(2)} m`;
}

export default function DrsReviewAnimation({ summary, originalDecision }) {
  const rootRef = useRef(null);
  const active = Boolean(summary);

  // ---- derive display values from real data ----
  const decision = summary?.lbw_recommendation || "PENDING";
  const decUpper = String(decision).toUpperCase();
  const isOut = /(^|[^T])OUT/.test(decUpper) && !decUpper.includes("NOT");
  const isNotOut = decUpper.includes("NOT");
  const confTarget = Math.max(0, Math.min(1, summary?.confidence_score || 0));
  const confLabel = confTarget >= 0.85 ? "VERY HIGH" : confTarget >= 0.65 ? "HIGH" : confTarget >= 0.4 ? "MEDIUM" : "LOW";
  const speed = summary?.ball_speed_kmh != null ? summary.ball_speed_kmh : "--";
  const spin = summary?.spin_rate_rpm != null ? summary.spin_rate_rpm : (summary?.spin_rpm != null ? summary.spin_rpm : "--");
  const pitchStatus = summary?.pitching_status || (active ? "IN LINE" : "--");
  const impactStatus = summary?.impact_status || (active ? "IN LINE" : "--");
  const wicketStatusRaw = summary?.wicket_status || summary?.predicted_wicket_impact || "--";
  const wicketStatus = String(wicketStatusRaw).replace(/_/g, " ").toUpperCase();
  const hitting = /HIT/.test(wicketStatus);
  const pitchM = metersAlong(summary?.pitching_location_mm) || "--";
  const impactM = metersAlong(summary?.impact_location_mm) || "--";

  useEffect(() => {
    const root = rootRef.current;
    if (!root) return undefined;
    const q = (sel) => root.querySelector(sel);
    const master = q("#drsa-master");
    const ball = q("#drsa-ball");
    const ring = q("#drsa-ring");
    const ringPct = q("#drsa-ringPct");
    const trail = q("#drsa-trail");
    const wickGlow = q("#drsa-wickGlow");
    const fin = q(".drsa-final");
    if (!master || !ball || !trail) return undefined;

    const ML = master.getTotalLength();
    const RL = ring.getTotalLength();
    ring.style.strokeDasharray = RL;

    const mk = { p: q("#drsa-mPitch"), i: q("#drsa-mImpact") };
    const co = { p: q("#drsa-coPitch"), i: q("#drsa-coImpact"), w: q("#drsa-coWick") };
    const card = { p: q("#drsa-cardP"), i: q("#drsa-cardI"), w: q("#drsa-cardW") };
    const node = { p: q("#drsa-n1"), i: q("#drsa-n2"), w: q("#drsa-n3") };

    // build the ball-path trail (actual balls, not a line)
    trail.innerHTML = "";
    const N = 30;
    const ghosts = [];
    for (let i = 1; i <= N; i++) {
      const fg = i / (N + 1);
      const pt = master.getPointAtLength(fg * ML);
      const sc = 0.5 + ((pt.y - 208) / 620) * 0.78;
      const col = Math.abs(fg - F_IMPACT) < 0.05 ? "#ff3b3b" : fg < F_IMPACT ? "#2f83ff" : "#2fe07a";
      const g = document.createElementNS(NS, "g");
      g.setAttribute("transform", `translate(${pt.x},${pt.y}) scale(${sc.toFixed(3)})`);
      const halo = document.createElementNS(NS, "circle");
      halo.setAttribute("r", 20); halo.setAttribute("fill", col); halo.setAttribute("opacity", 0.2);
      const b = document.createElementNS(NS, "circle");
      b.setAttribute("r", 15); b.setAttribute("fill", "url(#drsa-ballg)");
      const s = document.createElementNS(NS, "path");
      s.setAttribute("d", "M-11,-5 Q0,3 11,-5"); s.setAttribute("fill", "none");
      s.setAttribute("stroke", "#9a9a90"); s.setAttribute("stroke-width", 1.4);
      g.appendChild(halo); g.appendChild(b); g.appendChild(s);
      g.style.opacity = 0; g._fg = fg;
      trail.appendChild(g); ghosts.push(g);
    }

    const setOn = (on, list) => list.forEach((e) => e && e.classList.toggle("on", on));
    const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
    let start = null;
    let raf = null;
    const DUR = 4200;

    const reset = () => {
      cancelAnimationFrame(raf);
      start = null;
      setOn(false, [mk.p, mk.i, co.p, co.i, co.w, card.p, card.i, card.w, node.p, node.i, node.w, fin]);
      if (wickGlow) wickGlow.style.opacity = 0;
      ghosts.forEach((g) => { g.style.opacity = 0; });
      ring.style.strokeDashoffset = RL;
      ringPct.textContent = "0%";
      const s0 = master.getPointAtLength(0);
      ball.setAttribute("transform", `translate(${s0.x},${s0.y})`);
    };

    const frame = (ts) => {
      if (!start) start = ts;
      const raw = Math.min(1, (ts - start) / DUR);
      const p = ease(raw);
      const pt = master.getPointAtLength(p * ML);
      ball.setAttribute("transform", `translate(${pt.x},${pt.y})`);
      ghosts.forEach((g) => {
        g.style.opacity = g._fg <= p ? 0.28 + 0.62 * Math.max(0, 1 - (p - g._fg) / 0.55) : 0;
      });
      ring.style.strokeDashoffset = RL * (1 - confTarget * p);
      ringPct.textContent = `${Math.round(confTarget * 100 * p)}%`;
      if (p > 0.05) setOn(true, [mk.p, co.p, card.p, node.p]);
      if (p >= F_IMPACT) setOn(true, [mk.i, co.i, card.i, node.i]);
      if (p >= 0.985) {
        setOn(true, [co.w, card.w, node.w, fin]);
        if (wickGlow && hitting) wickGlow.style.opacity = 1;
      }
      if (raw < 1) raf = requestAnimationFrame(frame);
    };

    reset();
    let startTimer = null;
    if (active) startTimer = setTimeout(() => { raf = requestAnimationFrame(frame); }, 350);

    root._replay = () => { reset(); raf = requestAnimationFrame(frame); };

    return () => {
      cancelAnimationFrame(raf);
      if (startTimer) clearTimeout(startTimer);
      root._replay = null;
    };
    // re-run whenever the delivery / decision changes
  }, [active, confTarget, hitting, decision, pitchM, impactM]);

  const replay = () => rootRef.current?._replay?.();

  return (
    <div className="drsa-wrap">
      <div className="drsa-stage" ref={rootRef}>
        <svg className="drsa-field" viewBox="0 0 1520 1000" preserveAspectRatio="xMidYMid slice" role="img" aria-label="DRS ball tracking replay">
          <defs>
            <radialGradient id="drsa-lite" cx="0.5" cy="0.28" r="0.75"><stop offset="0" stopColor="#1a2733" /><stop offset="0.6" stopColor="#0a1017" /><stop offset="1" stopColor="#04070b" /></radialGradient>
            <linearGradient id="drsa-grass" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#2c5326" /><stop offset="1" stopColor="#1c3a19" /></linearGradient>
            <linearGradient id="drsa-pitch" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#8f7648" /><stop offset="0.5" stopColor="#b89a67" /><stop offset="1" stopColor="#cdb07d" /></linearGradient>
            <radialGradient id="drsa-ballg" cx="0.36" cy="0.32" r="0.75"><stop offset="0" stopColor="#ffffff" /><stop offset="0.6" stopColor="#efefe9" /><stop offset="1" stopColor="#b6b6ac" /></radialGradient>
            <radialGradient id="drsa-wglow" cx="0.5" cy="0.5" r="0.5"><stop offset="0" stopColor="#2fe07a" stopOpacity="0.9" /><stop offset="1" stopColor="#2fe07a" stopOpacity="0" /></radialGradient>
          </defs>
          <rect x="0" y="0" width="1520" height="1000" fill="url(#drsa-lite)" />
          <rect x="0" y="150" width="1520" height="850" fill="url(#drsa-grass)" />
          <g opacity="0.1" fill="#ffffff"><rect x="0" y="300" width="1520" height="70" /><rect x="0" y="470" width="1520" height="90" /><rect x="0" y="680" width="1520" height="120" /></g>
          <polygon points="470,850 1050,850 830,210 690,210" fill="url(#drsa-pitch)" />
          <polygon points="470,850 1050,850 830,210 690,210" fill="#ffffff" opacity="0.06" />
          <polygon points="668,850 852,850 772,210 748,210" fill="#000000" opacity="0.16" />
          <line x1="512" y1="770" x2="1008" y2="770" stroke="#efeadd" strokeWidth="4" opacity="0.8" />
          <line x1="700" y1="238" x2="820" y2="238" stroke="#efeadd" strokeWidth="3" opacity="0.8" />
          <ellipse id="drsa-wickGlow" cx="760" cy="196" rx="70" ry="60" fill="url(#drsa-wglow)" opacity="0" />
          <g strokeLinecap="round">
            <line x1="746" y1="168" x2="774" y2="166" stroke="#efe7d2" strokeWidth="4" />
            <line x1="749" y1="168" x2="749" y2="214" stroke="#e7dfca" strokeWidth="6" />
            <line x1="760" y1="168" x2="760" y2="214" stroke="#efe7d2" strokeWidth="6" />
            <line x1="771" y1="168" x2="771" y2="214" stroke="#e7dfca" strokeWidth="6" />
            <ellipse cx="760" cy="216" rx="22" ry="5" fill="#00000045" />
          </g>
          <path id="drsa-master" d={MASTER_D} fill="none" stroke="none" />
          <g id="drsa-trail" />
          <g id="drsa-mPitch" className="drsa-ripple"><ellipse className="drsa-r1" cx="700" cy="832" rx="30" ry="13" fill="none" stroke="#2f83ff" strokeWidth="3" /><ellipse className="drsa-r2" cx="700" cy="832" rx="46" ry="19" fill="none" stroke="#2f83ff" strokeWidth="2" /></g>
          <g id="drsa-mImpact" className="drsa-ripple"><ellipse className="drsa-r1" cx="900" cy="456" rx="26" ry="11" fill="none" stroke="#ff4141" strokeWidth="3" /><ellipse className="drsa-r2" cx="900" cy="456" rx="40" ry="16" fill="none" stroke="#ff4141" strokeWidth="2" /></g>
          <g id="drsa-ball"><circle r="17" fill="url(#drsa-ballg)" /><path d="M-12,-6 Q0,3 12,-6" fill="none" stroke="#9a9a90" strokeWidth="1.5" /><path d="M-12,6 Q0,-3 12,6" fill="none" stroke="#c9c9c0" strokeWidth="1.3" /><circle r="17" fill="none" stroke="#ffffffcc" strokeWidth="1.6" /></g>
          <g className="drsa-co" id="drsa-coPitch"><line x1="556" y1="742" x2="700" y2="828" stroke="#cfe6ff" strokeWidth="1.5" opacity="0.7" /><circle cx="700" cy="828" r="3.5" fill="#2f83ff" /><rect x="384" y="702" width="176" height="84" rx="9" fill="#0b1119" stroke="#2f83ff" strokeWidth="1.4" opacity="0.96" /><rect x="384" y="702" width="5" height="84" rx="2" fill="#2f83ff" /><text x="404" y="730" className="drsa-cT">PITCHING</text><text x="404" y="754" className="drsa-cS">{pitchStatus}</text><text x="404" y="778" className="drsa-cM" fill="#6fb4ff">{pitchM}</text></g>
          <g className="drsa-co" id="drsa-coImpact"><line x1="1004" y1="446" x2="902" y2="454" stroke="#ffd0cb" strokeWidth="1.5" opacity="0.7" /><circle cx="902" cy="454" r="3.5" fill="#ff3b3b" /><rect x="1004" y="404" width="176" height="84" rx="9" fill="#140b0d" stroke="#ff3b3b" strokeWidth="1.4" opacity="0.96" /><rect x="1004" y="404" width="5" height="84" rx="2" fill="#ff3b3b" /><text x="1024" y="432" className="drsa-cT">IMPACT</text><text x="1024" y="456" className="drsa-cS">{impactStatus}</text><text x="1024" y="480" className="drsa-cM" fill="#ff8a80">{impactM}</text></g>
          <g className="drsa-co" id="drsa-coWick"><line x1="884" y1="158" x2="792" y2="196" stroke="#c6f5d8" strokeWidth="1.5" opacity="0.7" /><circle cx="792" cy="196" r="3.5" fill="#2fe07a" /><rect x="884" y="120" width="168" height="66" rx="9" fill="#0a1512" stroke="#2fe07a" strokeWidth="1.4" opacity="0.96" /><rect x="884" y="120" width="5" height="66" rx="2" fill="#2fe07a" /><text x="904" y="150" className="drsa-cT">WICKETS</text><text x="904" y="174" className="drsa-cM" fill="#69f0a6">{wicketStatus}</text></g>
        </svg>

        <div className="drsa-hud drsa-top"><div className="drsa-shield">◈</div><div><div className="drsa-brand">DRS REVIEW</div><div className="drsa-orig">{originalDecision ? <>ORIGINAL DECISION: <b>{originalDecision}</b></> : <>CVB EDGE · BALL TRACKING</>}</div></div></div>
        <button className="drsa-replay" onClick={replay} type="button">↻ REPLAY</button>

        <div className="drsa-hud drsa-left">
          <div className="drsa-card blue" id="drsa-cardP"><div className="drsa-ico"><span className="drsa-pip" /></div><div className="drsa-cbody"><div className="drsa-ct">PITCHING</div><div className="drsa-cst">{pitchStatus}</div><div className="drsa-cmr">{pitchM}</div></div><div className="drsa-chk">✓</div></div>
          <div className="drsa-card red" id="drsa-cardI"><div className="drsa-ico"><span className="drsa-pball" /></div><div className="drsa-cbody"><div className="drsa-ct">IMPACT</div><div className="drsa-cst">{impactStatus}</div><div className="drsa-cmr">{impactM}</div></div><div className="drsa-chk">✓</div></div>
          <div className="drsa-card green" id="drsa-cardW"><div className="drsa-ico"><span className="drsa-pstump" /></div><div className="drsa-cbody"><div className="drsa-ct">WICKETS</div><div className="drsa-cst">{wicketStatus}</div></div><div className="drsa-chk">✓</div></div>
        </div>

        <div className="drsa-hud drsa-right">
          <div className="drsa-conf"><div className="drsa-cl">CONFIDENCE</div><svg viewBox="0 0 120 120" className="drsa-ring"><circle cx="60" cy="60" r="50" fill="none" stroke="#ffffff1c" strokeWidth="9" /><circle id="drsa-ring" cx="60" cy="60" r="50" fill="none" stroke="#2fd07a" strokeWidth="9" strokeLinecap="round" transform="rotate(-90 60 60)" /><text id="drsa-ringPct" x="60" y="70" textAnchor="middle" className="drsa-pct">0%</text></svg><div className="drsa-ch">{confLabel}<br /><span>CONFIDENCE</span></div></div>
          <div className="drsa-binfo"><div className="drsa-bh">BALL INFO</div><div className="drsa-brow"><span>DELIVERY SPEED</span><b>{speed} <i>km/h</i></b></div><div className="drsa-brow"><span>SPIN RATE</span><b>{spin} <i>rpm</i></b></div><div className="drsa-brow"><span>BALL TRACKING</span><b>CVB EDGE <em className="drsa-live" /></b></div></div>
        </div>

        <div className="drsa-hud drsa-bottom">
          <div className="drsa-bstats"><div className="drsa-bs"><span>SPEED</span><b>{speed}</b><i>km/h</i></div><div className="drsa-bs"><span>SPIN</span><b>{spin}</b><i>rpm</i></div></div>
          <div className="drsa-track"><div className="drsa-node" id="drsa-n1"><span className="drsa-nic blue">●</span><em>PITCHING</em><small>{pitchStatus}</small></div><div className="drsa-ln" /><div className="drsa-node" id="drsa-n2"><span className="drsa-nic red">●</span><em>IMPACT</em><small>{impactStatus}</small></div><div className="drsa-ln" /><div className="drsa-node" id="drsa-n3"><span className="drsa-nic green">▮▮▮</span><em>WICKETS</em><small>{wicketStatus}</small></div></div>
          <div className={`drsa-final ${isOut ? "out" : isNotOut ? "notout" : ""}`}><span>FINAL DECISION</span><b>{active ? decision : "--"}</b><i>LBW</i></div>
        </div>

        <div className="drsa-hud drsa-foot"><span>TECHNOLOGY: <b>CVB TRACK</b></span><span>SYSTEM: <b>CVB EDGE</b></span><span>RELIABILITY: <b>{(summary?.reliability || "--").toUpperCase()}</b></span></div>

        {!active && <div className="drsa-idle">Awaiting delivery analysis — upload &amp; process a delivery to render the review.</div>}
      </div>
    </div>
  );
}
