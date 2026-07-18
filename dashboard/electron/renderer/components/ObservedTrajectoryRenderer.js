// Reusable, near-stupid observed-trajectory overlay.
//
// Given a canvas, an observation (points + display cutoff already computed upstream) and
// the current video frame, it draws the enabled layers in IMAGE space. It knows nothing
// about pad regions, impact heuristics or confidence thresholds — the pipeline decided all
// of that. It only knows "draw these points, up to this frame". One renderer, two
// consumers: the Testing UI and the standalone verifier.
//
// The canvas is passive: it never owns timing. The video is the master — a consumer feeds
// it currentFrame from `video.currentTime`.

const ACCENT = "#5ce65a";   // real detection
const PREDICT = "#ff9d3c";  // Kalman gap-fill
const IMPACT = "#ff5a3c";
const BALL = "#ffffff";

export class ObservedTrajectoryRenderer {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.points = [];
    this.displayEnd = null;   // don't draw past this frame (display policy)
    this.impactFrame = null;
    this.videoW = 1;
    this.videoH = 1;
    this.frame = 0;
    this.cssW = 0;
    this.cssH = 0;
    this.dpr = 1;
    this.layers = { centres: true, trajectory: true, confidence: false, impact: false };
    this.onPick = null;       // consumer callback: (point) => seek video + show info
    this._onClick = (e) => this._handleClick(e);
    canvas.addEventListener("click", this._onClick);
  }

  // obs: { points, display_end_frame, videoW, videoH, impactFrame }
  setObservation(obs) {
    this.points = (obs.points || []).slice().sort((a, b) => a.frame_id - b.frame_id);
    this.displayEnd = obs.display_end_frame ?? null;
    this.impactFrame = obs.impactFrame ?? null;
    this.videoW = obs.videoW || 1;
    this.videoH = obs.videoH || 1;
    this.draw(this.frame);
  }

  setLayers(partial) {
    Object.assign(this.layers, partial);
    this.draw(this.frame);
  }

  // Match the canvas to the video's displayed CSS size (kept crisp with devicePixelRatio).
  resize(cssW, cssH) {
    this.dpr = window.devicePixelRatio || 1;
    this.cssW = cssW;
    this.cssH = cssH;
    this.canvas.width = Math.round(cssW * this.dpr);
    this.canvas.height = Math.round(cssH * this.dpr);
    this.canvas.style.width = cssW + "px";
    this.canvas.style.height = cssH + "px";
    this.draw(this.frame);
  }

  _sx() { return this.cssW / this.videoW; }
  _sy() { return this.cssH / this.videoH; }

  _visible() {
    const cut = this.displayEnd == null ? Infinity : this.displayEnd;
    const upto = Math.min(this.frame, cut);
    return this.points.filter((p) => p.frame_id <= upto);
  }

  _colour(p) {
    if (this.layers.confidence) {
      const c = Math.max(0, Math.min(1, p.confidence));
      return `rgb(${Math.round(255 * (1 - c))},${Math.round(200 * c + 40)},60)`;
    }
    return p.real ? ACCENT : PREDICT;
  }

  draw(frame) {
    if (frame != null) this.frame = frame;
    const ctx = this.ctx;
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.clearRect(0, 0, this.cssW, this.cssH);
    const pts = this._visible();
    if (!pts.length || !this.cssW) return;
    const sx = this._sx(), sy = this._sy();

    if (this.layers.trajectory && pts.length > 1) {
      ctx.lineJoin = "round";
      ctx.lineCap = "round";
      // dark casing first so the line reads against bright grass...
      ctx.strokeStyle = "rgba(0,0,0,0.55)";
      ctx.lineWidth = 5.5;
      ctx.beginPath();
      ctx.moveTo(pts[0].x_px * sx, pts[0].y_px * sy);
      for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].x_px * sx, pts[i].y_px * sy);
      ctx.stroke();
      // ...then the coloured line on top (per-segment, so the confidence layer works), with a glow.
      ctx.lineWidth = 2.5;
      ctx.shadowColor = "rgba(92,230,90,0.55)";
      ctx.shadowBlur = 6;
      for (let i = 1; i < pts.length; i++) {
        ctx.strokeStyle = this._colour(pts[i]);
        ctx.beginPath();
        ctx.moveTo(pts[i - 1].x_px * sx, pts[i - 1].y_px * sy);
        ctx.lineTo(pts[i].x_px * sx, pts[i].y_px * sy);
        ctx.stroke();
      }
      ctx.shadowBlur = 0;
    }

    if (this.layers.centres) {
      for (const p of pts) {
        const x = p.x_px * sx, y = p.y_px * sy;
        ctx.beginPath();
        ctx.arc(x, y, p.real ? 4 : 3.5, 0, Math.PI * 2);
        if (p.real) {
          ctx.fillStyle = this._colour(p);
          ctx.fill();
          ctx.lineWidth = 1;
          ctx.strokeStyle = "rgba(0,0,0,0.7)";   // dark outline for contrast
          ctx.stroke();
        } else {
          ctx.lineWidth = 1.5;
          ctx.strokeStyle = this._colour(p);
          ctx.stroke();
        }
      }
    }

    // current ball position = last visible point — dark ring + white core + coloured glow
    const b = pts[pts.length - 1];
    const bx = b.x_px * sx, by = b.y_px * sy;
    ctx.beginPath();
    ctx.arc(bx, by, 7, 0, Math.PI * 2);
    ctx.fillStyle = BALL;
    ctx.shadowColor = "rgba(92,230,90,0.9)";
    ctx.shadowBlur = 12;
    ctx.fill();
    ctx.shadowBlur = 0;
    ctx.lineWidth = 2.5;
    ctx.strokeStyle = "rgba(0,0,0,0.8)";
    ctx.stroke();

    if (this.layers.impact && this.impactFrame != null) {
      const m = this.points.find((p) => p.frame_id >= this.impactFrame);
      if (m && m.frame_id <= this.frame) {
        ctx.beginPath();
        ctx.arc(m.x_px * sx, m.y_px * sy, 12, 0, Math.PI * 2);
        ctx.strokeStyle = IMPACT;
        ctx.lineWidth = 2.5;
        ctx.stroke();
      }
    }
  }

  _handleClick(e) {
    const rect = this.canvas.getBoundingClientRect();
    const p = this.hitTest(e.clientX - rect.left, e.clientY - rect.top);
    if (p && this.onPick) this.onPick(p);
  }

  // Nearest observed point to a canvas coordinate, within a small radius (for click-seek).
  hitTest(cx, cy) {
    const sx = this._sx(), sy = this._sy();
    let best = null;
    let bd = 18 * 18;
    for (const p of this.points) {
      const dx = p.x_px * sx - cx;
      const dy = p.y_px * sy - cy;
      const d = dx * dx + dy * dy;
      if (d < bd) { bd = d; best = p; }
    }
    return best;
  }

  destroy() {
    this.canvas.removeEventListener("click", this._onClick);
  }
}
