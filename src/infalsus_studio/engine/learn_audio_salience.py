"""Learn salience from an explicitly supplied official corpus."""
from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score, precision_recall_curve

from .generate_phrase_charts import musical_events, salience_features

BASE = Path(__file__).resolve().parent
ROOT = BASE / "revision2"


def train(corpus_root, songs, heldout_songs, output_root, progress=None, cancel=None):
    corpus_root, output_root = Path(corpus_root), Path(output_root); target = output_root / "revision2"; target.mkdir(parents=True, exist_ok=True)
    heldout_songs = set(heldout_songs); xs = []; ys = []; tests = []; song_count = 0
    for song in songs:
        if cancel and cancel(): raise RuntimeError("calibration cancelled")
        if not song.get("BaseName") or not any(chart.get("Available") for chart in song.get("ChartInfos", [])): continue
        chart = max((row for row in song["ChartInfos"] if row.get("Available")), key=lambda row: row["Difficulty"])
        notes = json.loads((corpus_root / "charts" / f"{chart['Id']}.json").read_text(encoding="utf8"))["notes"]
        points = np.unique([note["start"] / 1000 for note in notes if note["type"] in (1, 2, 4)])
        if not len(points): continue
        audio = np.load(corpus_root / "features" / f"{song['BaseName']}.npz"); times, features = audio["t"], audio["features"]
        events, _ = musical_events(times, features)
        if not events: continue
        event_times = times[[row[0] for row in events]]; index = np.searchsorted(points, event_times)
        distance = np.minimum(abs(event_times - points[np.maximum(0, index - 1)]), abs(event_times - points[np.minimum(len(points) - 1, index)]))
        xs.append(salience_features(times, features, events)); ys.append(distance <= .035); tests.extend([song["BaseName"] in heldout_songs] * len(events)); song_count += 1
        if progress: progress("salience", song_count, len(songs))
    if song_count < 4: raise ValueError("calibration needs at least four songs with audio events")
    assert song_count >= 4
    x, y, test = np.vstack(xs), np.concatenate(ys), np.asarray(tests)
    if not test.any() or test.all() or len(np.unique(y[~test])) < 2: raise ValueError("insufficient held-out salience data")
    def model(): return HistGradientBoostingClassifier(max_iter=160, max_leaf_nodes=15, min_samples_leaf=70, l2_regularization=4, random_state=20260913)
    checked = model().fit(x[~test], y[~test]); probabilities = checked.predict_proba(x[test])[:, 1]
    precision, recall, thresholds = precision_recall_curve(y[test], probabilities); valid = np.flatnonzero(recall[:-1] >= .9)
    if not len(valid): raise ValueError("held-out salience data has no high-recall threshold")
    index = valid[np.argmax(precision[valid])]; threshold = float(thresholds[index])
    joblib.dump({"model": model().fit(x, y), "threshold": threshold}, target / "salience-model.joblib")
    return {"songs": song_count, "candidate_events": len(y), "holdout_AP": float(average_precision_score(y[test], probabilities)), "threshold": threshold}
