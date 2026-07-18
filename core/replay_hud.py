"""Broadcast HUD — first-class TV graphics, drawn AFTER the post chain so they stay crisp.
Typography: real Arial Bold (PIL/TrueType), not a debug stroke font. Colors/proportions
measured from the f92 broadcast reference (cards x259 w398, 50+50px rows at 1920).
All metrics are in 1920-space; pass scale (e.g. 2/3 for a 1280x720 frame).
Used by BOTH replays so the graphics language is identical."""
import cv2, numpy as np
from PIL import Image, ImageDraw, ImageFont

_FONT_CACHE = {}


def _font(px):
    px = int(px)
    if px not in _FONT_CACHE:
        for path in (r"C:\Windows\Fonts\arialbd.ttf", r"C:\Windows\Fonts\arial.ttf"):
            try:
                _FONT_CACHE[px] = ImageFont.truetype(path, px)
                break
            except OSError:
                continue
        else:
            _FONT_CACHE[px] = ImageFont.load_default()
    return _FONT_CACHE[px]


def _vgrad_rgb(w, h, stops):
    """stops = [(pos, (r,g,b)), ...] vertical gradient as PIL Image."""
    out = np.zeros((h, w, 3), np.uint8)
    ys = np.linspace(0, 1, h)
    for i, y in enumerate(ys):
        for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
            if p0 <= y <= p1:
                f = (y - p0) / max(p1 - p0, 1e-6)
                out[i, :] = [int(a + (b - a) * f) for a, b in zip(c0, c1)]
                break
    return Image.fromarray(out)


HDR = [(0.0, (0x46, 0xce, 0xde)), (0.30, (0x0d, 0x88, 0xa0)), (1.0, (0x05, 0x3e, 0x54))]
TONES = {"green": [(0.0, (0x46, 0xd3, 0x57)), (1.0, (0x15, 0x70, 0x21))],
         "red": [(0.0, (0xf0, 0x4b, 0x46)), (1.0, (0x9c, 0x0a, 0x0d))],
         "grey": [(0.0, (0x7a, 0x84, 0x94)), (1.0, (0x32, 0x39, 0x44))],
         "amber": [(0.0, (0xe2, 0xa8, 0x2e)), (1.0, (0x8a, 0x5f, 0x0a))]}


def _ease(v):
    v = min(1.0, max(0.0, v))
    return v * v * (3 - 2 * v)


def _card_img(w, hh, hs, label, status, tone):
    """One complete card (border + header + status) as a PIL image with alpha."""
    B = 3
    img = Image.new("RGBA", (w + 2 * B, hh + hs + 2 * B), (0, 0, 0, 0))
    dr = ImageDraw.Draw(img)
    dr.rectangle([0, 0, w + 2 * B - 1, hh + hs + 2 * B - 1], fill=(207, 224, 239, 255))
    img.paste(_vgrad_rgb(w, hh, HDR), (B, B))
    img.paste(_vgrad_rgb(w, hs, TONES.get(tone, TONES["grey"])), (B, B + hh))
    dr = ImageDraw.Draw(img)
    for txt, cy, fh in [(label, B + hh // 2, hh), (status, B + hh + hs // 2, hs)]:
        f = _font(fh * 0.72)                                        # broadcast type: fill the bar
        tw = dr.textlength(txt, font=f)
        x = B + (w - tw) / 2
        dr.text((x + 2, cy + 2 - fh * 0.40), txt, font=f, fill=(0, 0, 0, 140))     # shadow
        dr.text((x, cy - fh * 0.40), txt, font=f, fill=(255, 255, 255, 255))
    return img


def _blit(bgr, pil_rgba, x, y, alpha=1.0):
    """Alpha-composite a PIL RGBA onto a BGR np frame at (x, y)."""
    w, h = pil_rgba.size
    H, W = bgr.shape[:2]
    x, y = int(x), int(y)
    if x >= W or y >= H: return
    w = min(w, W - x); h = min(h, H - y)
    fg = np.asarray(pil_rgba)[:h, :w].astype(np.float32)
    a = (fg[..., 3:4] / 255.0) * alpha
    rgb = fg[..., :3][..., ::-1]                                   # RGB -> BGR
    bgr[y:y + h, x:x + w] = (rgb * a + bgr[y:y + h, x:x + w] * (1 - a)).astype(np.uint8)


def draw_hud(img, t_ms, cards, tag, banner_at=4100, scale=2 / 3):
    """Metrics in 1920-space x scale. All four cards enter early and stay."""
    s = scale
    CX, CW, HH, HS = round(170 * s), round(490 * s), round(68 * s), round(68 * s)
    rows = [("ORIGINAL DECISION", cards["original"], "green" if cards["original"] == "NOT OUT" else "red", 96),
            ("PITCHING", cards["pitching"], cards.get("pitching_tone", "grey"), 510),
            ("IMPACT", cards["impact"], cards.get("impact_tone", "grey"), 690),
            ("WICKETS", cards["wickets"], cards.get("wickets_tone", "grey"), 870)]
    for i, (lab, stat, tone, y1920) in enumerate(rows):
        a = _ease((t_ms - (150 + i * 120)) / 260)
        if a > 0.02:
            _blit(img, _card_img(CW, HH, HS, lab, stat, tone), CX, round(y1920 * s), a)
    if t_ms >= banner_at:
        a = _ease((t_ms - banner_at) / 350)
        H, W = img.shape[:2]
        dec = cards["decision"]
        tone = "green" if "NOT" in dec else ("amber" if "INCONCLUSIVE" in dec else "red")
        bw, bh = round(1040 * s), round(150 * s)
        card_right = round((170 + 490) * s)            # keep clear of the WICKETS card
        bimg = Image.new("RGBA", (bw + 8, bh + 8), (0, 0, 0, 0))
        dr = ImageDraw.Draw(bimg)
        dr.rectangle([0, 0, bw + 7, bh + 7], fill=(207, 224, 239, 255))
        bimg.paste(_vgrad_rgb(bw, bh, TONES[tone]), (4, 4))
        dr = ImageDraw.Draw(bimg)
        f = _font(bh * 0.68)
        tw = dr.textlength(dec, font=f)
        dr.text((4 + (bw - tw) / 2 + 2, 4 + bh * 0.13 + 2), dec, font=f, fill=(0, 0, 0, 140))
        dr.text((4 + (bw - tw) / 2, 4 + bh * 0.13), dec, font=f, fill=(255, 255, 255, 255))
        _blit(img, bimg, max((W - bw) // 2, card_right + round(30 * s)), H - round(190 * s), a)
    H, W = img.shape[:2]
    cv2.rectangle(img, (8, H - 28), (int(8 + 520 * s * 1.34), H - 6), (0, 0, 0), -1)
    cv2.putText(img, tag, (14, H - 13), cv2.FONT_HERSHEY_SIMPLEX, 0.38 * s * 1.5, (77, 211, 255), 1, cv2.LINE_AA)
    return img
