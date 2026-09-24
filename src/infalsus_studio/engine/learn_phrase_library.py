"""Learn stock phrase templates from an explicitly supplied corpus."""
from __future__ import annotations

import collections
import copy
import json
import math
import struct
from pathlib import Path

import joblib
import numpy as np
from scipy import signal
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error
from sklearn.preprocessing import StandardScaler

BASE = Path(__file__).resolve().parent
ROOT = BASE / "revision2"
RATING_FEATURES = ["density", "peak_2s", "chord_fraction", "side_key_fraction", "flick_fraction", "hold_key_fraction", "field_coverage", "field_motion_per_second", "wide_keyboard_fraction"]


def union_duration(intervals):
    total = 0; end = -1e10
    for start, finish in sorted(intervals):
        total += max(0, finish - max(start, end)); end = max(end, finish)
    return total


def chart_metrics(notes, duration):
    attacks = [note for note in notes if note["type"] in (1, 2, 4)]
    keys = [note for note in attacks if note["side"] != 4]
    fields = [note for note in notes if note["type"] == 5]
    times = np.array(sorted(note["start"] / 1000 for note in attacks))
    span = max(1, (max(note["end"] for note in notes) - min(note["start"] for note in notes)) / 1000)
    unique = np.unique(times)
    peak = max((np.searchsorted(times, time + 2) - index) / 2 for index, time in enumerate(times)) if len(times) else 0
    chords = sum(value > 1 for value in collections.Counter(note["start"] for note in attacks).values()) / max(1, len(unique))
    coverage = union_duration([(note["start"] / 1000, note["end"] / 1000) for note in fields]) / span
    islands = 0; last = -1e9
    for note in sorted(fields, key=lambda value: value["start"]):
        islands += note["start"] > last + 100; last = max(last, note["end"])
    flicks = [note for note in attacks if note["type"] == 4]
    flick_times = np.array(sorted(note["start"] for note in flicks))
    return {"strikes": len(attacks), "density": len(attacks) / span, "peak_2s": peak, "chord_fraction": chords,
            "side_key_fraction": sum(note["side"] in (2, 3) for note in keys) / max(1, len(keys)),
            "flick_fraction": len(flicks) / max(1, len(attacks)), "flick_run_links": int(np.sum(np.diff(flick_times) <= 700)),
            "hold_key_fraction": sum(note["type"] == 2 for note in keys) / max(1, len(keys)),
            "field_coverage": coverage, "field_islands": islands,
            "field_motion_per_second": sum(abs(note["x1"][0] / note["x1"][1] - note["x0"][0] / note["x0"][1]) for note in fields) / span,
            "wide_keyboard_fraction": sum(note["w0"][0] / note["w0"][1] >= .99 for note in keys) / max(1, len(keys)), "span": span}


def rating_features(metrics):
    return [metrics[key] for key in RATING_FEATURES]


def descriptor(times, features, start, finish):
    ids = np.flatnonzero((times >= start) & (times < finish)); values = features[ids]
    if len(values) < 4:
        return np.zeros(54, dtype="float32")
    peaks, _ = signal.find_peaks(values[:, 0], prominence=.1, distance=4)
    gaps = np.diff(times[ids[peaks]])
    return np.r_[np.mean(values, axis=0), np.std(values, axis=0), len(peaks) / max(.1, finish - start),
                 np.quantile(values[:, 0], .9), np.quantile(values[:, 5], .1), np.quantile(values[:, 5], .9),
                 np.median(gaps) if len(gaps) else 1, np.std(gaps) if len(gaps) else 0].astype("float32")


def train(corpus_root, songs, heldout_songs, output_root, progress=None, cancel=None):
    """Write phrase-library.joblib and stock-metrics.json below ``output_root``."""
    corpus_root, output_root = Path(corpus_root), Path(output_root)
    target = output_root / "revision2"; target.mkdir(parents=True, exist_ok=True)
    songs = [song for song in songs if song.get("BaseName") and any(chart.get("Available") for chart in song.get("ChartInfos", []))]
    if len(songs) < 4:
        raise ValueError("at least four official songs are required for calibration")
    assert len(songs) >= 4
    bank = []; metrics = []; features_x = []; ratings = []; names = []; chart_count = 0
    for song_index, song in enumerate(songs, 1):
        if cancel and cancel(): raise RuntimeError("calibration cancelled")
        name = song["BaseName"]
        audio = np.load(corpus_root / "features" / f"{name}.npz"); times, features = audio["t"], audio["features"]
        for chart in song["ChartInfos"]:
            if not chart.get("Available"):
                continue
            data = json.loads((corpus_root / "charts" / f"{chart['Id']}.json").read_text(encoding="utf8")); notes = data["notes"]
            if not notes:
                continue
            for note in notes:
                note["flags"] = struct.unpack_from("<I", bytes.fromhex(note["extra"]), 36)[0]
            level = int(math.log2(chart["Difficulty"])); metric = chart_metrics(notes, float(times[-1]))
            metrics.append({"song": name, "chart": chart["Id"], "level": level, "rating": chart["Rating"], **metric})
            features_x.append(rating_features(metric)); ratings.append(chart["Rating"]); names.append(name); chart_count += 1
            width = min(6, max(2, 8 * 60 / data["bpm"]))
            for start in np.arange(0, float(times[-1]) - width * .5, width):
                finish = start + width; part = [note for note in notes if start * 1000 <= note["start"] < finish * 1000]
                attacks = [note for note in part if note["type"] in (1, 2, 4)]
                if len(attacks) < 2:
                    continue
                middle = set()
                for note in part:
                    if note["side"] == 1:
                        low, high = struct.unpack_from("<ii", bytes.fromhex(note["extra"]), 40); middle.update(range(low, high + 1))
                payload = []
                for note in part:
                    row = {key: copy.deepcopy(note[key]) for key in ("id", "group", "side", "type", "start", "end", "x0", "x1", "w0", "w1", "flags")}
                    row["start"] = (row["start"] / 1000 - start) / width; row["end"] = min(1, (row["end"] / 1000 - start) / width); payload.append(row)
                bank.append({"song": name, "chart": chart["Id"], "level": level, "start": float(start), "duration": width,
                             "descriptor": descriptor(times, features, start, finish), "notes": payload,
                             "middle_keys": len(middle), "strike_count": len(attacks), "density": len(attacks) / width})
        if progress: progress("phrases", song_index, len(songs))
    if chart_count < 16 or not bank:
        raise ValueError("calibration needs at least 16 playable official charts")
    assert chart_count >= 16
    x, y = np.asarray(features_x), np.asarray(ratings); heldout = np.array([name in set(heldout_songs) for name in names])
    if not heldout.any() or heldout.all():
        raise ValueError("held-out songs must leave training and validation rows")
    def model(): return HistGradientBoostingRegressor(max_iter=160, max_leaf_nodes=7, min_samples_leaf=8, l2_regularization=3, random_state=20260913)
    check = model().fit(x[~heldout], y[~heldout]); mae = float(mean_absolute_error(y[heldout], check.predict(x[heldout])))
    scaler = StandardScaler().fit(np.stack([phrase["descriptor"] for phrase in bank]))
    joblib.dump({"phrases": bank, "scaler": scaler}, target / "phrase-library.joblib", compress=3)
    (target / "stock-metrics.json").write_text(json.dumps(metrics, ensure_ascii=False), encoding="utf8")
    return {"songs": len(songs), "charts": chart_count, "phrases": len(bank), "rating_holdout_MAE": mae}
