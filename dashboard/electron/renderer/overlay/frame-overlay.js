// Frame overlay — draws evidence ON the stepped replay frame.
//
// This is the surface an umpire actually judges from, so every mark here obeys
// the Evidence Contract: colours come from token ROLES, never literals; a dark
// casing goes under every stroke (measured on real footage, no colour reaches
// 3:1 luminance contrast against sunlit grass on its own); and nothing is drawn
// that was not measured.
//
// Tiers, decided by the data rather than by the caller:
//   measured   real geometry from a calibrated camera  → solid, carries a number
//   detected   real pixels, no calibration             → solid, no number
//   schematic  assumed geometry                        → dashed, labelled a guess
//
// The payload is in FRAME pixels (the camera's natural resolution), so everything
// is scaled by the displayed size. Drawing in display space would silently
// misplace evidence the moment the panel resized.

import { token } from "./tokens.js";

const LABEL = token("label");
const QUALIFIER = token("qualifier");
const MEASUREMENT = token("measurement");
const COUNTER = token("frame.counter");
const CASING = token("casing");

function casedStroke(ctx, points, role, { dash = null, alpha = 1 } = {}) {
  if (points.length < 2) return;
  const spec = token(role);
  const trace = () => {
    ctx.beginPath();
    ctx.moveTo(points[0][0], points[0][1]);
    for (const [x, y] of points.slice(1)) ctx.lineTo(x, y);
  };
  ctx.save();
  ctx.lineCap = spec.cap || "round";
  ctx.lineJoin = "round";
  if (dash) ctx.setLineDash(dash);
  // casing first: it carries the luminance separation the hue cannot
  ctx.strokeStyle = CASING.color;
  ctx.lineWidth = spec.width + CASING.width_delta;
  trace(); ctx.stroke();
  ctx.globalAlpha = alpha;
  ctx.strokeStyle = spec.color;
  ctx.lineWidth = spec.width;
  trace(); ctx.stroke();
  ctx.restore();
}

function knockoutText(ctx, text, x, y, spec, { align = "left" } = {}) {
  ctx.save();
  ctx.textAlign = align;
  ctx.font = `${spec.weight} ${spec.size}px ${spec.family === "mono"
    ? "ui-monospace, Consolas, monospace" : "Inter, system-ui, sans-serif"}`;
  const value = spec.transform === "uppercase" ? String(text).toUpperCase() : String(text);
  ctx.lineWidth = spec.knockout_width || 4;
  ctx.strokeStyle = spec.knockout_color || "rgba(0,0,0,0.7)";
  ctx.lineJoin = "round";
  ctx.strokeText(value, x, y);
  ctx.fillStyle = spec.color;
  ctx.fillText(value, x, y);
  ctx.restore();
}

function detectedRing(ctx, x, y, radius) {
  const spec = token("mark.detected");
  ctx.save();
  ctx.strokeStyle = CASING.color;
  ctx.lineWidth = spec.width + CASING.width_delta;
  ctx.beginPath(); ctx.arc(x, y, radius, 0, Math.PI * 2); ctx.stroke();
  ctx.strokeStyle = spec.color;
  ctx.lineWidth = spec.width;
  ctx.beginPath(); ctx.arc(x, y, radius, 0, Math.PI * 2); ctx.stroke();
  // the centre IS the measurement's anchor point, so it alone may be filled
  ctx.fillStyle = spec.color;
  ctx.beginPath(); ctx.arc(x, y, 3, 0, Math.PI * 2); ctx.fill();
  ctx.restore();
}

function measurementPlate(ctx, text, x, y, outcome) {
  ctx.save();
  ctx.font = `${MEASUREMENT.weight} ${MEASUREMENT.size}px Inter, system-ui, sans-serif`;
  const width = ctx.measureText(text).width + 26;
  const tint = outcome === true ? token("status.adverse").color
    : outcome === false ? token("status.safe").color : null;
  ctx.fillStyle = "rgba(10,12,16,0.82)";
  ctx.strokeStyle = tint || "rgba(255,255,255,0.22)";
  ctx.lineWidth = 1.5;
  const h = MEASUREMENT.size + 16;
  ctx.beginPath();
  if (ctx.roundRect) ctx.roundRect(x, y - h / 2, width, h, 7);
  else ctx.rect(x, y - h / 2, width, h);
  ctx.fill(); ctx.stroke();
  ctx.fillStyle = tint || MEASUREMENT.color;
  ctx.textBaseline = "middle";
  ctx.fillText(text, x + 13, y + 1);
  ctx.restore();
}

// The standing legend: same three symbols, same corner, on every review type, so
// the vocabulary teaches itself after a couple of reviews.
function legend(ctx, height, entries) {
  const rows = entries.filter(Boolean);
  if (!rows.length) return;
  const pad = 12, rowH = 22, w = 168;
  const top = height - pad - rows.length * rowH - 10;
  ctx.save();
  ctx.fillStyle = "rgba(10,12,16,0.78)";
  ctx.strokeStyle = "rgba(255,255,255,0.14)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  if (ctx.roundRect) ctx.roundRect(pad, top, w, rows.length * rowH + 10, 7);
  else ctx.rect(pad, top, w, rows.length * rowH + 10);
  ctx.fill(); ctx.stroke();
  rows.forEach((row, i) => {
    const y = top + 15 + i * rowH;
    const spec = token(row.role);
    ctx.save();
    ctx.strokeStyle = spec.color;
    ctx.lineWidth = Math.min(4, spec.width || 3);
    if (row.dash) ctx.setLineDash(row.dash);
    if (row.ring) {
      ctx.beginPath(); ctx.arc(pad + 24, y, 7, 0, Math.PI * 2); ctx.stroke();
    } else {
      ctx.beginPath(); ctx.moveTo(pad + 12, y); ctx.lineTo(pad + 40, y); ctx.stroke();
    }
    ctx.restore();
    ctx.save();
    ctx.font = "600 11px Inter, system-ui, sans-serif";
    ctx.fillStyle = "#e8e8e8";
    ctx.textBaseline = "middle";
    ctx.fillText(row.label, pad + 50, y + 1);
    ctx.restore();
  });
  ctx.restore();
}

/**
 * Draw the overlay for one review type onto a canvas sized to the displayed frame.
 * `frameSize` is the frame's NATURAL pixel size — the payload's coordinate space.
 */
export function drawFrameOverlay(canvas, payload, { frameSize, frameIndex } = {}) {
  if (!canvas || !payload) return;
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  if (!frameSize || !frameSize.width || !frameSize.height) return;

  const sx = canvas.width / frameSize.width;
  const sy = canvas.height / frameSize.height;
  const P = ([x, y]) => [x * sx, y * sy];

  if (String(payload.review_type || "").toLowerCase() === "wide") {
    drawWide(ctx, payload, P, canvas);
  }

  if (frameIndex != null) {
    knockoutText(ctx, COUNTER.format.replace("{index}", frameIndex), 16, 28, COUNTER);
  }
}

function drawWide(ctx, payload, P, canvas) {
  const line = (payload.wide_line_px || []).map(P);
  const measured = line.length >= 2;

  if (measured) {
    casedStroke(ctx, line, "geometry.reference");
    knockoutText(ctx, "Wide line", line[0][0] + 10, line[0][1] + 20, LABEL);
  } else {
    // No projection → the line is an assumption. Draw it as one, and say so.
    const x = canvas.width * 0.28;
    casedStroke(ctx, [[x, 0], [x, canvas.height]], "guide.schematic",
      { dash: token("guide.schematic").dash });
    knockoutText(ctx, "Wide line", x + 10, 26, LABEL);
    knockoutText(ctx, "schematic — calibrate to measure", x + 10, 44, QUALIFIER);
  }

  const ball = payload.ball_centre;
  if (ball) {
    const [bx, by] = P([ball.x, ball.y]);
    const r = Math.max(11, (payload.ball_radius_px || 8) * 1.7 * (canvas.width / 1280));
    detectedRing(ctx, bx, by, r);
    knockoutText(ctx, "Ball", bx + r + 8, by - r - 4, LABEL);

    // A measurement only exists in the measured tier: a number against a
    // schematic line would be a real figure quoted from an imagined reference.
    if (measured && payload.distance_cm != null) {
      const cm = Math.abs(Number(payload.distance_cm)).toFixed(1);
      const word = payload.is_wide ? "outside" : "inside";
      measurementPlate(ctx, `${cm} cm ${word}`, bx + r + 8, by + 22, payload.is_wide);
    }
  }

  legend(ctx, canvas.height, [
    measured ? { role: "geometry.reference", label: "Measured" }
             : { role: "guide.schematic", label: "Guide", dash: token("guide.schematic").dash },
    { role: "mark.detected", label: "Detected", ring: true },
  ]);
}
