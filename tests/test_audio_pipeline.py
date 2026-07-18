"""Tests for the UltraEdge audio infrastructure (no microphone required)."""

from __future__ import annotations

import numpy as np

from core.audio_pipeline import AudioSyncResult, UltraEdgeAudioPipeline


def test_synchronize_applies_offset():
    pipeline = UltraEdgeAudioPipeline()
    pipeline.set_video_offset(12.0)
    result = pipeline.synchronize(1000.0)
    assert isinstance(result, AudioSyncResult)
    assert result.aligned_timestamp_ms == 1012.0


def test_filter_noise_and_envelope_shapes():
    pipeline = UltraEdgeAudioPipeline()
    signal = np.random.RandomState(0).randn(2048).astype(np.float32)
    filtered = pipeline.filter_noise(signal)
    envelope = pipeline.envelope(signal)
    assert filtered.shape == signal.shape
    assert envelope.shape == signal.shape
    assert np.all(envelope >= 0)


def test_estimate_offset_recovers_known_lag():
    pipeline = UltraEdgeAudioPipeline(sample_rate=44100)
    reference = np.random.RandomState(1).randn(4000).astype(np.float32)
    lag = 200
    delayed = np.zeros_like(reference)
    delayed[lag:] = reference[:-lag]
    estimated = pipeline.estimate_offset_ms(reference, delayed)
    expected = lag / 44100 * 1000.0
    assert abs(estimated - expected) < 0.2
