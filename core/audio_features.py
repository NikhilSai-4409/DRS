"""Deterministic audio features for the UltraEdge sound classifier.

ONE implementation shared by the trainer (scripts/train_ultraedge.py) and the
runtime (core/audio_classifier.py) so a model always sees the exact features it
was trained on. numpy/scipy only — no librosa/torchaudio dependency.

A clip (~0.3–2 s around one contact sound) becomes a fixed-length vector:
log-mel band statistics + MFCC statistics + a few percussive shape scalars
(centroid, rolloff, zero-crossing rate, decay time). Percussive events separate
mostly on band energy balance (crack vs thud) and decay, which these capture.
"""

from __future__ import annotations

import numpy as np
from scipy.fftpack import dct

N_MELS = 26
N_MFCC = 13
FRAME_MS = 25.0
HOP_MS = 10.0
FMIN_HZ = 100.0


def _hz_to_mel(hz):
    return 2595.0 * np.log10(1.0 + np.asarray(hz) / 700.0)


def _mel_to_hz(mel):
    return 700.0 * (10.0 ** (np.asarray(mel) / 2595.0) - 1.0)


def _mel_filterbank(sample_rate: int, n_fft: int) -> np.ndarray:
    fmax = sample_rate / 2.0
    mel_points = np.linspace(_hz_to_mel(FMIN_HZ), _hz_to_mel(fmax), N_MELS + 2)
    hz_points = _mel_to_hz(mel_points)
    bins = np.floor((n_fft + 1) * hz_points / sample_rate).astype(int)
    bank = np.zeros((N_MELS, n_fft // 2 + 1), dtype=np.float64)
    for m in range(1, N_MELS + 1):
        lo, mid, hi = bins[m - 1], bins[m], bins[m + 1]
        if mid == lo:
            mid += 1
        if hi <= mid:
            hi = mid + 1
        for k in range(lo, min(mid, bank.shape[1])):
            bank[m - 1, k] = (k - lo) / max(1, mid - lo)
        for k in range(mid, min(hi, bank.shape[1])):
            bank[m - 1, k] = (hi - k) / max(1, hi - mid)
    return bank


def extract_features(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    """Fixed-length feature vector for one clip. Deterministic; safe on short clips."""
    x = np.asarray(samples, dtype=np.float64).reshape(-1)
    if x.size == 0:
        x = np.zeros(int(sample_rate * 0.1))
    peak = np.max(np.abs(x))
    if peak > 0:
        x = x / peak                       # gain-invariant
    frame = max(64, int(sample_rate * FRAME_MS / 1000.0))
    hop = max(32, int(sample_rate * HOP_MS / 1000.0))
    if x.size < frame:
        x = np.pad(x, (0, frame - x.size))
    n_frames = 1 + (x.size - frame) // hop
    window = np.hanning(frame)
    bank = _mel_filterbank(sample_rate, frame)

    mel_frames = np.zeros((n_frames, N_MELS))
    centroids = np.zeros(n_frames)
    rolloffs = np.zeros(n_frames)
    freqs = np.fft.rfftfreq(frame, d=1.0 / sample_rate)
    for i in range(n_frames):
        seg = x[i * hop: i * hop + frame] * window
        power = np.abs(np.fft.rfft(seg)) ** 2
        mel_frames[i] = np.log10(bank @ power + 1e-10)
        total = power.sum() + 1e-12
        centroids[i] = float((freqs * power).sum() / total)
        cumulative = np.cumsum(power)
        rolloffs[i] = float(freqs[int(np.searchsorted(cumulative, 0.85 * cumulative[-1]))])

    mfcc = dct(mel_frames, type=2, axis=1, norm="ortho")[:, :N_MFCC]

    # Percussive shape: envelope decay time from the peak to 10% of it.
    envelope = np.abs(x)
    peak_idx = int(np.argmax(envelope))
    tail = envelope[peak_idx:]
    below = np.nonzero(tail < 0.1)[0]
    decay_ms = (below[0] / sample_rate * 1000.0) if below.size else (tail.size / sample_rate * 1000.0)
    zcr = float(np.mean(np.abs(np.diff(np.signbit(x))))) if x.size > 1 else 0.0

    return np.concatenate([
        mel_frames.mean(axis=0), mel_frames.std(axis=0),
        mfcc.mean(axis=0), mfcc.std(axis=0),
        [centroids.mean(), centroids.std(), rolloffs.mean(), rolloffs.std(),
         zcr, min(decay_ms, 1000.0) / 1000.0],
    ]).astype(np.float32)


FEATURE_SIZE = 2 * N_MELS + 2 * N_MFCC + 6
