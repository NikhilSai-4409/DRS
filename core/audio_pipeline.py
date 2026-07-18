"""UltraEdge audio infrastructure: capture -> filter -> sync -> waveform.

Wires the existing audio building blocks (the rolling buffer and bandpass filter
in core.audio_analyzer) into an ordered pipeline whose stages can be run
independently. It deliberately does NOT make caught-behind decisions; it
prepares time-aligned, noise-filtered waveforms and amplitude envelopes for a
future UltraEdge decision stage.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.signal import butter, sosfilt

from config.settings import AUDIO_SAMPLE_RATE, EDGE_FREQ_HIGH_HZ, EDGE_FREQ_LOW_HZ
from core.audio_analyzer import AudioAnalyzer, WaveformWindow


@dataclass(slots=True)
class AudioSyncResult:
    audio_offset_ms: float
    aligned_timestamp_ms: float


class UltraEdgeAudioPipeline:
    """Ordered audio infrastructure for synchronized edge analysis.

    Stages:
      1. capture       start_capture / ingest         (microphone or fed samples)
      2. noise filter  filter_noise                   (band-limit to edge band)
      3. synchronize   set_video_offset / synchronize (audio<->video clock)
      4. waveform      extract_waveform               (filtered, time-aligned)
      5. envelope      envelope                       (amplitude over time)
      6. alignment     estimate_offset_ms             (cross-correlation lag)
    """

    def __init__(
        self,
        sample_rate: int = AUDIO_SAMPLE_RATE,
        low_hz: float = EDGE_FREQ_LOW_HZ,
        high_hz: float = EDGE_FREQ_HIGH_HZ,
        buffer_seconds: float = 5.0,
    ) -> None:
        self.sample_rate = sample_rate
        self.low_hz = low_hz
        self.high_hz = high_hz
        self.analyzer = AudioAnalyzer(sample_rate=sample_rate, buffer_seconds=buffer_seconds)
        self.sos = butter(4, [low_hz, high_hz], btype="bandpass", fs=sample_rate, output="sos")
        self.audio_offset_ms = 0.0

    # Stage 1: capture --------------------------------------------------------
    def start_capture(self) -> None:
        self.analyzer.start()

    def stop_capture(self) -> None:
        self.analyzer.stop()

    def ingest(self, samples: np.ndarray, start_timestamp_ms: float | None = None) -> None:
        self.analyzer.add_samples(samples, start_timestamp_ms)

    # Stage 2: noise filtering ------------------------------------------------
    def filter_noise(self, samples: np.ndarray) -> np.ndarray:
        values = np.asarray(samples, dtype=np.float32).reshape(-1)
        if values.size == 0:
            return values
        return sosfilt(self.sos, values).astype(np.float32)

    # Stage 3: synchronization with video ------------------------------------
    def set_video_offset(self, audio_offset_ms: float) -> None:
        self.audio_offset_ms = float(audio_offset_ms)

    def synchronize(self, video_timestamp_ms: float) -> AudioSyncResult:
        aligned = float(video_timestamp_ms) + self.audio_offset_ms
        return AudioSyncResult(self.audio_offset_ms, aligned)

    # Stage 4: time-aligned, filtered edge waveform --------------------------
    def extract_waveform(self, video_timestamp_ms: float, duration_s: float = 0.2) -> WaveformWindow:
        aligned = self.synchronize(video_timestamp_ms).aligned_timestamp_ms
        window = self.analyzer.get_waveform_window(aligned, duration_s=duration_s)
        filtered = self.filter_noise(window.samples)
        return WaveformWindow(window.center_timestamp_ms, filtered, window.sample_rate)

    # Stage 5: amplitude envelope --------------------------------------------
    def envelope(self, samples: np.ndarray) -> np.ndarray:
        return np.abs(self.filter_noise(samples))

    # Stage 6: timestamp alignment (cross-correlation lag) -------------------
    def estimate_offset_ms(self, reference: np.ndarray, signal: np.ndarray) -> float:
        ref = np.asarray(reference, dtype=np.float32).reshape(-1)
        sig = np.asarray(signal, dtype=np.float32).reshape(-1)
        if ref.size == 0 or sig.size == 0:
            return 0.0
        correlation = np.correlate(sig - sig.mean(), ref - ref.mean(), mode="full")
        lag_samples = int(np.argmax(correlation)) - (ref.size - 1)
        return lag_samples / self.sample_rate * 1000.0
