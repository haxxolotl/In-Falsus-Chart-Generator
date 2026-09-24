"""Bounded keyboard-target repair calibrated from decoded official FBD charts.

``audit(notes, level, profile=None)`` is read-only and reports deterministic
physical-key assignments plus machine-detectable conflicts.  ``apply`` edits
only conflicting keyboard targets in place, retaining survivor times and adding
FBD alternative-key widths where repetition requires them. ``build_profile``
accepts decoded chart dictionaries and ``load_profile`` reads a corpus root.
Neither function is a claim of human playability; they check the keyboard
constraints represented by the installed official corpus.
"""
from __future__ import annotations

import copy
import json
import re
from bisect import bisect_left
from functools import lru_cache
from collections import Counter, defaultdict
from pathlib import Path


# Snapshot from %LOCALAPPDATA%/InFalsusStudio/calibration/corpus on 2026-09-22:
# 70 FBD charts; the ten highest one-second target peaks supplied these limits.
DEFAULT_PROFILE = {
    "key_count": 6,
    "tap_recovery_ms": 67,
    "near_ms": 18,
    "density_window_ms": 1000,
    "max_density": 30,
    "density_limits": {250: 10, 500: 20, 1000: 30, 2000: 48},
    "key_window_ms": 500,
    "max_key_density": 4,
    "calibration": {
        "source": "decoded official FBD corpus",
        "charts": 70,
        "hardest": [
            "chaoticismlegacy3.json", "kiretsu3.json", "chronophobia3.json",
            "tactom3.json", "codeleviathan3.json", "chronomia3.json",
            "kuukaku3.json", "moonsliders3.json", "tokonagi3.json",
            "trajectoryofhope3.json",
        ],
        "method": "official peaks at 250/500/1000/2000 ms; narrow-only key recovery and 500 ms key load (wide spans are alternatives, not simultaneous presses)",
    },
}


def _profile(profile):
    value = copy.deepcopy(DEFAULT_PROFILE)
    if profile:
        value.update({key: item for key, item in profile.items() if key != "calibration"})
        if "calibration" in profile:
            value["calibration"] = copy.deepcopy(profile["calibration"])
    return value


def _value(rational):
    return float(rational[0]) / float(rational[1])


def _span(note, key_count):
    """Return the physical keys a keyboard target may accept."""
    if note.get("side") == 2:
        return (0,)
    if note.get("side") == 3:
        return (key_count - 1,)
    start = round(_value(note["x0"]) * 4)
    end = round((_value(note["x0"]) + _value(note["w0"])) * 4)
    end = max(start + 1, end)
    return tuple(range(max(0, start), min(key_count, end)))


def _events(notes, profile):
    result = []
    for index, note in enumerate(notes):
        if note.get("type") not in (1, 2) or note.get("side") not in (1, 2, 3):
            continue
        keys = _span(note, profile["key_count"])
        if keys:
            result.append({"index": index, "note": note, "time": note["start"],
                           "end": note["end"], "keys": keys, "held": note["type"] == 2})
    return sorted(result, key=lambda event: (event["time"], event["index"]))


def _preferred(event):
    center = (_value(event["note"]["x0"]) + _value(event["note"]["w0"]) / 2) * 4
    return min(event["keys"], key=lambda key: (abs(key - center), key))


def _assign(events, profile, expand=False):
    """Greedy temporal assignment; expand only during an authorized repair."""
    recovery = profile["tap_recovery_ms"]
    hold_until = [-1] * profile["key_count"]
    last_hit = [-10**12] * profile["key_count"]
    history = [[] for _ in hold_until]
    assignments, failures = [], []
    by_time = defaultdict(list)
    for event in events:
        by_time[event["time"]].append(event)
    for timestamp in sorted(by_time):
        used = set()
        # Retain sustained material before disposable tap layers.
        group = sorted(by_time[timestamp], key=lambda event: (
            not event["held"], -(event["end"] - event["time"]), event["index"]))
        for event in group:
            candidates = list(event["keys"])
            def available(key):
                recent = len(history[key]) - bisect_left(history[key], timestamp - profile['key_window_ms'] + 1)
                return (key not in used and hold_until[key] <= timestamp
                        and timestamp - last_hit[key] >= recovery and recent < profile['max_key_density'])
            allowed = [key for key in candidates if available(key)]
            if not allowed and expand:
                central = range(1, max(1, profile["key_count"] - 1))
                allowed = [key for key in central if available(key)]
            if not allowed:
                failures.append({"kind": "key_capacity", "time": timestamp,
                                 "index": event["index"], "keys": list(event["keys"])})
                continue
            preferred = _preferred(event)
            key = min(allowed, key=lambda item: (item != preferred if len(candidates)==1 else False,
                       last_hit[item], abs(item - preferred), item))
            assignments.append({"index": event["index"], "id": event["note"].get("id"),
                                "time": timestamp, "key": key,
                                "reassigned": key != preferred})
            used.add(key)
            last_hit[key] = timestamp
            history[key].append(timestamp)
            if event["held"]:
                hold_until[key] = max(hold_until[key], event["end"])
    return assignments, failures


def _limits(profile):
    result = {int(w):int(n) for w,n in profile['density_limits'].items()}
    result[profile['density_window_ms']] = profile['max_density']
    return result


def _density_violations(events, profile):
    ordered = sorted(events, key=lambda event: (event["time"], event["index"]))
    times = [event['time'] for event in ordered]
    violations = []
    for window,limit in _limits(profile).items():
        for start in sorted(set(times)):
            a,z = bisect_left(times,start),bisect_left(times,start+window)
            if z-a > limit:
                violations.append({"kind":"density","start":start,"end":start+window,
                    "count":z-a,"limit":limit,"indices":[e['index'] for e in ordered[a:z]]})
    return violations


def audit(notes, level, profile=None):
    """Read-only keyboard assignment audit; success is not a human-playtest claim."""
    profile = _profile(profile)
    events = _events(notes, profile)
    violations, by_time = [], defaultdict(list)
    for event in events:
        by_time[event["time"]].append(event)
        if level < 3 and len(event["keys"]) > 1:
            violations.append({"kind": "wide_below_fbd", "time": event["time"],
                               "index": event["index"]})
    near = profile["near_ms"]
    for timestamp, group in by_time.items():
        spans = Counter(event["keys"] for event in group)
        for span, count in spans.items():
            if count > 1:
                duplicates = [event["index"] for event in group if event["keys"] == span]
                violations.append({"kind": "duplicate_stack", "time": timestamp,
                                   "indices": duplicates})
    for position, left in enumerate(events):
        if len(left["keys"]) == 1:
            continue
        for right in events[position + 1:]:
            if right["time"] - left["time"] > near:
                break
            if abs(right["time"] - left["time"]) <= near and set(left["keys"]) & set(right["keys"]):
                violations.append({"kind": "wide_collision", "time": left["time"],
                                   "other_time": right["time"], "indices": [left["index"], right["index"]]})
        # The narrow event can precede the wide event in an unsorted input list.
        for right in reversed(events[:position]):
            if left['time']-right['time'] > near:break
            if len(right['keys'])==1 and set(left['keys']) & set(right['keys']):
                violations.append({'kind':'wide_collision','time':left['time'],
                    'other_time':right['time'],'indices':[left['index'],right['index']]})
    assignments, failures = _assign(events, profile)
    violations.extend(failures)
    violations.extend(_density_violations(events, profile))
    return {"ok": not violations, "checked_targets": len(events), "violations": violations,
            "assignment": assignments, "profile": profile,
            "limitation": "Machine-detectable keyboard constraints only; human playability needs playtest."}


def _thin_density(events, profile):
    """Drop excess chord layers first, then only truly over-capacity onsets."""
    removed = []
    for window,limit in sorted(_limits(profile).items()):
        active=[];deleted=set();counts=Counter(e['time'] for e in events)
        for event in list(events):
            active=[e for e in active if e['time']>event['time']-window]
            active.append(event)
            if len(active)<=limit:continue
            target=min(active,key=lambda e:(e['held'], counts[e['time']]<=1,
                e['note'].get('_source_type')=='floor', -e['time'], -e['index']))
            active.remove(target);deleted.add(target['index']);counts[target['time']]-=1
            removed.append({'index':target['index'],'time':target['time'],
                'reason':'density_layer' if counts[target['time']] else 'over_capacity_onset'})
        events[:]=[e for e in events if e['index'] not in deleted]
    return removed


def _widen_repeated_keys(events, profile, level):
    """FBD repeat-key runs may accept either adjacent key; clean trills stay narrow."""
    if level!=3:return []
    by_key=defaultdict(list)
    for e in events:
        if not e['held'] and e['note']['side']==1 and len(e['keys'])==1:
            by_key[e['keys'][0]].append(e)
    widened=[]
    for key,items in by_key.items():
        times=[e['time'] for e in items]
        marked=set()
        for i,e in enumerate(items):
            z=bisect_left(times,e['time']+profile['key_window_ms'])
            if z-i>profile['max_key_density']:
                marked.update(range(i,z))
            if i and times[i]-times[i-1]<profile['tap_recovery_ms']:
                marked.update((i-1,i))
        for i in sorted(marked):
            e=items[i]
            for lo in (key,key-1):
                if lo<1 or lo+1>4:continue
                span={lo,lo+1};blocked=False
                for other in events:
                    if other is e or not span.intersection(other['keys']):continue
                    if other['held'] and other['time']<=e['time']<=other['end']:
                        blocked=True;break
                    if abs(other['time']-e['time'])<=profile['near_ms']:
                        blocked=True;break
                if not blocked:
                    e['note'].update(x0=[lo,4],x1=[lo,4],w0=[2,4],w1=[2,4])
                    e['keys']=(lo,lo+1);widened.append(e['index']);break
    return widened


def _narrow(note, key):
    note.update(side=1, x0=[key, 4], x1=[key, 4], w0=[1, 4], w1=[1, 4])


def apply(notes, level, profile=None):
    """Repair in place and return a receipt; unresolved audit findings remain explicit."""
    profile = _profile(profile)
    before = audit(notes, level, profile)
    if before["ok"]:
        return {"status": "unchanged", "changed_targets": 0, "removed_targets": 0,
                "audit": before, "timing_preserved": True}
    events = _events(notes, profile)
    by_time = defaultdict(list)
    for event in events:
        by_time[event["time"]].append(event)
    removed = []
    # Remove only literal duplicate/stacked spans first.  Adjacent keys survive.
    for timestamp, group in by_time.items():
        seen = set()
        for event in sorted(group, key=lambda item: item["index"]):
            if event["keys"] in seen:
                events.remove(event)
                removed.append({"index": event["index"], "time": timestamp, "reason": "duplicate_stack"})
            else:
                seen.add(event["keys"])
    removed.extend(_thin_density(events, profile))
    widened = _widen_repeated_keys(events,profile,level)
    assignments, failures = _assign(events, profile, expand=True)
    assigned = {item["index"]: item for item in assignments}
    # A key capacity failure is an impossible simultaneous/held layer.  Keep the
    # remaining motif and record the one target that had no physical key.
    for failure in failures:
        event = next(event for event in events if event["index"] == failure["index"])
        events.remove(event)
        removed.append({"index": event["index"], "time": event["time"], "reason": "key_capacity"})
    if failures:
        assignments, _ = _assign(events, profile, expand=True)
        assigned = {item["index"]: item for item in assignments}
    force_narrow = set()
    for violation in before["violations"]:
        if violation["kind"] == "wide_below_fbd":
            force_narrow.add(violation["index"])
        elif violation["kind"] == "wide_collision":
            force_narrow.update(violation["indices"])
    changed = 0
    for event in events:
        assignment = assigned[event["index"]]
        if assignment['key'] not in event['keys'] or event["index"] in force_narrow:
            _narrow(event["note"], assignment["key"])
            changed += 1
    remove_indices = {item["index"] for item in removed}
    if remove_indices:
        notes[:] = [note for index, note in enumerate(notes) if index not in remove_indices]
    after = audit(notes, level, profile)
    # A rerouted narrow note can enter a newly widened neighbour's visual span.
    # Collapse only that colliding span to its already-proven physical key.
    physical={id(e['note']):assigned[e['index']]['key'] for e in events}
    collapsed=set()
    for violation in after['violations']:
        if violation['kind']!='wide_collision':continue
        for index in violation['indices']:
            n=notes[index]
            if len(_span(n,profile['key_count']))>1:
                _narrow(n,physical[id(n)]);collapsed.add(id(n))
    if collapsed:after=audit(notes,level,profile)
    wide_count=sum(e['index'] in widened and len(_span(e['note'],profile['key_count']))>1 for e in events)
    return {"status": "repaired" if after["ok"] else "unresolved", "changed_targets": changed+len(widened),
            "widened_targets":wide_count,
            "removed_targets": len(removed), "removed": removed, "audit": after,
            "timing_preserved": True,
            "limitation": "Passing this check does not establish human playability."}


def _is_fbd(chart):
    if chart.get("level") is not None:
        return int(chart["level"]) == 3
    return bool(re.search(r"3(?:\.json)?$", str(chart.get("name", ""))))


def build_profile(decoded_rows):
    """Measure the hardest decoded official FBD rows without inventing a universal limit."""
    rows = [row for row in decoded_rows if _is_fbd(row)]
    if not rows:
        raise ValueError("no FBD decoded chart rows")
    base = _profile(None)
    measured = []
    for row in rows:
        events = _events(row.get("notes", []), base)
        times=[event['time'] for event in events]
        peak = max((bisect_left(times,t+1000)-i for i,t in enumerate(times)),default=0)
        measured.append((peak, str(row.get("name", "official-fbd")), events))
    hardest = sorted(measured, key=lambda item: (-item[0], item[1]))[:min(10, len(measured))]
    gaps = [];key_peaks=[]
    for _, _, events in hardest:
        for key in range(base["key_count"]):
            times = sorted({event["time"] for event in events if event['keys']==(key,)})
            gaps.extend(right - left for left, right in zip(times, times[1:]) if right > left)
            key_peaks.append(max((bisect_left(times,t+500)-i for i,t in enumerate(times)),default=0))
    if not gaps:
        raise ValueError("FBD rows contain no reusable keyboard targets")
    limits={w:max((max((bisect_left(ts,t+w)-i for i,t in enumerate(ts)),default=0)
        for _,_,es in measured for ts in [[e['time'] for e in es]]),default=0) for w in (250,500,1000,2000)}
    base.update(tap_recovery_ms=min(gaps), max_key_density=max(key_peaks),density_limits=limits,
                max_density=max(item[0] for item in hardest),
                calibration={"source": "decoded official FBD corpus", "charts": len(rows),
                             "hardest": [item[1] for item in hardest],
                             "method": DEFAULT_PROFILE['calibration']['method']})
    return base


@lru_cache(maxsize=2)
def load_profile(corpus_root):
    """Load ``charts/*3.json`` from a decoded official corpus directory."""
    charts = Path(corpus_root) / "charts"
    rows = []
    for path in sorted(charts.glob("*3.json")):
        row = json.loads(path.read_text(encoding="utf8"))
        row["name"] = path.name
        rows.append(row)
    return build_profile(rows)
