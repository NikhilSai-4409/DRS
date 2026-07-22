"""Train the UltraEdge sound classifier from the labeled clip folders.

    python scripts/train_ultraedge.py

Dataset = training/ultraedge/training_audio/<class>/*.wav — the folder is the
label; no annotation files. Trains a RandomForest on the shared feature vector
(core/audio_features.py), reports per-class precision/recall + a confusion
matrix into training/ultraedge/reports/, and saves the model + metadata into
training/ultraedge/models/. The live backend picks the model up automatically
on next start (core/audio_classifier.py).

Augmentation (noise + gain + time shift) multiplies small datasets; a stratified
hold-out split keeps the report honest. CPU-only, trains in seconds.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.audio_features import extract_features  # noqa: E402

AUDIO_DIR = ROOT / "training" / "ultraedge" / "training_audio"
MODELS_DIR = ROOT / "training" / "ultraedge" / "models"
REPORTS_DIR = ROOT / "training" / "ultraedge" / "reports"
CLASSES = ("ball_bat", "ball_pad", "ball_glove", "ball_ground", "ball_stump",
           "ambient_noise", "speech", "unknown")
AUGMENT_PER_CLIP = 4
MIN_PER_CLASS = 8


def _augment(samples: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    out = samples.copy()
    out = out * rng.uniform(0.6, 1.4)                                   # gain
    out = np.roll(out, rng.integers(-len(out) // 10, len(out) // 10))   # time shift
    out = out + rng.normal(0, 0.005 * (np.std(out) + 1e-6), out.shape)  # noise floor
    return out


def load_dataset():
    from scipy.io import wavfile

    X, y, counts = [], [], {}
    rng = np.random.default_rng(7)
    for label in CLASSES:
        folder = AUDIO_DIR / label
        files = sorted(folder.glob("*.wav")) if folder.exists() else []
        counts[label] = len(files)
        for path in files:
            try:
                sr, data = wavfile.read(path)
            except Exception:
                continue
            if data.ndim > 1:
                data = data.mean(axis=1)
            data = data.astype(np.float64)
            X.append(extract_features(data, sr)); y.append(label)
            for _ in range(AUGMENT_PER_CLIP):
                X.append(extract_features(_augment(data, rng), sr)); y.append(label)
    return np.array(X), np.array(y), counts


def main() -> None:
    try:
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.metrics import classification_report, confusion_matrix
        from sklearn.model_selection import train_test_split
        import joblib
    except ImportError:
        print("scikit-learn is required:  pip install scikit-learn joblib")
        sys.exit(1)

    X, y, counts = load_dataset()
    present = [c for c in CLASSES if counts.get(c, 0) > 0]
    thin = [c for c in present if counts[c] < MIN_PER_CLASS]
    print("Clips per class:", {k: v for k, v in counts.items() if v})
    if len(present) < 2:
        print(f"\nNeed clips in at least 2 class folders under {AUDIO_DIR}")
        print("Record at the nets, slice with scripts/slice_audio_events.py, sort, re-run.")
        sys.exit(1)
    if thin:
        print(f"NOTE: thin classes (<{MIN_PER_CLASS} clips): {thin} — expect weak recall there.")

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, stratify=y, random_state=7)
    model = RandomForestClassifier(n_estimators=300, class_weight="balanced", random_state=7, n_jobs=-1)
    model.fit(X_train, y_train)
    predictions = model.predict(X_test)

    labels_sorted = sorted(set(y))
    report = classification_report(y_test, predictions, labels=labels_sorted, zero_division=0)
    matrix = confusion_matrix(y_test, predictions, labels=labels_sorted)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / f"train_{stamp}.txt"
    matrix_text = "confusion matrix (rows=truth, cols=predicted)\n" + " ".join(labels_sorted) + "\n" + str(matrix)
    report_path.write_text(report + "\n\n" + matrix_text, encoding="utf-8")
    print("\n" + report)
    print(matrix_text)

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    model_path = MODELS_DIR / "ultraedge_rf.joblib"
    joblib.dump(model, model_path)
    (MODELS_DIR / "ultraedge_rf.json").write_text(json.dumps({
        "trained_at": stamp, "classes": labels_sorted,
        "clips_per_class": counts, "augment_per_clip": AUGMENT_PER_CLIP,
        "holdout_accuracy": float((predictions == y_test).mean()),
        "feature_size": int(X.shape[1]),
    }, indent=2), encoding="utf-8")
    print(f"\nModel saved: {model_path}")
    print("The backend loads it automatically on next start — live spikes get class labels.")


if __name__ == "__main__":
    main()
