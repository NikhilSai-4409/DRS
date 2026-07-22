"""Slice a long nets recording into per-event clips for UltraEdge labeling.

You do NOT timestamp segments by hand. Record one long WAV at the nets, run this,
and it auto-detects every percussive transient and writes a ~1.5 s clip per event
into training_audio/unsorted/. Then sort the clips into the class folders
(ball_bat, ball_pad, ball_glove, ball_ground, ball_stump, ambient_noise, speech,
unknown) in Explorer — listen to each in any player; the FOLDER is the label.

    python scripts/slice_audio_events.py --input nets_session.wav
    python scripts/slice_audio_events.py --input nets_session.wav --threshold 4.0

Records from a phone are usually .m4a — convert once with ffmpeg:
    ffmpeg -i session.m4a -ac 1 -ar 44100 session.wav
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from scipy.io import wavfile
from scipy.signal import butter, sosfilt

ROOT = Path(__file__).resolve().parent.parent
UNSORTED = ROOT / "training" / "ultraedge" / "training_audio" / "unsorted"
CLIP_S = 1.5           # written clip length, centred on the transient
MIN_GAP_S = 0.4        # merge transients closer than this into one event


def detect_events(samples: np.ndarray, sr: int, threshold_sigma: float) -> list[int]:
    """Indices of percussive transients: band-limited energy above a rolling baseline."""
    sos = butter(4, [1500, min(8000, sr / 2 - 1)], btype="bandpass", fs=sr, output="sos")
    energy = np.abs(sosfilt(sos, samples.astype(np.float64)))
    win = max(1, int(sr * 0.010))
    smoothed = np.convolve(energy, np.ones(win) / win, mode="same")
    baseline = float(np.median(smoothed))
    spread = float(np.median(np.abs(smoothed - baseline))) * 1.4826 + 1e-9  # robust sigma
    above = smoothed > baseline + threshold_sigma * spread
    events, last = [], -sr
    for idx in np.nonzero(above)[0]:
        if idx - last >= int(MIN_GAP_S * sr):
            events.append(int(idx))
        last = idx
    return events


def main() -> None:
    parser = argparse.ArgumentParser(description="Auto-slice a nets recording into labelable event clips")
    parser.add_argument("--input", required=True, help="Long WAV recording (mono or stereo)")
    parser.add_argument("--threshold", type=float, default=5.0, help="Detection strictness in sigmas (lower = more clips)")
    parser.add_argument("--out", default=str(UNSORTED), help="Output folder for unsorted clips")
    args = parser.parse_args()

    sr, data = wavfile.read(args.input)
    if data.ndim > 1:
        data = data.mean(axis=1)
    data = data.astype(np.float64)
    peak = np.max(np.abs(data)) or 1.0
    data /= peak

    events = detect_events(data, sr, args.threshold)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    half = int(CLIP_S * sr / 2)
    stem = Path(args.input).stem
    for n, idx in enumerate(events, start=1):
        lo, hi = max(0, idx - half), min(data.size, idx + half)
        clip = (data[lo:hi] * 32767).astype(np.int16)
        seconds = idx / sr
        wavfile.write(out_dir / f"{stem}_{n:03d}_at{seconds:07.2f}s.wav", sr, clip)

    print(f"{len(events)} event clip(s) written to {out_dir}")
    print("Next: listen to each and MOVE it into the right class folder — the folder is the label:")
    for name in ("ball_bat", "ball_pad", "ball_glove", "ball_ground", "ball_stump",
                 "ambient_noise", "speech", "unknown"):
        print(f"  training_audio/{name}/")
    print("Then train:  python scripts/train_ultraedge.py")


if __name__ == "__main__":
    main()
