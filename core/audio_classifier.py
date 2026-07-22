"""Runtime UltraEdge sound classifier.

Loads the RandomForest trained by scripts/train_ultraedge.py (if one exists) and
labels detected transients: ball_bat / ball_pad / ball_glove / ball_ground /
ball_stump / ambient_noise / speech / unknown. Honest by construction — when no
trained model exists, ``available`` is False and callers keep reporting plain
unlabeled spikes rather than pretending to know what they were.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from core.audio_features import extract_features
from utils.logger import get_logger

log = get_logger("audio_classifier")

MODEL_PATH = (Path(__file__).resolve().parent.parent
              / "training" / "ultraedge" / "models" / "ultraedge_rf.joblib")

# Sounds that are evidence of BAT involvement vs noise to be filtered out.
BAT_CLASSES = {"ball_bat"}
NOISE_CLASSES = {"ambient_noise", "speech", "unknown"}


class UltraEdgeClassifier:
    def __init__(self, model_path: Path = MODEL_PATH):
        self.model = None
        self.available = False
        try:
            if model_path.exists():
                import joblib

                self.model = joblib.load(model_path)
                self.available = True
                log.info("UltraEdge classifier loaded: {}", model_path.name)
        except Exception as exc:
            log.warning("UltraEdge classifier unavailable: {}", exc)

    def classify(self, samples: np.ndarray, sample_rate: int) -> dict | None:
        """Label one transient window. None when no model is trained yet."""
        if not self.available or self.model is None:
            return None
        features = extract_features(np.asarray(samples), sample_rate).reshape(1, -1)
        probabilities = self.model.predict_proba(features)[0]
        classes = list(self.model.classes_)
        best = int(np.argmax(probabilities))
        label = str(classes[best])
        return {
            "label": label,
            "label_confidence": round(float(probabilities[best]), 3),
            "is_bat": label in BAT_CLASSES,
            "is_noise": label in NOISE_CLASSES,
        }
