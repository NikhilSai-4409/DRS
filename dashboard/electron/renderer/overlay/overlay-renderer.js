// Shared overlay engine for the dashboard — the JS twin of core/overlay_renderer.py
// + core/animation_director.py. It consumes the SAME OverlayPayload the backend
// produces (decision.overlay), so the live Review Mode and the saved replay speak
// one visual language. Timing lives in AnimationDirector; drawing in OverlayRenderer;
// colours in THEMES. Nothing here knows how a review was computed.

function clamp(v) { return Math.max(0, Math.min(1, v)); }
function ease(v) { v = clamp(v); return v * v * (3 - 2 * v); }

function shade(hex, factor) {
  const n = parseInt(hex.replace("#", ""), 16);
  const r = Math.round(((n >> 16) & 255) * factor);
  const g = Math.round(((n >> 8) & 255) * factor);
  const b = Math.round((n & 255) * factor);
  return `rgb(${r},${g},${b})`;
}

const _BASE_THEME = { stumpIdle: "#cccccc", glow: 0.32, statusOut: "#eb3c3c", statusNotOut: "#5ec86e", statusInfo: "#b6b6b6" };
export const THEMES = {
  broadcast: { ..._BASE_THEME, measured: "#f2f2f2", transition: "#ffa41e", predicted: "#c37ac3", ribbon: "150,90,170", bounce: "#ffa41e", impact: "#eb3c3c", stumpHit: "#eb3c3c" },
  ipl: { ..._BASE_THEME, measured: "#ffffff", transition: "#ffb020", predicted: "#3ca0ff", ribbon: "180,60,150", bounce: "#ffc83c", impact: "#ff3b3b", stumpHit: "#ff3b3b", glow: 0.4 },
  // Per-review colour identity (operator recognises the review by colour).
  lbw: { ..._BASE_THEME, measured: "#f2f2f2", transition: "#ffa41e", predicted: "#46aaeb", ribbon: "70,110,150", bounce: "#ffa41e", impact: "#eb3c3c", stumpHit: "#eb3c3c" },
  wide: { ..._BASE_THEME, measured: "#f5f5f5", transition: "#be78cd", predicted: "#be78cd", ribbon: "175,90,160", bounce: "#be78cd", impact: "#be78cd", stumpHit: "#be78cd" },
  noball: { ..._BASE_THEME, measured: "#f5f5f5", transition: "#eb3c3c", predicted: "#eb4646", ribbon: "175,60,60", bounce: "#eb4646", impact: "#eb3232", stumpHit: "#eb3c3c" },
  runout: { ..._BASE_THEME, measured: "#f5f5f5", transition: "#ffc800", predicted: "#5ac85a", ribbon: "90,150,80", bounce: "#ffc800", impact: "#ffc800", stumpHit: "#ffc800" },
  stumping: { ..._BASE_THEME, measured: "#f5f5f5", transition: "#ffc800", predicted: "#46dceb", ribbon: "60,170,150", bounce: "#ffc800", impact: "#ffc800", stumpHit: "#ffc800" },
  edge: { ..._BASE_THEME, measured: "#f5f5f5", transition: "#ffc800", predicted: "#5aa0c8", ribbon: "70,120,150", bounce: "#ffc800", impact: "#eb3232", stumpHit: "#eb3c3c" },
};
const THEME_BY_TYPE = { lbw: "lbw", wide: "wide", noball: "noball", no_ball: "noball", front_foot: "noball", runout: "runout", run_out: "runout", stumping: "stumping", edge: "edge", ultraedge: "edge" };
export function themeFor(reviewType) { return THEMES[THEME_BY_TYPE[String(reviewType || "").toLowerCase()] || "broadcast"] || THEMES.broadcast; }

// Per-review-type timelines — mirror of core/timelines/*. Each carries its overlay
// cues, camera script (freeze/zoom/framestep/slowmo), and colour theme.
export const TIMELINES = {
  lbw: { key: "lbw", duration: 4.9, theme: "lbw", verdict: [4.5, 0.3], cards_start: 3.3,
    cues: { measured: [1.0, 1.2], bounce: [1.8, 0.12], impact: [2.2, 0.2], predicted: [2.3, 0.8], stumps: [3.1, 0.3] },
    camera: [["freeze", 0.0, 0.4], ["zoom", 0.4, 0.6]] },
  wide: { key: "wide", duration: 4.4, theme: "wide", verdict: [4.0, 0.3], cards_start: 3.2,
    cues: { ball: [1.0, 0.6], wideline: [1.8, 0.5], distance: [2.6, 0.4] }, camera: [["freeze", 0.0, 0.4], ["zoom", 0.4, 0.6]] },
  noball: { key: "noball", duration: 4.4, theme: "noball", verdict: [4.0, 0.3], cards_start: 3.2,
    cues: { crease: [0.9, 0.5], foot: [1.6, 0.6], distance: [2.6, 0.4] }, camera: [["freeze", 0.0, 0.4], ["zoom", 0.4, 0.6]] },
  runout: { key: "runout", duration: 5.0, theme: "runout", verdict: [4.6, 0.3], cards_start: 3.6,
    cues: { crease: [0.9, 0.5], bat: [1.6, 0.6], bails: [2.6, 0.4], framestep: [3.1, 1.0] },
    camera: [["freeze", 0.0, 0.4], ["framestep", 0.5, 1.8], ["zoom", 2.6, 0.9]] },
  stumping: { key: "stumping", duration: 5.2, theme: "stumping", verdict: [4.8, 0.3], cards_start: 3.8,
    cues: { ultraedge: [0.9, 0.5], foot: [1.6, 0.6], crease: [2.4, 0.5], bails: [3.0, 0.4] },
    camera: [["freeze", 0.0, 0.4], ["zoom", 0.5, 0.6], ["slowmo", 2.4, 1.6]] },
  edge: { key: "edge", duration: 4.0, theme: "edge", verdict: [3.6, 0.3], cards_start: 2.8,
    cues: { ultraedge: [1.0, 0.8], hotspot: [2.0, 0.6] }, camera: [["freeze", 0.0, 0.4], ["zoom", 0.4, 0.6]] },
};
const TL_ALIASES = { no_ball: "noball", front_foot: "noball", frontfoot: "noball", ultraedge: "edge", ultra_edge: "edge", snicko: "edge", run_out: "runout" };
// TimelineFactory: resolves a review_type to its declarative Timeline. Directors consume it.
export function timelineFor(reviewType) {
  const raw = String(reviewType || "lbw").toLowerCase();
  return TIMELINES[TL_ALIASES[raw] || raw] || TIMELINES.lbw;
}

// AnimationDirector — overlay reveals only (no camera). Twin of core/animation_director.py.
export class AnimationDirector {
  constructor(duration = 4.9) { this.duration = duration; }
  stateAt(timeline, payload, t) {
    payload = payload || {};
    const tl = timeline;
    const cards = payload.decision_cards || [];
    const hitting = !!payload.hitting;
    const ramp = (start, dur) => clamp((t - start) / Math.max(1e-6, dur));
    const reveals = {};
    for (const key in tl.cues) reveals[key] = ease(ramp(tl.cues[key][0], tl.cues[key][1]));
    const measured = reveals.measured || 0;
    const impactVisible = "impact" in tl.cues && measured >= 1.0;
    const impactStart = (tl.cues.impact || [2.2, 0.2])[0];
    const stumpsCue = "stumps" in tl.cues ? "stumps" : ("bails" in tl.cues ? "bails" : null);
    let vib = 0;
    if (hitting && stumpsCue) { const s0 = tl.cues[stumpsCue][0]; if (t >= s0 && t <= s0 + 0.6) vib = Math.sin((t - s0) * 40) * (1 - (t - s0) / 0.6); }
    return {
      t, duration: tl.duration, review_type: tl.key, reveals,
      measured_reveal: measured,
      predicted_reveal: reveals.predicted || 0,
      bounce_visible: (reveals.bounce || 0) > 0.05,
      impact_visible: impactVisible,
      impact_pulse: impactVisible ? Math.abs(Math.sin((t - impactStart) * Math.PI * 3)) : 0,
      stumps_reveal: stumpsCue ? (reveals[stumpsCue] || 0) : 0,
      stump_vibration: vib,
      hitting,
      cards: cards.map((_, i) => ease(ramp(tl.cards_start + i * 0.32, 0.25))),
      verdict_reveal: ease(ramp(tl.verdict[0], tl.verdict[1])),
    };
  }
  fullState(timeline, payload) { return this.stateAt(timeline, payload, timeline.duration); }
  stateForProgress(timeline, payload, p) { return this.stateAt(timeline, payload, clamp(p) * timeline.duration); }
}

// CameraDirector — camera moves only (freeze/zoom/frame-step/slow-mo). Twin of core/camera_director.py.
export class CameraDirector {
  constructor(zoomMax = 0.08) { this.zoomMax = zoomMax; }
  stateAt(timeline, t) {
    const tl = timeline;
    const state = { t, duration: tl.duration, zoom: 0, freeze: false, frame_step: false, slowmo: false, pan: 0 };
    for (const [action, start, dur] of tl.camera || []) {
      if (action === "zoom") state.zoom = clamp((t - start) / Math.max(1e-6, dur)) * this.zoomMax;
      else if (action === "freeze") state.freeze = t < start + dur;
      else if (action === "framestep") state.frame_step = t >= start && t <= start + dur;
      else if (action === "slowmo") state.slowmo = t >= start && t <= start + dur;
      else if (action === "pan") state.pan = clamp((t - start) / Math.max(1e-6, dur));
    }
    return state;
  }
  fullState(timeline) { return this.stateAt(timeline, timeline.duration); }
  stateForProgress(timeline, p) { return this.stateAt(timeline, clamp(p) * timeline.duration); }
}

const VERDICT_COLORS = {
  out: "#eb3c3c", wide: "#eb3c3c", "no ball": "#eb3c3c", edge: "#eb3c3c",
  "not out": "#5ec86e", "not wide": "#5ec86e", legal: "#5ec86e", "no edge": "#5ec86e",
};

export class OverlayRenderer {
  constructor(theme) { this._fixedTheme = theme || null; this.theme = theme || THEMES.broadcast; }

  render(ctx, w, h, payload, state) {
    payload = payload || {};
    if (!this._fixedTheme) this.theme = themeFor(payload.review_type);   // colour identity per review
    const reveals = state.reveals || {};
    const measured = (payload.measured_px || []).map((p) => [p[0], p[1]]);
    const predicted = (payload.predicted_px || []).map((p) => [p[0], p[1]]);
    const shadow = (payload.shadow_px || []).map((p) => [p[0], p[1]]);

    let drew = false;
    if (measured.length || predicted.length) { this._trajectory(ctx, h, payload, measured, predicted, shadow, state); drew = true; }
    // Field elements (Run Out / Stumping / No Ball) — each gated by its own cue.
    if (payload.crease_px) { this._crease(ctx, payload.crease_px, reveals.crease ?? 1); drew = true; }
    if (payload.bat_px) { this._outline(ctx, payload.bat_px, reveals.bat ?? 1, this.theme.transition, "BAT"); drew = true; }
    if (payload.foot_px_outline) { this._outline(ctx, payload.foot_px_outline, reveals.foot ?? 1, "#5ec86e", "FOOT"); drew = true; }
    if (payload.bails_px) { this._bails(ctx, payload.bails_px, payload.bails_status, reveals.bails ?? 1); drew = true; }
    if (payload.stumps_px && !(measured.length || predicted.length)) {
      this._stumps(ctx, payload.stumps_px, { ...state, stumps_reveal: reveals.bails ?? state.stumps_reveal ?? 1 });
    }
    if (payload.frame_number != null && (reveals.framestep || 0) > 0.05) this._framestep(ctx, payload.frame_number);
    if (!drew) this._simpleMarkers(ctx, payload);

    this._banner(ctx, w, payload);
    this._cards(ctx, w, payload.decision_cards || [], state.cards || []);
    this._measurements(ctx, h, payload);
  }

  // ----- field elements (Run Out / Stumping / No Ball) -----
  _crease(ctx, crease, reveal) {
    const pts = crease.map((p) => [p[0], p[1]]);
    if (pts.length < 2) return;
    const cut = pts.length > 2 ? Math.max(2, Math.round(reveal * pts.length)) : 2;
    const shown = pts.slice(0, cut);
    ctx.save();
    ctx.strokeStyle = "rgba(255,220,120,0.5)"; ctx.lineWidth = 12; ctx.lineCap = "round";  // matches Python (BGR 120,220,255)
    ctx.beginPath(); ctx.moveTo(shown[0][0], shown[0][1]); for (let i = 1; i < shown.length; i++) ctx.lineTo(shown[i][0], shown[i][1]); ctx.stroke();
    ctx.strokeStyle = "#ffffff"; ctx.lineWidth = 3; ctx.stroke();
    ctx.fillStyle = "#ffe6c8"; ctx.font = "12px system-ui"; ctx.fillText("CREASE", shown[0][0] + 8, shown[0][1] - 8);
    ctx.restore();
  }

  _outline(ctx, outline, reveal, color, label) {
    const pts = outline.map((p) => [p[0], p[1]]);
    if (pts.length < 2) return;
    ctx.save(); ctx.globalAlpha = clamp(reveal); ctx.strokeStyle = color; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(pts[0][0], pts[0][1]); for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]); ctx.closePath(); ctx.stroke();
    if (reveal > 0.5) {
      const cx = pts.reduce((s, p) => s + p[0], 0) / pts.length;
      const cy = Math.min(...pts.map((p) => p[1])) - 8;
      ctx.fillStyle = color; ctx.font = "12px system-ui"; ctx.fillText(label, cx - 12, cy);
    }
    ctx.restore();
  }

  _bails(ctx, bails, status, reveal) {
    if (reveal <= 0) return;
    const dislodged = String(status || "").toLowerCase() === "dislodged";
    const color = dislodged ? this.theme.impact : "#ffffff";
    for (const b of bails) {
      if (dislodged) { ctx.save(); ctx.globalAlpha = 0.32 * reveal; ctx.fillStyle = color; ctx.beginPath(); ctx.arc(b.x, b.y, 12 * reveal + 3, 0, 7); ctx.fill(); ctx.restore(); }
      ctx.save(); ctx.strokeStyle = color; ctx.lineWidth = Math.max(2, 3 * reveal); ctx.beginPath(); ctx.moveTo(b.x - 7, b.y); ctx.lineTo(b.x + 7, b.y); ctx.stroke(); ctx.restore();
    }
    if (reveal > 0.5 && bails.length) { ctx.save(); ctx.fillStyle = color; ctx.font = "12px system-ui"; ctx.fillText("BAILS " + (dislodged ? "DISLODGED" : "INTACT"), bails[0].x + 14, bails[0].y - 6); ctx.restore(); }
  }

  _framestep(ctx, frameNumber) {
    ctx.save(); ctx.fillStyle = "#c8dcff"; ctx.font = "bold 15px system-ui"; ctx.fillText("FRAME " + frameNumber, 16, 74); ctx.restore();
  }

  _radius(h, y, scale = 1) { return Math.max(2, (4 + 8 * (clamp(y / h))) * scale); }

  _trajectory(ctx, h, payload, measured, predicted, shadow, state) {
    const t = this.theme;
    const conf = payload.confidence == null ? 0.6 : Number(payload.confidence);
    const mShow = Math.round((state.measured_reveal ?? 1) * measured.length);
    const pShow = Math.round((state.predicted_reveal ?? 1) * predicted.length);
    const total = measured.length + predicted.length;

    // ground ribbon grows to the ball
    const ribbonN = Math.max(2, Math.round((total ? (mShow + pShow) / total : 0) * shadow.length));
    this._ribbon(ctx, shadow.slice(0, ribbonN));

    // PHASE 1 — measured (white), orange transition glow near impact
    for (let i = 0; i < mShow; i++) {
      const [x, y] = measured[i];
      const glow = i >= measured.length - 2 ? t.transition : null;
      this._sphere(ctx, x, y, this._radius(h, y), t.measured, 1, glow);
    }

    // bounce
    const b = payload.bounce_px;
    if (b && state.bounce_visible) {
      this._sphere(ctx, b.x, b.y, this._radius(h, b.y, 1.25), t.bounce, 1, t.transition);
      this._label(ctx, "BOUNCE", b.x + 14, b.y + 4, t.bounce);
    }

    // PHASE 3 — predicted (grey-purple), translucent by confidence
    const alpha = 0.5 * (0.6 + 0.4 * clamp(conf));
    for (let i = 0; i < pShow; i++) {
      const [x, y] = predicted[i];
      this._sphere(ctx, x, y, this._radius(h, y, 0.85), t.predicted, alpha);
    }

    // impact pulse
    const im = payload.impact_px;
    if (im && state.impact_visible) this._impact(ctx, im.x, im.y, state.impact_pulse || 0);

    // stumps glow + vibration + bails
    this._stumps(ctx, payload.stumps_px || [], state);

    // ball head
    const head = pShow ? predicted[pShow - 1] : (mShow ? measured[mShow - 1] : null);
    if (head) this._sphere(ctx, head[0], head[1], this._radius(h, head[1], 1.1) + 1, "#ffffff", 1);
  }

  _sphere(ctx, x, y, r, color, alpha = 1, glow = null) {
    if (glow) {
      ctx.save(); ctx.globalAlpha = this.theme.glow * alpha; ctx.fillStyle = glow;
      ctx.beginPath(); ctx.arc(x, y, r * 2.1, 0, 7); ctx.fill(); ctx.restore();
    }
    ctx.save(); ctx.globalAlpha = 0.25 * alpha; ctx.fillStyle = "#000";
    ctx.beginPath(); ctx.ellipse(x, y + r * 0.9, r * 1.15, r * 0.4, 0, 0, 7); ctx.fill(); ctx.restore();

    const g = ctx.createRadialGradient(x - r * 0.35, y - r * 0.35, r * 0.1, x, y, r);
    g.addColorStop(0, "#ffffff"); g.addColorStop(0.4, color); g.addColorStop(1, shade(colorHex(color), 0.5));
    ctx.save(); ctx.globalAlpha = alpha; ctx.fillStyle = g;
    ctx.beginPath(); ctx.arc(x, y, r, 0, 7); ctx.fill(); ctx.restore();
  }

  _ribbon(ctx, pts) {
    if (pts.length < 2) return;
    ctx.save();
    ctx.strokeStyle = `rgba(${this.theme.ribbon},0.22)`; ctx.lineWidth = 16; ctx.lineCap = "round"; ctx.lineJoin = "round";
    ctx.beginPath(); ctx.moveTo(pts[0][0], pts[0][1]);
    for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
    ctx.stroke(); ctx.restore();
  }

  _impact(ctx, x, y, pulse) {
    const c = this.theme.impact;
    ctx.save();
    ctx.strokeStyle = c; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(x, y, 11 + 13 * pulse, 0, 7); ctx.stroke();
    ctx.fillStyle = c; ctx.beginPath(); ctx.arc(x, y, 5, 0, 7); ctx.fill();
    ctx.restore();
    this._label(ctx, "IMPACT", x + 14, y - 8, c, "bold 13px system-ui");
  }

  _stumps(ctx, stumps, state) {
    const reveal = state.stumps_reveal ?? 1;
    if (reveal <= 0 || !stumps.length) return;
    const hitting = state.hitting;
    const color = hitting ? this.theme.stumpHit : this.theme.stumpIdle;
    const shift = hitting ? (state.stump_vibration || 0) * 3 : 0;
    for (const s of stumps) {
      const x = s.x + shift, y = s.y;
      if (hitting) {
        ctx.save(); ctx.globalAlpha = 0.28 * reveal; ctx.fillStyle = color;
        ctx.beginPath(); ctx.arc(x, y - 18, 18 * reveal + 4, 0, 7); ctx.fill(); ctx.restore();
      }
      ctx.save(); ctx.strokeStyle = color; ctx.lineWidth = 3;
      ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x, y - 42 * reveal); ctx.stroke();
      if (hitting && reveal > 0.8) { ctx.strokeStyle = "#fff"; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(x - 5, y - 42); ctx.lineTo(x + 5, y - 42); ctx.stroke(); }
      ctx.restore();
    }
  }

  _banner(ctx, w, payload) {
    const verdict = String(payload.verdict || "").toUpperCase() || "—";
    ctx.save();
    ctx.fillStyle = "rgba(18,18,20,0.9)"; ctx.fillRect(0, 0, w, 46);
    ctx.fillStyle = "#ececec"; ctx.font = "bold 22px system-ui"; ctx.textBaseline = "middle";
    ctx.fillText(String(payload.review_type || "review").toUpperCase(), 16, 24);
    ctx.fillStyle = VERDICT_COLORS[(payload.verdict || "").toLowerCase()] || "#a8a8a8";
    ctx.font = "bold 24px system-ui"; ctx.textAlign = "right";
    ctx.fillText(verdict, w - 16, 24); ctx.restore();
  }

  _cards(ctx, w, cards, cardStates) {
    if (!cards.length) return;
    const cw = 210, ch = 50, gap = 10, x0 = w - cw - 18, y0 = 62;
    const status = { out: this.theme.statusOut, "not-out": this.theme.statusNotOut, info: this.theme.statusInfo };
    cards.forEach((card, i) => {
      const reveal = cardStates[i] ?? 1;
      if (reveal <= 0) return;
      const x = x0 + (1 - reveal) * 44, y = y0 + i * (ch + gap);
      const color = status[card.status] || this.theme.statusInfo;
      ctx.save(); ctx.globalAlpha = reveal;
      ctx.fillStyle = "rgba(14,14,16,0.78)"; roundRect(ctx, x, y, cw, ch, 8); ctx.fill();
      ctx.strokeStyle = color; ctx.lineWidth = 1; roundRect(ctx, x, y, cw, ch, 8); ctx.stroke();
      ctx.fillStyle = color; ctx.fillRect(x, y, 5, ch);
      ctx.textAlign = "left"; ctx.textBaseline = "alphabetic";
      ctx.fillStyle = "#c8c8c8"; ctx.font = "11px system-ui"; ctx.fillText(String(card.label || "").slice(0, 22), x + 14, y + 19);
      ctx.fillStyle = color; ctx.font = "bold 17px system-ui"; ctx.fillText(String(card.value || "").slice(0, 18), x + 14, y + 40);
      ctx.restore();
    });
  }

  _measurements(ctx, h, payload) {
    ctx.save(); ctx.textAlign = "left"; ctx.font = "14px system-ui";
    let y = h - 16;
    (payload.measurements || []).slice(0, 3).reverse().forEach((m) => {
      ctx.fillStyle = "#dcdcdc"; ctx.fillText(`${m.label || ""}: ${m.value || ""}`, 16, y); y -= 22;
    });
    if (payload.confidence != null) { ctx.fillStyle = "#b4dcff"; ctx.fillText(`Confidence ${Math.round(payload.confidence * 100)}%`, 16, y); }
    ctx.restore();
  }

  _simpleMarkers(ctx, payload) {
    const c = payload.ball_centre;
    if (c && c.x != null) { ctx.save(); ctx.strokeStyle = "#ffd23c"; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(c.x, c.y, 9, 0, 7); ctx.stroke(); ctx.restore(); }
    for (const [k, col] of [["toe_px", "#5ec86e"], ["heel_px", "#eb3c3c"]]) {
      const p = payload[k];
      if (p && p.x != null) { ctx.save(); ctx.strokeStyle = col; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(p.x - 9, p.y); ctx.lineTo(p.x + 9, p.y); ctx.moveTo(p.x, p.y - 9); ctx.lineTo(p.x, p.y + 9); ctx.stroke(); ctx.restore(); }
    }
  }

  _label(ctx, text, x, y, color, font = "12px system-ui") {
    ctx.save(); ctx.fillStyle = color; ctx.font = font; ctx.textAlign = "left"; ctx.textBaseline = "alphabetic";
    ctx.fillText(text, x, y); ctx.restore();
  }
}

function colorHex(color) { return color.startsWith("#") ? color : "#cccccc"; }
function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
}

// Ties payload + director + renderer + a feed image into a played review on a canvas.
export class ReviewPlayer {
  constructor(canvas, { theme } = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.director = new AnimationDirector();
    this.camera = new CameraDirector();
    this.renderer = new OverlayRenderer(theme ? THEMES[theme] : null);  // null → per-review theme
    this.payload = null;
    this.timeline = timelineFor("lbw");   // TimelineFactory → Timeline; directors consume it
    this.feedImage = null;
    this.t = 0;
    this.playing = false;
    this.speed = 1;          // playback rate: <1 = slow-motion, >1 = faster
    this._raf = null;
    this.onProgress = null;
  }
  setTheme(name) { this.renderer._fixedTheme = name ? (THEMES[name] || null) : null; }
  setPayload(p) { this.payload = p || {}; this.timeline = timelineFor(this.payload.review_type); }
  setFeedImage(img) { this.feedImage = img; }
  // Playback speed (0.1 = 10x slow-mo … 2 = 2x). Clamped to the engine's range.
  setSpeed(s) { const v = Number(s); this.speed = Number.isFinite(v) && v > 0 ? Math.max(0.05, Math.min(4, v)) : 1; }
  // True when there is a ball trajectory to animate. Used to show "No replay data".
  hasReplayData() {
    const p = this.payload || {};
    return Boolean((p.measured_px && p.measured_px.length) || (p.predicted_px && p.predicted_px.length));
  }

  play() {
    this.playing = true; this._start = null;
    const dur = this.timeline.duration;
    const step = (ts) => {
      if (!this.playing) return;
      if (this._start == null) this._start = ts;
      this.t = Math.min(dur, ((ts - this._start) / 1000) * this.speed);
      this.draw(this.t);
      if (this.onProgress) this.onProgress(this.t / dur);
      if (this.t < dur) this._raf = requestAnimationFrame(step);
      else this.playing = false;
    };
    this._raf = requestAnimationFrame(step);
  }
  pause() { this.playing = false; if (this._raf) cancelAnimationFrame(this._raf); }
  seek(progress) { this.t = clamp(progress) * this.timeline.duration; this.draw(this.t); }
  restart() { this.pause(); this.t = 0; this.play(); }

  draw(t) {
    const { ctx, canvas } = this;
    const w = canvas.width, h = canvas.height;
    const time = t == null ? this.timeline.duration : t;
    const state = this.director.stateAt(this.timeline, this.payload, time);
    const camera = this.camera.stateAt(this.timeline, time);   // CameraDirector owns zoom now
    ctx.clearRect(0, 0, w, h);
    ctx.save();
    // cinematic zoom (scale about centre) — applied to feed + overlay together
    const z = 1 + (camera.zoom || 0);
    ctx.translate(w / 2, h / 2); ctx.scale(z, z); ctx.translate(-w / 2, -h / 2);
    if (this.feedImage && this.feedImage.complete && this.feedImage.naturalWidth) {
      ctx.drawImage(this.feedImage, 0, 0, w, h);
    } else {
      ctx.fillStyle = "#12241a"; ctx.fillRect(0, 0, w, h);
      ctx.fillStyle = "#1c3a29"; ctx.fillRect(w * 0.34, h * 0.14, w * 0.32, h * 0.74);
    }
    this.renderer.render(ctx, w, h, this.payload, state);
    ctx.restore();
  }
}
