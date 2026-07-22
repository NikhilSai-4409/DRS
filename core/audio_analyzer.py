"""UltraEdge-style synchronized audio analyzer."""

from __future__ import annotations

from dataclasses import dataclass
import time

import numpy as np
from scipy.signal import butter, sosfilt

from config.settings import AUDIO_SAMPLE_RATE


@dataclass(slots=True)
class WaveformWindow:
    center_timestamp_ms: float
    samples: np.ndarray
    sample_rate: int


def envelope_buckets(samples: np.ndarray, timestamps_ms: np.ndarray,
                     start_ms: float, end_ms: float, buckets: int) -> dict:
    """Reduce raw ring samples inside [start_ms, end_ms] to per-bucket [min, max]
    pairs normalised to the window peak — render-ready waveform data whose
    resolution is independent of sample rate. The ring wraps, so selected samples
    are re-ordered by capture time before bucketing."""
    mask = (timestamps_ms >= start_ms) & (timestamps_ms <= end_ms)
    values = np.asarray(samples[mask], dtype=np.float32)
    if values.size:
        values = values[np.argsort(timestamps_ms[mask], kind="stable")]
    n = int(values.size)
    buckets = max(1, int(buckets))
    if n == 0:
        return {"buckets": [], "samples": 0, "peak": 0.0}
    peak = float(np.max(np.abs(values)))
    scale = peak if peak > 1e-9 else 1.0
    edges = np.linspace(0, n, buckets + 1).astype(int)
    pairs = []
    for i in range(buckets):
        seg = values[edges[i]:edges[i + 1]]
        if seg.size == 0:
            pairs.append([0.0, 0.0])
        else:
            pairs.append([round(float(seg.min() / scale), 4),
                          round(float(seg.max() / scale), 4)])
    return {"buckets": pairs, "samples": n, "peak": peak}


@dataclass(slots=True)
class EdgeResult:
    has_edge: bool
    edge_confidence: float
    edge_timestamp_ms: float
    waveform_data: np.ndarray
    spectrogram_data: np.ndarray
    reason: str


class AudioAnalyzer:
    """Maintains a rolling audio buffer and detects transient bat-ball edges."""

    def __init__(self, sample_rate: int = AUDIO_SAMPLE_RATE, buffer_seconds: float = 5.0) -> None:
        self.sample_rate = sample_rate
        self.buffer_seconds = buffer_seconds
        self.buffer_size = int(sample_rate * buffer_seconds)
        self.samples = np.zeros(self.buffer_size, dtype=np.float32)
        self.timestamps_ms = np.zeros(self.buffer_size, dtype=np.float64)
        self.write_index = 0
        self.running = False
        self.stream = None
        self.sos = butter(4, [2000, 8000], btype="bandpass", fs=sample_rate, output="sos")

    def start(self) -> None:
        try:
            import sounddevice as sd

            self.stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                callback=self._callback,
            )
            self.stream.start()
            self.running = True
        except Exception:
            self.running = False
            self.stream = None

    def stop(self) -> None:
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
        self.running = False

    def add_samples(self, samples: np.ndarray, start_timestamp_ms: float | None = None) -> None:
        values = np.asarray(samples, dtype=np.float32).reshape(-1)
        if values.size == 0:
            return
        start = time.time() * 1000.0 if start_timestamp_ms is None else start_timestamp_ms
        for idx, sample in enumerate(values):
            pos = self.write_index % self.buffer_size
            self.samples[pos] = sample
            self.timestamps_ms[pos] = start + (idx / self.sample_rate) * 1000.0
            self.write_index += 1

    def get_waveform_window(self, center_timestamp: float, duration_s: float = 1.0) -> WaveformWindow:
        half_ms = duration_s * 500.0
        mask = (self.timestamps_ms >= center_timestamp - half_ms) & (self.timestamps_ms <= center_timestamp + half_ms)
        window = self.samples[mask]
        if window.size > 1024:
            indices = np.linspace(0, window.size - 1, 1024).astype(int)
            window = window[indices]
        return WaveformWindow(center_timestamp, window.astype(np.float32), self.sample_rate)

    def detect_edge_at(self, timestamp_ms: float) -> EdgeResult:
        window = self.get_waveform_window(timestamp_ms, duration_s=1.0)
        if window.samples.size < 64:
            return EdgeResult(False, 0.0, timestamp_ms, window.samples, np.empty((0, 0)), "No audio samples available around impact.")
        filtered = sosfilt(self.sos, window.samples)
        energy = np.abs(filtered)
        baseline = energy[: max(16, energy.size // 3)]
        threshold = float(np.mean(baseline) + 3.0 * np.std(baseline))
        peak_idx = int(np.argmax(energy))
        peak = float(energy[peak_idx])
        confidence = float(min(1.0, peak / max(threshold, 1e-6) / 2.0))
        freqs = np.abs(np.fft.rfft(filtered * np.hanning(filtered.size)))
        spectrogram = freqs.reshape(1, -1)
        edge_ts = timestamp_ms - 500.0 + (peak_idx / max(1, filtered.size - 1)) * 1000.0
        return EdgeResult(
            has_edge=peak > threshold,
            edge_confidence=confidence,
            edge_timestamp_ms=edge_ts,
            waveform_data=window.samples,
            spectrogram_data=spectrogram,
            reason="Transient spike detected above 3 sigma baseline." if peak > threshold else "No transient edge spike above threshold.",
        )

    def _callback(self, indata, frames, time_info, status) -> None:
        timestamp_ms = time.time() * 1000.0
        self.add_samples(np.asarray(indata).reshape(-1), timestamp_ms)
