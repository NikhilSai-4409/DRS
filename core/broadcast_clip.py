"""Broadcast-ready UltraEdge review clip renderer.

The operator's existing streaming rig (OBS/vMix/similar feeding YouTube) plays
finished video files — it cannot capture this app's windows on another machine.
So the review evidence is EXPORTED: this module composites the broadcast
UltraEdge scene (slow-motion replay frames, the waveform panel drawn in-frame,
the spike choreography, an optional verdict tail) into one H.264 MP4 written
with the pipeline's Chromium-safe writer. Pure numpy/PIL/cv2 — no Chrome.

Frame sync is inherent: waveform buckets are BUCKETS_PER_FRAME per replay frame
over exactly the clip's capture window, and the cursor column is derived from
the replay frame index being shown.
"""

from __future__ import annotations

import os
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

OUT_W, OUT_H = 1280, 720
FPS = 25
BUCKETS_PER_FRAME = 10

PANEL_W, PANEL_H = 790, 132
PANEL_BOTTOM = 38
PAD_X = 18

TEAL_TOP = (70, 206, 222)
TEAL_BOTTOM = (5, 62, 84)
GREEN_TOP, GREEN_BOTTOM = (70, 211, 87), (21, 112, 33)
RED_TOP, RED_BOTTOM = (240, 75, 70), (156, 10, 13)
NEUTRAL_TOP, NEUTRAL_BOTTOM = (43, 95, 125), (12, 44, 64)

GREEN_WORDS = ("NOT OUT", "NOT_OUT", "MISSING", "NO BAT", "LEGAL", "NONE", "CLEAR")
RED_WORDS = ("OUT", "IN LINE", "IN-LINE", "HITTING", "NO BALL", "BAT", "WIDE")


def resolve_export_dir() -> Path:
    env = os.environ.get("DRS_BROADCAST_EXPORT_DIR", "").strip()
    if env:
        return Path(env)
    from config.settings import RECORDINGS_DIR

    return Path(RECORDINGS_DIR) / "broadcast"


def _font(size: int, bold: bool = True):
    name = "arialbd.ttf" if bold else "arial.ttf"
    for candidate in (name, f"C:/Windows/Fonts/{name}"):
        try:
            return ImageFont.truetype(candidate, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _vgradient(w: int, h: int, top: tuple, bottom: tuple, alpha: int = 255) -> Image.Image:
    rows = np.linspace(top, bottom, h).astype(np.uint8)
    rgb = np.repeat(rows[:, None, :], w, axis=1)
    img = np.dstack([rgb, np.full((h, w, 1), alpha, dtype=np.uint8)])
    return Image.fromarray(img, "RGBA")


def _verdict_colors(text: str) -> tuple:
    t = str(text or "").upper()
    if any(w in t for w in GREEN_WORDS):
        return GREEN_TOP, GREEN_BOTTOM
    if any(w in t for w in RED_WORDS):
        return RED_TOP, RED_BOTTOM
    return NEUTRAL_TOP, NEUTRAL_BOTTOM


def _panel_base() -> Image.Image:
    """Static panel chrome: ULTRA EDGE tab + dark waveform box (RGBA)."""
    tab_h = 36
    img = Image.new("RGBA", (PANEL_W, PANEL_H + tab_h), (0, 0, 0, 0))
    tab_w = 180
    img.paste(_vgradient(tab_w, tab_h, TEAL_TOP, TEAL_BOTTOM), (0, 0))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, tab_w - 1, tab_h - 1], outline=(195, 232, 246, 170), width=1)
    draw.text((16, 6), "ULTRA EDGE", font=_font(20), fill=(255, 255, 255, 255))
    box = _vgradient(PANEL_W, PANEL_H, (5, 14, 24), (2, 8, 16), alpha=238)
    img.paste(box, (0, tab_h))
    draw.rectangle([0, tab_h, PANEL_W - 1, tab_h + PANEL_H - 1],
                   outline=(200, 228, 240, 190), width=1)
    return img


def _draw_wave(draw: ImageDraw.ImageDraw, y0: int, n: int, buckets: list,
               reveal: int, cursor: int, spikes: set, glow: float, marker: bool,
               impact: int | None, note: str | None) -> None:
    mid = y0 + PANEL_H // 2
    amp = int(PANEL_H * 0.40)
    plot_w = PANEL_W - PAD_X * 2
    draw.line([PAD_X, mid, PANEL_W - PAD_X, mid], fill=(140, 175, 195, 70), width=1)
    for i in range(n + 1):
        x = PAD_X + plot_w * i / n
        draw.line([x, y0 + PANEL_H - 8, x, y0 + PANEL_H - 4], fill=(140, 175, 195, 40), width=1)
    if not buckets and note:
        draw.text((PAD_X + 10, mid - 10), note, font=_font(15, bold=False),
                  fill=(159, 192, 213, 220))
        return
    fw = plot_w / n
    for f in range(min(reveal + 1, n)):
        spike = f in spikes
        color = (125, 240, 255, 255) if spike else (238, 248, 252, 235)
        for b in range(BUCKETS_PER_FRAME):
            pair = buckets[f * BUCKETS_PER_FRAME + b] if f * BUCKETS_PER_FRAME + b < len(buckets) else None
            if pair is None:
                continue
            x = PAD_X + fw * f + fw * (b + 0.5) / BUCKETS_PER_FRAME
            top = mid - pair[1] * amp - 1
            bottom = mid - pair[0] * amp + 1
            if spike and glow > 0:
                draw.line([x, top - 2, x, bottom + 2],
                          fill=(90, 220, 250, int(120 * glow)), width=4)
            draw.line([x, top, x, bottom], fill=color, width=2)
    if marker and impact is not None:
        sx = PAD_X + fw * impact + fw * 0.5
        draw.polygon([(sx, y0 + PANEL_H - 24), (sx - 7, y0 + PANEL_H - 10), (sx + 7, y0 + PANEL_H - 10)],
                     fill=(255, 82, 82, 255))
    cx = PAD_X + fw * (cursor + 1)
    draw.line([cx, y0 + 6, cx, y0 + PANEL_H - 6], fill=(255, 255, 255, 255), width=2)
    draw.polygon([(cx - 6, y0 + 6), (cx + 6, y0 + 6), (cx, y0 + 15)], fill=(255, 255, 255, 255))


def _chip(draw: ImageDraw.ImageDraw, label: str) -> None:
    text = f"DRS REVIEW   {label}"
    font = _font(19)
    w = draw.textlength(text, font=font) + 54
    draw.rectangle([32, 28, 32 + w, 66], fill=(6, 15, 26, 235), outline=(160, 200, 225, 90))
    draw.ellipse([46, 42, 56, 52], fill=(255, 59, 59, 255))
    draw.text((66, 37), text, font=font, fill=(234, 244, 251, 255))


def _timeline(n: int, impact: int | None) -> list[dict]:
    """Simulate the broadcast choreography into per-output-frame instructions:
    entrance -> first pass (hold+flash on the spike) -> quick rock back ->
    slow re-run -> play out -> end hold. Matches the approved on-screen anim."""
    if impact is None:
        seq = [{"to": n - 1, "fps": 8.0, "hold": 0.0}]
    else:
        seq = [
            {"to": min(n - 1, impact + 4), "fps": 8.0, "hold": 0.8},
            {"to": max(0, impact - 5), "fps": 14.0, "hold": 0.0},
            {"to": min(n - 1, impact + 4), "fps": 5.0, "hold": 0.65},
            {"to": n - 1, "fps": 8.0, "hold": 0.0},
        ]
    out: list[dict] = []
    dt = 1.0 / FPS
    for k in range(18):  # 0.72 s entrance: panel rises + fades in
        out.append({"ri": 0, "reveal": 0, "cursor": 0, "alpha": (k + 1) / 18,
                    "rise": int((1 - (k + 1) / 18) * 24), "flash": 0.0, "glow": 0.0, "marker": False})
    cur = maxi = 0
    seg, hold, next_step, spike_t, t = 0, 0.0, 0.0, -9.0, 0.0
    flash_until = -9.0
    while seg < len(seq) and len(out) < 1500:
        t += dt
        glow = max(0.0, 1.0 - (t - spike_t) / 0.9) if spike_t > 0 else 0.0
        flash = max(0.0, (flash_until - t) / 0.12) if flash_until > t else 0.0
        frame = {"ri": cur, "reveal": maxi, "cursor": cur, "alpha": 1.0, "rise": 0,
                 "flash": flash, "glow": glow, "marker": impact is not None and maxi >= impact}
        if hold > 0:
            hold -= dt
            out.append(frame)
            continue
        if t < next_step:
            out.append(frame)
            continue
        s = seq[seg]
        cur += 1 if s["to"] >= cur else -1
        maxi = max(maxi, cur)
        next_step = t + 1.0 / s["fps"]
        if impact is not None and cur == impact and s["hold"] > 0:
            hold = s["hold"]
            spike_t = t
            flash_until = t + 0.12
        if cur == s["to"]:
            seg += 1
        frame.update({"ri": cur, "reveal": maxi, "cursor": cur,
                      "glow": max(glow, 1.0 if hold > 0 and cur == impact else glow),
                      "marker": impact is not None and maxi >= impact})
        out.append(frame)
    for _ in range(int(1.1 * FPS)):  # end hold
        out.append({"ri": n - 1, "reveal": n - 1, "cursor": n - 1, "alpha": 1.0,
                    "rise": 0, "flash": 0.0, "glow": 0.0, "marker": impact is not None})
    return out


def _verdict_tail(base_bgr: np.ndarray, verdict: str, cards: list, label: str) -> list[np.ndarray]:
    """2.4 s decision scene over the final frame: dim + cards + verdict banner."""
    frames = []
    total = int(2.4 * FPS)
    banner_font = _font(64)
    for k in range(total):
        p = (k + 1) / total
        img = Image.fromarray(cv2.cvtColor(base_bgr, cv2.COLOR_BGR2RGB)).convert("RGBA")
        overlay = Image.new("RGBA", (OUT_W, OUT_H), (2, 8, 16, int(140 * min(1.0, p * 4))))
        img = Image.alpha_composite(img, overlay)
        draw = ImageDraw.Draw(img)
        _chip(draw, f"{label} · DECISION")
        shown = [c for i, c in enumerate(cards) if p * 4 >= (i + 1) * 0.6]
        if shown:
            card_w, card_h, gap = 220, 84, 18
            x0 = (OUT_W - (card_w * len(cards) + gap * (len(cards) - 1))) // 2
            for i, (head, value) in enumerate(cards):
                if p * 4 < (i + 1) * 0.6:
                    continue
                x = x0 + i * (card_w + gap)
                y = 240
                img.paste(_vgradient(card_w, 36, TEAL_TOP, TEAL_BOTTOM), (x, y))
                top, bottom = _verdict_colors(value)
                img.paste(_vgradient(card_w, card_h - 36, top, bottom), (x, y + 36))
                d2 = ImageDraw.Draw(img)
                d2.rectangle([x, y, x + card_w - 1, y + card_h - 1], outline=(190, 230, 245, 140))
                d2.text((x + card_w / 2, y + 8), str(head).upper()[:16], font=_font(17),
                        fill=(255, 255, 255, 255), anchor="ma")
                d2.text((x + card_w / 2, y + 44), str(value).upper()[:16], font=_font(19),
                        fill=(255, 255, 255, 255), anchor="ma")
        if p >= 0.55:
            text = str(verdict).replace("_", " ").upper()
            top, bottom = _verdict_colors(text)
            tw = draw.textlength(text, font=banner_font) + 160
            bx = int((OUT_W - tw) / 2)
            by = 400
            img.paste(_vgradient(int(tw), 108, top, bottom), (bx, by))
            d3 = ImageDraw.Draw(img)
            d3.rectangle([bx, by, bx + int(tw) - 1, by + 107], outline=(220, 255, 235, 140))
            d3.text((OUT_W / 2, by + 18), text, font=banner_font, fill=(255, 255, 255, 255), anchor="ma")
        frames.append(cv2.cvtColor(np.array(img.convert("RGB")), cv2.COLOR_RGB2BGR))
    return frames


def render_ultraedge_clip(frames: list[np.ndarray], buckets: list, spikes: list,
                          impact: int | None, out_path: Path, review_label: str = "REVIEW",
                          verdict: str | None = None, cards: list | None = None,
                          waveform_note: str | None = None) -> Path:
    """Composite the broadcast UltraEdge clip and write H.264 MP4 at out_path."""
    from core.testing_pipeline import _open_video_writer  # Chromium-safe H.264 (never raw mp4v)

    n = len(frames)
    if n < 2:
        raise ValueError("need at least 2 replay frames to render a clip")
    scaled = [cv2.resize(f, (OUT_W, OUT_H), interpolation=cv2.INTER_AREA) for f in frames]
    panel = _panel_base()
    tab_h = panel.height - PANEL_H
    spike_set = set(spikes or [])
    label = f"{review_label} · ULTRAEDGE"

    writer = _open_video_writer(Path(out_path), FPS, (OUT_W, OUT_H))
    try:
        for step in _timeline(n, impact):
            base = scaled[max(0, min(n - 1, step["ri"]))]
            img = Image.fromarray(cv2.cvtColor(base, cv2.COLOR_BGR2RGB)).convert("RGBA")
            draw = ImageDraw.Draw(img)
            _chip(draw, label)
            layer = panel.copy()
            ld = ImageDraw.Draw(layer)
            _draw_wave(ld, tab_h, n, buckets, step["reveal"], step["cursor"], spike_set,
                       step["glow"], step["marker"], impact, waveform_note)
            if step["flash"] > 0:
                ld.rectangle([0, tab_h, PANEL_W - 1, tab_h + PANEL_H - 1],
                             fill=(234, 248, 255, int(150 * step["flash"])))
            if step["alpha"] < 1.0:
                r, g, b, a = layer.split()
                layer.putalpha(a.point(lambda v: int(v * step["alpha"])))
            px = (OUT_W - PANEL_W) // 2
            py = OUT_H - PANEL_BOTTOM - PANEL_H - tab_h + step["rise"]
            img.alpha_composite(layer, (px, py))
            writer.write(cv2.cvtColor(np.array(img.convert("RGB")), cv2.COLOR_RGB2BGR))
        if verdict:
            for frame in _verdict_tail(scaled[-1], verdict, (cards or [])[:4], review_label):
                writer.write(frame)
    finally:
        writer.release()
    return Path(out_path)
