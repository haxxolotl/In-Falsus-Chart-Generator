"""Learn audio-conditioned arrangement policy from an explicit corpus."""
from __future__ import annotations

import copy
import json
import math
import struct
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.metrics import mean_absolute_error
from sklearn.preprocessing import StandardScaler

from .arrangement_metrics import extended
from .learn_phrase_library import descriptor, union_duration

B = Path(__file__).resolve().parent
R = B / "revision4"


def train(corpus_root, songs, heldout_songs, output_root, progress=None, cancel=None):
    corpus_root, output_root = Path(corpus_root), Path(output_root); target = output_root / "revision4"; target.mkdir(parents=True, exist_ok=True)
    rows = []; scenes = []; training = []; song_count = 0
    for song in songs:
        if cancel and cancel(): raise RuntimeError("calibration cancelled")
        base = song.get("BaseName")
        if not base: continue
        feature_path = corpus_root / "features" / f"{base}.npz"
        if not feature_path.exists(): continue
        audio = np.load(feature_path); times, features, duration = audio["t"], audio["features"], float(audio["t"][-1])
        for chart in song.get("ChartInfos", []):
            if not chart.get("Available"): continue
            path = corpus_root / "charts" / f"{chart['Id']}.json"
            if not path.exists(): continue
            data = json.loads(path.read_text(encoding="utf8")); notes = data["notes"]
            if not notes: continue
            level = int(math.log2(chart["Difficulty"])); rows.append({"song": base, "chart": chart["Id"], "level": level, "rating": chart["Rating"], **extended(notes, duration)})
            size = 8 * 60 / data["bpm"]
            for start in np.arange(0, duration - size * .8, size):
                finish = min(duration, start + size); part = [note for note in notes if start * 1000 <= note["start"] < finish * 1000]
                attacks = [note for note in part if note["type"] in (1, 2, 4)]
                fields = [(max(start, note["start"] / 1000), min(finish, note["end"] / 1000)) for note in notes if note["type"] == 5 and note["start"] / 1000 < finish and note["end"] / 1000 > start]
                holds = [note for note in notes if note["type"] == 2 and note["side"] in (2, 3) and note["start"] / 1000 < finish and note["end"] / 1000 > start]
                side = sum(min(finish, note["end"] / 1000) - max(start, note["start"] / 1000) for note in holds) / (finish - start)
                moments = sorted({note["start"] / 1000 for note in attacks}); strikes = sorted(note["start"] / 1000 for note in attacks)
                peak = lambda values: max((np.searchsorted(values, time + 1) - index for index, time in enumerate(values)), default=0)
                training.append({"song": base, "level": level, "x": descriptor(times, features, start, finish), "y": [len(moments) / (finish - start), len(attacks) / (finish - start), union_duration(fields) / (finish - start), side, peak(moments), peak(strikes)]})
            relevant = sorted([note for note in notes if note["type"] == 5 or (note["type"] == 2 and note["side"] in (2, 3) and note["end"] - note["start"] >= 500)], key=lambda note: note["start"])
            group = []; finish = -1
            for note in relevant + [None]:
                if note is None or (group and (note["start"] > finish + 150 or note["end"] - group[0]["start"] > 12000)):
                    if group:
                        start = min(item["start"] for item in group) / 1000; end = max(item["end"] for item in group) / 1000
                        if 1.5 <= end - start <= 12:
                            payload = []
                            for item in group:
                                row = {key: copy.deepcopy(item[key]) for key in ("group", "side", "type", "start", "end", "x0", "x1", "w0", "w1")}; row["flags"] = struct.unpack_from("<I", bytes.fromhex(item["extra"]), 36)[0]; payload.append(row)
                            pair = max((min(left["end"], right["end"]) - max(left["start"], right["start"]) for left in group if left["type"] == 2 and left["side"] == 2 for right in group if right["type"] == 2 and right["side"] == 3), default=0) / 1000
                            coverage = union_duration([(item["start"] / 1000, item["end"] / 1000) for item in group if item["type"] == 5]) / (end - start)
                            scenes.append({"song": base, "chart": chart["Id"], "level": level, "start": start, "end": end, "descriptor": descriptor(times, features, start, end), "notes": payload, "paired_sides": pair >= .5, "paired_seconds": pair, "field_coverage": coverage})
                    group = []; finish = -1
                if note is not None: group.append(note); finish = max(finish, note["end"])
        song_count += 1
        if progress: progress("arrangements", song_count, len(songs))
    if song_count < 4 or len(rows) < 16 or not scenes: raise ValueError("calibration needs at least four songs, 16 charts, and sustain scenes")
    assert song_count >= 4 and len(rows) >= 16
    heldout_songs = set(heldout_songs); models = {}; checks = {}
    for level in range(4):
        level_rows = [row for row in training if row["level"] == level]
        x, y = np.stack([row["x"] for row in level_rows]), np.array([row["y"] for row in level_rows]); test = np.array([row["song"] in heldout_songs for row in level_rows])
        if not test.any() or test.all(): raise ValueError(f"level {level} has no valid held-out split")
        def model(): return ExtraTreesRegressor(n_estimators=64, max_depth=12, min_samples_leaf=10, n_jobs=4, random_state=20260914)
        checked = model().fit(x[~test], y[~test]); checks[level] = mean_absolute_error(y[test], checked.predict(x[test]), multioutput="raw_values").tolist(); models[level] = model().fit(x, y)
    scaler = StandardScaler().fit(np.stack([scene["descriptor"] for scene in scenes]))
    joblib.dump({"models": models, "scenes": scenes, "scene_scaler": scaler, "stock": rows}, target / "arrangement-model.joblib", compress=3)
    return {"songs": song_count, "charts": len(rows), "complete_scenes": len(scenes), "density_model_holdout": checks}
