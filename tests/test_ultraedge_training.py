"""End-to-end proof of the UltraEdge sound-training toolkit — runs BEFORE any real
nets recordings exist, on synthetic sounds with the acoustic character of the real
classes: a bat 'snick' (short broadband click, fast decay) vs a pad 'thud'
(low-frequency, slow decay) vs ambient noise. If features + trainer + runtime
classifier can't separate these, they won't separate the real thing."""

from __future__ import annotations

import numpy as np
import pytest

from core.audio_features import FEATURE_SIZE, extract_features

SR = 44100
RNG = np.random.default_rng(11)


def _snick() -> np.ndarray:
    """Sharp broadband transient, ~8 ms, fast decay — bat edge character."""
    n = int(SR * 0.5)
    x = RNG.normal(0, 0.002, n)
    hit = int(n * 0.4)
    burst = RNG.normal(0, 1.0, int(SR * 0.008)) * np.exp(-np.linspace(0, 6, int(SR * 0.008)))
    x[hit:hit + burst.size] += burst
    return x


def _thud() -> np.ndarray:
    """Low-frequency thump, slow decay — pad/body character."""
    n = int(SR * 0.5)
    x = RNG.normal(0, 0.002, n)
    hit = int(n * 0.4)
    dur = int(SR * 0.09)
    t = np.linspace(0, dur / SR, dur)
    x[hit:hit + dur] += np.sin(2 * np.pi * 150 * t) * np.exp(-t * 40)
    return x


def _ambient() -> np.ndarray:
    return RNG.normal(0, 0.01, int(SR * 0.5))


def test_feature_vector_shape_and_determinism() -> None:
    clip = _snick()
    a = extract_features(clip, SR)
    b = extract_features(clip, SR)
    assert a.shape == (FEATURE_SIZE,)
    assert np.allclose(a, b)


def test_train_and_classify_synthetic_classes(tmp_path) -> None:
    sklearn = pytest.importorskip("sklearn")  # noqa: F841
    import joblib
    from sklearn.ensemble import RandomForestClassifier

    X, y = [], []
    for _ in range(24):
        X.append(extract_features(_snick(), SR)); y.append("ball_bat")
        X.append(extract_features(_thud(), SR)); y.append("ball_pad")
        X.append(extract_features(_ambient(), SR)); y.append("ambient_noise")
    X, y = np.array(X), np.array(y)
    model = RandomForestClassifier(n_estimators=100, random_state=3)
    model.fit(X[: len(X) // 2], y[: len(y) // 2])
    accuracy = float((model.predict(X[len(X) // 2:]) == y[len(y) // 2:]).mean())
    assert accuracy >= 0.9, f"synthetic classes must separate cleanly, got {accuracy}"

    # Runtime round-trip: the live classifier loads the model and labels a bat snick,
    # marks ambient as noise, and stays honest when no model exists.
    model_path = tmp_path / "ultraedge_rf.joblib"
    joblib.dump(model, model_path)
    from core.audio_classifier import UltraEdgeClassifier

    clf = UltraEdgeClassifier(model_path=model_path)
    assert clf.available
    verdict = clf.classify(_snick(), SR)
    assert verdict["label"] == "ball_bat" and verdict["is_bat"] is True
    noise = clf.classify(_ambient(), SR)
    assert noise["is_noise"] is True

    missing = UltraEdgeClassifier(model_path=tmp_path / "nope.joblib")
    assert missing.available is False
    assert missing.classify(_snick(), SR) is None
