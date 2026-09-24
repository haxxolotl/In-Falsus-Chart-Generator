"""Inventory recurring keyboard figures from an explicit stock corpus."""
from __future__ import annotations

import collections
import json
from pathlib import Path

import numpy as np

B = Path(__file__).resolve().parent


def windows(chart, beats):
    notes = [note for note in chart["notes"] if note["type"] in (1, 2)]
    if not notes: return []
    beat = 60000 / chart["bpm"]; origin = min(note["start"] for note in notes); width = beat * beats; result = []
    for start in np.arange(origin, max(note["start"] for note in notes), width):
        part = [note for note in notes if start - .1 <= note["start"] < start + width - .1]
        if len(part) < 4: continue
        rows = sorted((round((note["start"] - start) / beat * 8), note["type"], max(1, round(note["w0"][0] / note["w0"][1] * 4)), round(note["x0"][0] / note["x0"][1] * 4)) for note in part)
        result.append((float(start / 1000), tuple(row[:3] for row in rows), tuple(row[3] for row in rows)))
    return result


def study(corpus_root, songs, output_root, progress=None, cancel=None):
    corpus_root, output_root = Path(corpus_root), Path(output_root); target = output_root / "revision3"; target.mkdir(parents=True, exist_ok=True)
    inventory = []; examples = []; totals = collections.Counter()
    for song_index, song in enumerate(songs, 1):
        if cancel and cancel(): raise RuntimeError("calibration cancelled")
        for chart in song.get("ChartInfos", []):
            if not chart.get("Available"): continue
            path = corpus_root / "charts" / f"{chart['Id']}.json"
            if not path.exists(): continue
            data = json.loads(path.read_text(encoding="utf8")); counts = collections.Counter(); checked = 0
            for beats in (4, 8):
                seen = {}
                for start, signature, lanes in windows(data, beats):
                    checked += 1
                    if signature in seen:
                        old, prior = seen[signature]; kind = "other layout"; shift = 0
                        if lanes == prior: kind = "repeat"
                        elif lanes == tuple(6 - width - lane for (_, _, width), lane in zip(signature, prior)): kind = "mirror"
                        else:
                            shifts = {later - earlier for earlier, later in zip(prior, lanes) if 1 <= earlier <= 4 and 1 <= later <= 4}
                            if len(shifts) == 1 and next(iter(shifts)) and all((1 <= earlier <= 4 and 1 <= later <= 4) or earlier == later for earlier, later in zip(prior, lanes)):
                                kind = "lane shift"; shift = next(iter(shifts))
                        counts[kind] += 1; totals[kind] += 1
                        if kind in ("mirror", "lane shift") and sum(item["chart"] == chart["Id"] and item["kind"] == kind for item in examples) < 2:
                            examples.append({"song": song["BaseName"], "chart": chart["Id"], "beats": beats, "kind": kind, "shift": shift, "first": old, "repeat": start, "notes": len(lanes), "first_lanes": prior, "repeat_lanes": lanes})
                    seen[signature] = (start, lanes)
            inventory.append({"song": song["BaseName"], "chart": chart["Id"], "windows": checked, "patterns": dict(counts), "status": "reviewed"})
        if progress: progress("phrase structure", song_index, len(songs))
    if len(inventory) < 16: raise ValueError("calibration needs at least 16 official charts")
    assert len(inventory) >= 16 and len({row["song"] for row in inventory}) >= 4
    result = {"charts": len(inventory), "songs": len({row["song"] for row in inventory}), "method": "one/two-measure keyboard windows", "patterns": dict(totals), "examples": examples, "inventory": inventory}
    (target / "phrase-structure-study.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf8")
    return {"songs": result["songs"], "charts": result["charts"], "patterns": result["patterns"]}
