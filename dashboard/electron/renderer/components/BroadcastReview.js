// Broadcast-style DRS review animation (self-contained, no Three.js). The "Broadcast
// Replay" presentation view inside the LBW Review card. Path geometry is a stylised
// perspective; all numbers/statuses come from the live decision payload.

import { resolveTimeline, timelineState } from "./animationTimeline.js";

const NS = "http://www.w3.org/2000/svg";
const MASTER_D = "M700,828 C740,700 820,560 900,452 C860,360 800,268 760,208";
const F_IMPACT = 0.55;

const TEMPLATE = `
<div class="drsa-stage" data-drsa-root>
  <svg class="drsa-field" viewBox="0 0 1520 1000" preserveAspectRatio="xMidYMid slice" role="img" aria-label="DRS ball tracking replay">
    <defs>
      <radialGradient id="drsa-lite" cx="0.5" cy="0.28" r="0.75"><stop offset="0" stop-color="#1a2733"/><stop offset="0.6" stop-color="#0a1017"/><stop offset="1" stop-color="#04070b"/></radialGradient>
      <linearGradient id="drsa-grass" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#2c5326"/><stop offset="1" stop-color="#1c3a19"/></linearGradient>
      <linearGradient id="drsa-pitch" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#8f7648"/><stop offset="0.5" stop-color="#b89a67"/><stop offset="1" stop-color="#cdb07d"/></linearGradient>
      <radialGradient id="drsa-ballg" cx="0.36" cy="0.32" r="0.75"><stop offset="0" stop-color="#ffffff"/><stop offset="0.6" stop-color="#efefe9"/><stop offset="1" stop-color="#b6b6ac"/></radialGradient>
      <radialGradient id="drsa-wglow" cx="0.5" cy="0.5" r="0.5"><stop offset="0" stop-color="#2fe07a" stop-opacity="0.9"/><stop offset="1" stop-color="#2fe07a" stop-opacity="0"/></radialGradient>
    </defs>
    <rect x="0" y="0" width="1520" height="1000" fill="url(#drsa-lite)"/>
    <rect x="0" y="150" width="1520" height="850" fill="url(#drsa-grass)"/>
    <g opacity="0.1" fill="#ffffff"><rect x="0" y="300" width="1520" height="70"/><rect x="0" y="470" width="1520" height="90"/><rect x="0" y="680" width="1520" height="120"/></g>
    <polygon points="470,850 1050,850 830,210 690,210" fill="url(#drsa-pitch)"/>
    <polygon points="470,850 1050,850 830,210 690,210" fill="#ffffff" opacity="0.06"/>
    <polygon points="668,850 852,850 772,210 748,210" fill="#000000" opacity="0.16"/>
    <line x1="512" y1="770" x2="1008" y2="770" stroke="#efeadd" stroke-width="4" opacity="0.8"/>
    <line x1="700" y1="238" x2="820" y2="238" stroke="#efeadd" stroke-width="3" opacity="0.8"/>
    <ellipse data-wick-glow cx="760" cy="196" rx="70" ry="60" fill="url(#drsa-wglow)" opacity="0"/>
    <g stroke-linecap="round">
      <line x1="746" y1="168" x2="774" y2="166" stroke="#efe7d2" stroke-width="4"/>
      <line x1="749" y1="168" x2="749" y2="214" stroke="#e7dfca" stroke-width="6"/>
      <line x1="760" y1="168" x2="760" y2="214" stroke="#efe7d2" stroke-width="6"/>
      <line x1="771" y1="168" x2="771" y2="214" stroke="#e7dfca" stroke-width="6"/>
      <ellipse cx="760" cy="216" rx="22" ry="5" fill="#00000045"/>
    </g>
    <path data-master d="${MASTER_D}" fill="none" stroke="none"/>
    <g data-trail></g>
    <g data-mk="pitch" class="drsa-ripple"><ellipse class="drsa-r1" cx="700" cy="832" rx="30" ry="13" fill="none" stroke="#2f83ff" stroke-width="3"/><ellipse class="drsa-r2" cx="700" cy="832" rx="46" ry="19" fill="none" stroke="#2f83ff" stroke-width="2"/></g>
    <g data-mk="impact" class="drsa-ripple"><ellipse class="drsa-r1" cx="900" cy="456" rx="26" ry="11" fill="none" stroke="#ff4141" stroke-width="3"/><ellipse class="drsa-r2" cx="900" cy="456" rx="40" ry="16" fill="none" stroke="#ff4141" stroke-width="2"/></g>
    <g data-ball><circle r="17" fill="url(#drsa-ballg)"/><path d="M-12,-6 Q0,3 12,-6" fill="none" stroke="#9a9a90" stroke-width="1.5"/><path d="M-12,6 Q0,-3 12,6" fill="none" stroke="#c9c9c0" stroke-width="1.3"/><circle r="17" fill="none" stroke="#ffffffcc" stroke-width="1.6"/></g>
    <g class="drsa-co" data-co="pitch"><line x1="556" y1="742" x2="700" y2="828" stroke="#cfe6ff" stroke-width="1.5" opacity="0.7"/><circle cx="700" cy="828" r="3.5" fill="#2f83ff"/><rect x="384" y="702" width="176" height="84" rx="9" fill="#0b1119" stroke="#2f83ff" stroke-width="1.4" opacity="0.96"/><rect x="384" y="702" width="5" height="84" rx="2" fill="#2f83ff"/><text x="404" y="730" class="drsa-cT">PITCHING</text><text x="404" y="754" class="drsa-cS" data-t="pitchStatus">IN LINE</text><text x="404" y="778" class="drsa-cM" fill="#6fb4ff" data-t="pitchM">--</text></g>
    <g class="drsa-co" data-co="impact"><line x1="1004" y1="446" x2="902" y2="454" stroke="#ffd0cb" stroke-width="1.5" opacity="0.7"/><circle cx="902" cy="454" r="3.5" fill="#ff3b3b"/><rect x="1004" y="404" width="176" height="84" rx="9" fill="#140b0d" stroke="#ff3b3b" stroke-width="1.4" opacity="0.96"/><rect x="1004" y="404" width="5" height="84" rx="2" fill="#ff3b3b"/><text x="1024" y="432" class="drsa-cT">IMPACT</text><text x="1024" y="456" class="drsa-cS" data-t="impactStatus">IN LINE</text><text x="1024" y="480" class="drsa-cM" fill="#ff8a80" data-t="impactM">--</text></g>
    <g class="drsa-co" data-co="wick"><line x1="884" y1="158" x2="792" y2="196" stroke="#c6f5d8" stroke-width="1.5" opacity="0.7"/><circle cx="792" cy="196" r="3.5" fill="#2fe07a"/><rect x="884" y="120" width="168" height="66" rx="9" fill="#0a1512" stroke="#2fe07a" stroke-width="1.4" opacity="0.96"/><rect x="884" y="120" width="5" height="66" rx="2" fill="#2fe07a"/><text x="904" y="150" class="drsa-cT">WICKETS</text><text x="904" y="174" class="drsa-cM" fill="#69f0a6" data-t="wickStatus">--</text></g>
  </svg>

  <div class="drsa-hud drsa-top"><div class="drsa-shield">◈</div><div><div class="drsa-brand">DRS REVIEW</div><div class="drsa-orig">CVB EDGE · BALL TRACKING</div></div></div>
  <button class="drsa-fs" data-fs type="button" title="Toggle fullscreen" aria-label="Toggle fullscreen">⛶</button>
  <button class="drsa-replay" data-replay type="button">↻ REPLAY</button>

  <div class="drsa-hud drsa-left">
    <div class="drsa-card blue" data-card="pitch"><div class="drsa-ico"><span class="drsa-pip"></span></div><div class="drsa-cbody"><div class="drsa-ct">PITCHING</div><div class="drsa-cst" data-t="pitchStatus2">--</div><div class="drsa-cmr" data-t="pitchM2">--</div></div><div class="drsa-chk">✓</div></div>
    <div class="drsa-card red" data-card="impact"><div class="drsa-ico"><span class="drsa-pball"></span></div><div class="drsa-cbody"><div class="drsa-ct">IMPACT</div><div class="drsa-cst" data-t="impactStatus2">--</div><div class="drsa-cmr" data-t="impactM2">--</div></div><div class="drsa-chk">✓</div></div>
    <div class="drsa-card green" data-card="wick"><div class="drsa-ico"><span class="drsa-pstump"></span></div><div class="drsa-cbody"><div class="drsa-ct">WICKETS</div><div class="drsa-cst" data-t="wickStatus2">--</div></div><div class="drsa-chk">✓</div></div>
  </div>

  <div class="drsa-hud drsa-right">
    <div class="drsa-conf"><div class="drsa-cl">CONFIDENCE</div><svg viewBox="0 0 120 120" class="drsa-ring"><circle cx="60" cy="60" r="50" fill="none" stroke="#ffffff1c" stroke-width="9"/><circle data-ring cx="60" cy="60" r="50" fill="none" stroke="#2fd07a" stroke-width="9" stroke-linecap="round" transform="rotate(-90 60 60)"/><text data-ring-pct x="60" y="70" text-anchor="middle" class="drsa-pct">0%</text></svg><div class="drsa-ch" data-t="confLabel">--<br><span>CONFIDENCE</span></div></div>
    <div class="drsa-binfo"><div class="drsa-bh">BALL INFO</div><div class="drsa-brow"><span>DELIVERY SPEED</span><b><span data-t="speed">--</span> <i>km/h</i></b></div><div class="drsa-brow"><span>SPIN RATE</span><b><span data-t="spin">--</span> <i>rpm</i></b></div><div class="drsa-brow"><span>BALL TRACKING</span><b>CVB EDGE <em class="drsa-live"></em></b></div></div>
  </div>

  <div class="drsa-hud drsa-bottom">
    <div class="drsa-bstats"><div class="drsa-bs"><span>SPEED</span><b data-t="speed2">--</b><i>km/h</i></div><div class="drsa-bs"><span>SPIN</span><b data-t="spin2">--</b><i>rpm</i></div></div>
    <div class="drsa-track"><div class="drsa-node" data-node="pitch"><span class="drsa-nic blue">●</span><em>PITCHING</em><small data-t="pitchStatus3">--</small></div><div class="drsa-ln"></div><div class="drsa-node" data-node="impact"><span class="drsa-nic red">●</span><em>IMPACT</em><small data-t="impactStatus3">--</small></div><div class="drsa-ln"></div><div class="drsa-node" data-node="wick"><span class="drsa-nic green">▮▮▮</span><em>WICKETS</em><small data-t="wickStatus3">--</small></div></div>
    <div class="drsa-final" data-final><span>FINAL DECISION</span><b data-t="decision">--</b><i>LBW</i></div>
  </div>

  <div class="drsa-hud drsa-foot"><span>TECHNOLOGY: <b>CVB TRACK</b></span><span>SYSTEM: <b>CVB EDGE</b></span><span>RELIABILITY: <b data-t="reliability">--</b></span></div>

  <div class="drsa-idle" data-idle>Awaiting review — request an appeal to render the broadcast replay.</div>
</div>`;

export class BroadcastReview {
  constructor(container, options = {}) {
    this.el = container;
    // Cadence comes from the timeline module, never hard-coded here. Callers may pass a
    // preset name ("ipl"/"tv"/"debug"), a partial override, or nothing (IPL default).
    this.timeline = resolveTimeline(options.timeline);
    this.el.innerHTML = TEMPLATE;
    this.root = this.el.querySelector("[data-drsa-root]");
    this.master = this.el.querySelector("[data-master]");
    this.ball = this.el.querySelector("[data-ball]");
    this.ring = this.el.querySelector("[data-ring]");
    this.ringPct = this.el.querySelector("[data-ring-pct]");
    this.trail = this.el.querySelector("[data-trail]");
    this.wickGlow = this.el.querySelector("[data-wick-glow]");
    this.finalBox = this.el.querySelector("[data-final]");
    this.idle = this.el.querySelector("[data-idle]");
    this.mk = { p: this.el.querySelector('[data-mk="pitch"]'), i: this.el.querySelector('[data-mk="impact"]') };
    this.co = { p: this.el.querySelector('[data-co="pitch"]'), i: this.el.querySelector('[data-co="impact"]'), w: this.el.querySelector('[data-co="wick"]') };
    this.card = { p: this.el.querySelector('[data-card="pitch"]'), i: this.el.querySelector('[data-card="impact"]'), w: this.el.querySelector('[data-card="wick"]') };
    this.node = { p: this.el.querySelector('[data-node="pitch"]'), i: this.el.querySelector('[data-node="impact"]'), w: this.el.querySelector('[data-node="wick"]') };
    this.confTarget = 0;
    this.hitting = false;
    this.active = false;
    // Which path the ball animates along — declared, never silently assumed.
    this.animationSource = { real: false, reason: "fallback template" };
    this.raf = null;
    this.startTimer = null;
    this.ML = this.master.getTotalLength();
    this.RL = this.ring.getTotalLength();
    this.ring.style.strokeDasharray = this.RL;
    this._buildTrail();
    this.el.querySelector("[data-replay]").addEventListener("click", () => this.play());
    const fsBtn = this.el.querySelector("[data-fs]");
    if (fsBtn) {
      fsBtn.addEventListener("click", () => {
        if (document.fullscreenElement) document.exitFullscreen();
        else this.root.requestFullscreen?.();
      });
    }
    this._reset();
  }

  _set(key, value) {
    this.el.querySelectorAll(`[data-t="${key}"]`).forEach((n) => { n.textContent = value; });
  }

  _buildTrail() {
    this.trail.innerHTML = "";
    this.ghosts = [];
    const N = 30;
    for (let i = 1; i <= N; i++) {
      const fg = i / (N + 1);
      const pt = this.master.getPointAtLength(fg * this.ML);
      const sc = 0.5 + ((pt.y - 208) / 620) * 0.78;
      // Colour splits at the REAL impact fraction (blue pre-impact, green post), not a
      // constant — so the trail reads correctly for each delivery's actual impact point.
      const impactAt = this.impactFrac != null ? this.impactFrac : F_IMPACT;
      const col = Math.abs(fg - impactAt) < 0.05 ? "#ff3b3b" : fg < impactAt ? "#2f83ff" : "#2fe07a";
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
      this.trail.appendChild(g); this.ghosts.push(g);
    }
  }

  _toggle(on, list) { list.forEach((e) => e && e.classList.toggle("on", on)); }

  _reset() {
    cancelAnimationFrame(this.raf);
    this._start = null;
    this._toggle(false, [this.mk.p, this.mk.i, this.co.p, this.co.i, this.co.w, this.card.p, this.card.i, this.card.w, this.node.p, this.node.i, this.node.w, this.finalBox]);
    this.wickGlow.style.opacity = 0;
    this.ghosts.forEach((g) => { g.style.opacity = 0; });
    this.ring.style.strokeDashoffset = this.RL;
    this.ringPct.textContent = "0%";
    const s0 = this.master.getPointAtLength(0);
    this.ball.setAttribute("transform", `translate(${s0.x},${s0.y})`);
  }

  // Swap the cadence at runtime (preset name, partial override, or full timeline) — lets a
  // debug/TV toggle change durations without the renderer knowing any timing policy.
  setTimeline(timeline) { this.timeline = resolveTimeline(timeline); }

  play() {
    if (!this.active) return;
    this._reset();
    const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
    const frame = (ts) => {
      if (this._start == null) this._start = ts;
      // The renderer is a dumb consumer: timelineState() owns cadence, play() maps it to DOM.
      const s = timelineState(this.timeline, ts - this._start);
      // Ball travels along the path (frozen while travelP is 0 — the release hold), trailing.
      const p = ease(s.travelP);
      const pt = this.master.getPointAtLength(p * this.ML);
      this.ball.setAttribute("transform", `translate(${pt.x},${pt.y})`);
      this.ghosts.forEach((g) => { g.style.opacity = g._fg <= p ? 0.28 + 0.62 * Math.max(0, 1 - (p - g._fg) / 0.55) : 0; });
      // Staged reveals — one beat at a time, each after its hold.
      if (s.showPitching) this._toggle(true, [this.mk.p, this.co.p, this.card.p, this.node.p]);
      if (s.showImpact) this._toggle(true, [this.mk.i, this.co.i, this.card.i, this.node.i]);
      if (s.showWickets) { this._toggle(true, [this.co.w, this.card.w, this.node.w]); if (this.hitting) this.wickGlow.style.opacity = 1; }
      // Decision banner expands and the confidence ring fills, last.
      if (s.showDecision) {
        this._toggle(true, [this.finalBox]);
        this.ring.style.strokeDashoffset = this.RL * (1 - this.confTarget * s.ringP);
        this.ringPct.textContent = `${Math.round(this.confTarget * 100 * s.ringP)}%`;
      }
      if (!s.done) this.raf = requestAnimationFrame(frame);
    };
    this.raf = requestAnimationFrame(frame);
  }

  update(decision) {
    const d = decision || {};
    const status = String(d.status || "WAITING").toUpperCase();
    this.active = status !== "WAITING";
    this.idle.style.display = this.active ? "none" : "";

    // Choose the ball path. Preference order: the canonical trajectory object (this
    // renderer projects its observed points into its own viewBox), then a legacy baked
    // trajectory_svg string, else the template. We record which — and why — so the UI
    // states "real trajectory" vs "fallback" instead of anyone inferring it from
    // noticing every clip looks the same. Fallback is only for a STRUCTURALLY invalid
    // trajectory, never merely a low-confidence one.
    let realPath = null;
    let reason = "no trajectory supplied";
    let lowConfidence = false;
    let sourceLabel = "none";
    let markerPts = null;
    if (typeof d.trajectory_svg === "string" && d.trajectory_svg.length > 20) {
      realPath = d.trajectory_svg;
      reason = "analyzed trajectory";
      sourceLabel = "svg";
    } else {
      // Gather observed PIXEL points from whichever surface supplied them and project
      // THAT. This is what stops every live review from showing the same template: the
      // testing pipeline supplies a canonical trajectory.observed.points object, but the
      // LIVE review carries its real per-delivery path in `overlay` (ball_path /
      // measured_px), not in trajectory.observed — so we read both.
      const obs = this._observedPoints(d);
      if (obs && obs.points.length >= 2) {
        const conf = Number(d.trajectory?.confidence ?? d.overall_confidence ?? d.ball_confidence ?? 0);
        const projected = this._projectPoints(obs.points, conf);
        if (projected) {
          realPath = projected.d;
          reason = obs.source === "overlay" ? `${projected.reason} (live)` : projected.reason;
          lowConfidence = projected.lowConfidence;
          sourceLabel = obs.source;
          markerPts = obs.points;   // remember the pixel points for bounce/impact placement
        } else {
          reason = "trajectory invalid";
        }
      } else if (d.trajectory && !Array.isArray(d.trajectory) && Array.isArray(d.trajectory.reasons) && d.trajectory.reasons.length) {
        reason = d.trajectory.reasons.join("; ");
      }
    }
    this.master.setAttribute("d", realPath || MASTER_D);
    this.ML = this.master.getTotalLength();
    this.animationSource = realPath
      ? { real: true, reason, lowConfidence, confidence: Number(d.trajectory?.confidence ?? 0) }
      : { real: false, reason };

    // Place the bounce/impact markers at their REAL positions along the path (not a
    // fixed F_IMPACT fraction). When we projected a real path, derive each fraction from
    // the actual data; otherwise fall back to the template constants. `this.impactFrac`
    // also drives the ball-trail colour split, so the whole reveal stays consistent.
    this.impactFrac = this._impactFraction(d, markerPts);
    this.pitchFrac = this._bounceFraction(d, markerPts);
    this._buildTrail();  // rebuild so the trail colours split at the real impact fraction
    this._placeMarker(this.mk.p, realPath ? this.pitchFrac : null, 700, 832);
    this._placeMarker(this.mk.i, realPath ? this.impactFrac : null, 900, 456);

    // Self-declaring diagnostic (open DevTools console): reports the EXACT trajectory
    // shape BroadcastReview received and whether it projected the real path or fell back
    // to the template. This is the "print the object passed in" check, live in the app.
    const t = d.trajectory;
    const shape = Array.isArray(t) ? "array (world pts)"
      : Array.isArray(t?.observed?.points) ? "object.observed.points (canonical)"
      : t ? "object (no observed points)" : "none";
    console.info(
      "[BroadcastReview] trajectory shape:", shape,
      "| path source:", sourceLabel,
      "| overlay ball_path pts:", Array.isArray(d.overlay?.ball_path) ? d.overlay.ball_path.length : 0,
      "| animation:", this.animationSource.real ? "REAL" : "FALLBACK", "—", this.animationSource.reason,
    );

    const decText = d.decision || d.outcome || status.replace(/_/g, " ");
    const decUpper = String(decText).toUpperCase();
    const isOut = decUpper.includes("OUT") && !decUpper.includes("NOT");
    const isNotOut = decUpper.includes("NOT");
    this.finalBox.classList.toggle("out", isOut);
    this.finalBox.classList.toggle("notout", isNotOut);

    this.confTarget = Math.max(0, Math.min(1, Number(d.overall_confidence ?? d.ball_confidence ?? 0)));
    const confLabel = this.confTarget >= 0.85 ? "VERY HIGH" : this.confTarget >= 0.65 ? "HIGH" : this.confTarget >= 0.4 ? "MEDIUM" : "LOW";
    // Don't show a pixel-derived speed as if it were real: on an uncalibrated camera
    // the ball moves in depth, so that number is meaningless. Show N/A until calibrated.
    const uncalibrated = d.trajectory && d.trajectory.geometry_source && d.trajectory.geometry_source !== "calibration";
    const speed = uncalibrated ? "N/A" : (d.ball_speed_kmh != null ? Number(d.ball_speed_kmh).toFixed(1) : "--");
    const spin = d.spin_rate_rpm != null ? d.spin_rate_rpm : (d.spin_rpm != null ? d.spin_rpm : "--");
    const pitchStatus = d.pitching_status || (this.active ? "IN LINE" : "--");
    const impactStatus = d.impact_status || (this.active ? "IN LINE" : "--");
    const wickStatus = String(d.wicket_status || d.wicket_zone_status || "--").replace(/_/g, " ").toUpperCase();
    this.hitting = /HIT/.test(wickStatus);
    const alongM = (pt) => (pt && pt.x != null ? `${Math.abs(Number(pt.x)).toFixed(2)} m` : "--");
    const pitchM = alongM(d.bounce_point);
    const impactM = alongM(d.impact_point);

    this._set("pitchStatus", pitchStatus); this._set("pitchStatus2", pitchStatus); this._set("pitchStatus3", pitchStatus);
    this._set("impactStatus", impactStatus); this._set("impactStatus2", impactStatus); this._set("impactStatus3", impactStatus);
    this._set("wickStatus", wickStatus); this._set("wickStatus2", wickStatus); this._set("wickStatus3", wickStatus);
    this._set("pitchM", pitchM); this._set("pitchM2", pitchM);
    this._set("impactM", impactM); this._set("impactM2", impactM);
    this._set("speed", speed); this._set("speed2", speed);
    this._set("spin", spin); this._set("spin2", spin);
    this._set("decision", this.active ? decText : "--");
    this._set("reliability", String(d.reliability || (this.active ? "--" : "--")).toUpperCase());
    this.el.querySelector('[data-t="confLabel"]').innerHTML = `${confLabel}<br><span>CONFIDENCE</span>`;

    if (this.active) {
      clearTimeout(this.startTimer);
      this.startTimer = setTimeout(() => this.play(), 200);
    } else {
      this._reset();
    }
  }

  // Project the canonical trajectory's observed points into THIS svg's viewBox
  // (0 0 1520 1000). The ball path runs from the pitch/release end (~700,828, bottom)
  // up to the stumps (~760,208, top): delivery progression drives the down-pitch axis,
  // while the ball's lateral pixel position drives sideways deviation (swing/spin) — so
  // different deliveries genuinely curve differently. The corridor narrows toward the
  // stumps for perspective. Returns null for a structurally invalid trajectory so the
  // caller falls back to the template. Presentation-only: no analysis happens here.
  // Gather observed PIXEL points from the best available source, returning
  // {points:[{x_px, frame_id}], source}. The canonical trajectory object (testing
  // pipeline) wins; otherwise the LIVE review's real per-delivery pixel path from the
  // overlay (ball_path preferred, else measured_px). Returns null if none has ≥2 points.
  _observedPoints(d) {
    const t = d.trajectory;
    if (t && !Array.isArray(t) && t.valid !== false && Array.isArray(t.observed?.points)) {
      let pts = t.observed.points;
      if (t.observed.display_end_frame != null) {
        pts = pts.filter((p) => p.frame_id <= t.observed.display_end_frame);
      }
      if (pts.length >= 2) return { points: pts, source: "canonical" };
    }
    const ov = d.overlay || {};
    const fromPairs = (arr) => arr.map((p, i) => ({
      frame_id: i,
      x_px: Array.isArray(p) ? Number(p[0]) : Number(p.x ?? p.x_px),
      y_px: Array.isArray(p) ? Number(p[1]) : Number(p.y ?? p.y_px),
    }));
    if (Array.isArray(ov.ball_path) && ov.ball_path.length >= 2) return { points: fromPairs(ov.ball_path), source: "overlay" };
    if (Array.isArray(ov.measured_px) && ov.measured_px.length >= 2) return { points: fromPairs(ov.measured_px), source: "overlay" };
    return null;
  }

  // Project observed pixel points into THIS svg's viewBox (0 0 1520 1000). The path runs
  // from the pitch/release end (~700,828, bottom) up to the stumps (~760,208, top):
  // delivery progression drives the down-pitch axis, the ball's lateral pixel position
  // drives sideways deviation (swing/spin) — so different deliveries genuinely curve
  // differently. Corridor narrows toward the stumps for perspective. Presentation only.
  _projectPoints(pts, conf) {
    if (!Array.isArray(pts) || pts.length < 2) return null;
    const xs = pts.map((p) => Number(p.x_px)).filter((v) => Number.isFinite(v));
    if (xs.length < 2) return null;
    const minX = Math.min(...xs);
    const spanX = Math.max(1, Math.max(...xs) - minX);
    const n = pts.length - 1;
    const coords = pts.map((p, i) => {
      const prog = i / n;                                   // 0 = release, 1 = stumps
      const centerX = 700 + 60 * prog;                      // follows the master centreline
      const baseY = 828 - 620 * prog;                       // bottom → top of the pitch
      const lat = 2 * ((Number(p.x_px) - minX) / spanX) - 1; // [-1, 1] across the corridor
      const halfW = 150 * (1 - prog) + 40 * prog;           // corridor narrows with distance
      return [centerX + lat * halfW, baseY];
    });
    const d = "M" + coords.map(([x, y], i) => `${i ? "L" : ""}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
    const c = Number(conf ?? 0);
    const lowConfidence = c > 0 && c < 0.65;
    return { d, lowConfidence, reason: lowConfidence ? "real trajectory (low confidence)" : "real trajectory" };
  }

  // Fraction [0,1] along the path where IMPACT occurs, from real data. Priority:
  // (1) the overlay's impact pixel → nearest observed point; (2) a canonical track
  // trimmed AT impact ("Impact confirmed") ends at impact → 1.0; else the F_IMPACT
  // template constant. This replaces the hardcoded 0.55 so the impact ripple sits on
  // the ball's actual impact position for each delivery.
  _impactFraction(d, pts) {
    const ov = d.overlay || {};
    const f = this._nearestFraction(pts, ov.impact_px);
    if (f != null) return f;
    const t = d.trajectory;
    const endReason = t && !Array.isArray(t) ? (t.observed?.end_reason || "") : "";
    if (/impact/i.test(String(endReason))) return 1.0;   // display range ends at impact
    return null;   // no real impact data → hide the marker rather than place it at a guess
  }

  // Fraction where the ball PITCHES (bounces). Bounce is often not detected (bounce_px
  // null) — then return null so the caller hides the marker rather than faking a spot.
  _bounceFraction(d, pts) {
    const ov = d.overlay || {};
    return this._nearestFraction(pts, ov.bounce_px);
  }

  // Index of the observed point nearest a target pixel [x,y] (or {x,y}/{x_px,y_px}),
  // returned as a 0..1 fraction. null if there's no usable target or too few points.
  _nearestFraction(pts, target) {
    if (!Array.isArray(pts) || pts.length < 2 || target == null) return null;
    const tx = Array.isArray(target) ? target[0] : (target.x ?? target.x_px);
    const ty = Array.isArray(target) ? target[1] : (target.y ?? target.y_px);
    if (!Number.isFinite(Number(tx))) return null;
    let best = -1, bestD = Infinity;
    pts.forEach((p, i) => {
      const dx = Number(p.x_px) - Number(tx);
      const dy = Number.isFinite(Number(ty)) ? Number(p.y_px) - Number(ty) : 0;
      const dd = dx * dx + dy * dy;
      if (dd < bestD) { bestD = dd; best = i; }
    });
    return best >= 0 ? best / (pts.length - 1) : null;
  }

  // Move a marker group (built at template coords baseX/baseY) onto the path at `frac`.
  // frac == null hides it — used when the underlying event wasn't detected (e.g. bounce).
  _placeMarker(el, frac, baseX, baseY) {
    if (!el) return;
    if (frac == null) { el.style.display = "none"; return; }
    el.style.display = "";
    const pt = this.master.getPointAtLength(Math.max(0, Math.min(1, frac)) * this.ML);
    el.setAttribute("transform", `translate(${(pt.x - baseX).toFixed(1)},${(pt.y - baseY).toFixed(1)})`);
  }

  destroy() { cancelAnimationFrame(this.raf); clearTimeout(this.startTimer); }
}
