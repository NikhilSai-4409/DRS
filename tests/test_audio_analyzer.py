import numpy as np

from core.audio_analyzer import AudioAnalyzer, envelope_buckets


def test_edge_transient_detected():
    analyzer = AudioAnalyzer()
    samples = np.random.default_rng(1).normal(0, 0.005, analyzer.sample_rate).astype(np.float32)
    samples[analyzer.sample_rate // 2] = 1.0
    analyzer.add_samples(samples, 0.0)
    result = analyzer.detect_edge_at(500.0)
    assert result.has_edge
    assert result.edge_confidence > 0


def test_clean_noise_no_edge():
    analyzer = AudioAnalyzer()
    samples = np.zeros(analyzer.sample_rate, dtype=np.float32)
    analyzer.add_samples(samples, 0.0)
    result = analyzer.detect_edge_at(500.0)
    assert not result.has_edge


def test_envelope_buckets_normalises_and_localises_spike():
    # 1 s of quiet noise stamped at 1000 ms with a hard spike at 1500 ms: the
    # envelope must have exactly the requested buckets, peak-normalise to 1.0,
    # and place that peak in the middle bucket (frame-sync depends on this).
    analyzer = AudioAnalyzer()
    rng = np.random.default_rng(2)
    samples = rng.normal(0, 0.01, analyzer.sample_rate).astype(np.float32)
    samples[analyzer.sample_rate // 2] = 0.8
    analyzer.add_samples(samples, 1000.0)
    data = envelope_buckets(analyzer.samples, analyzer.timestamps_ms, 1000.0, 2000.0, 100)
    assert len(data["buckets"]) == 100
    assert data["samples"] == analyzer.sample_rate
    extremes = [max(abs(lo), abs(hi)) for lo, hi in data["buckets"]]
    assert max(extremes) == 1.0
    assert abs(extremes.index(max(extremes)) - 50) <= 1
    assert data["peak"] > 0.5


def test_envelope_buckets_empty_window_is_honest():
    analyzer = AudioAnalyzer()
    data = envelope_buckets(analyzer.samples, analyzer.timestamps_ms, 5000.0, 6000.0, 50)
    assert data == {"buckets": [], "samples": 0, "peak": 0.0}
