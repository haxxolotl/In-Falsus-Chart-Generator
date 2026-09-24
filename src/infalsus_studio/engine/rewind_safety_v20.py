"""Audit and conservatively repair unsafe global scroll reversals.

The chart files keep timing/control records as ``<qiidff>``.  Kind ``0`` is
the global scroll multiplier; kinds ``1`` and ``2`` are musical-clock
controls and are deliberately left alone here.  This module operates on
already parsed chart dictionaries and never writes a chart during an audit.
"""

from __future__ import annotations

import argparse
import bisect
import copy
import json
import math
import struct
from pathlib import Path
from typing import Any, Iterable, Sequence


TIMING_RECORD = struct.Struct("<qiidff")
PLAYABLE_TYPES = frozenset((1, 2, 4, 5))
SAFE_EPSILON = 1e-9
REPAIR_SPEED = 0.12


def decode_timing_data(value: str | bytes | bytearray) -> list[list[int | float]]:
    """Decode timing_data_hex into mutable six-field records."""

    raw = bytes.fromhex(value) if isinstance(value, str) else bytes(value)
    if len(raw) % TIMING_RECORD.size:
        raise ValueError("timing data is not a whole number of <qiidff> records")
    return [list(record) for record in TIMING_RECORD.iter_unpack(raw)]


def encode_timing_data(records: Iterable[Sequence[int | float]]) -> str:
    """Encode records without changing their order or event times."""

    return b"".join(TIMING_RECORD.pack(*record) for record in records).hex()


def playable_boundaries(chart: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the judgment boundaries that can visibly reach the line.

    Tap notes use their start.  Holds, flicks, and field segments use both
    endpoints when they differ.  A same-time endpoint is represented once.
    """

    result: list[dict[str, Any]] = []
    fields: dict[int, list[tuple[int, dict[str, Any]]]] = {}
    for note_index, note in enumerate(chart.get("notes", ())):
        note_type = int(note.get("type", -1))
        if note_type not in PLAYABLE_TYPES:
            continue
        if note_type == 5:
            fields.setdefault(int(note.get("group", note_index)), []).append((note_index, note))
            continue
        start = note.get("start")
        end = note.get("end", start)
        values = [("start", start)]
        if note_type in (2, 4, 5) and end != start:
            values.append(("end", end))
        for role, timestamp in values:
            if timestamp is None:
                continue
            result.append(
                {
                    "time": float(timestamp),
                    "note_index": note_index,
                    "note_id": note.get("id", note_index),
                    "type": note_type,
                    "role": role,
                }
            )
    # Consecutive type-5 records are one continuous tracking zone.  Their
    # internal spline seams are not new judgments and must not cancel a safe
    # authored freeze; only the group's entrance and exit are playable events.
    for group, members in fields.items():
        members.sort(key=lambda item: (item[1]['start'], item[1]['end'], item[0]))
        first_index, first = members[0]
        last_index, last = max(members, key=lambda item: (item[1]['end'], item[1]['start']))
        for role, timestamp, note_index, note in (
            ('start', first['start'], first_index, first),
            ('end', last['end'], last_index, last),
        ):
            result.append({'time': float(timestamp), 'note_index': note_index,
                           'note_id': note.get('id', note_index), 'type': 5,
                           'role': role, 'group': group})
    result.sort(key=lambda item: (item["time"], item["note_index"], item["role"]))
    return result


def _kind0_events(records: Sequence[Sequence[int | float]]) -> list[dict[str, Any]]:
    """Collapse same-time kind-0 controls to the last effective value."""

    grouped: dict[float, dict[str, Any]] = {}
    for index, record in enumerate(records):
        if int(record[2]) != 0:
            continue
        time = float(record[1])
        event = grouped.setdefault(time, {"time": time, "value": float(record[3]), "indexes": []})
        event["value"] = float(record[3])
        event["indexes"].append(index)
    return [grouped[time] for time in sorted(grouped)]


def _contains_boundary(segment: dict[str, Any], boundaries: Sequence[dict[str, Any]]) -> bool:
    start, end = segment["start"], segment["end"]
    return any(start <= boundary["time"] < end for boundary in boundaries)


def _segments(
    records: Sequence[Sequence[int | float]],
    boundaries: Sequence[dict[str, Any]],
    speed_overrides: dict[float, float] | None = None,
) -> list[dict[str, Any]]:
    """Build [start,end) scroll intervals through the last playable time."""

    events = _kind0_events(records)
    boundary_times = [float(item["time"]) for item in boundaries]
    if not events and not boundary_times:
        return []
    origin = min([0.0, *boundary_times, *(event["time"] for event in events)])
    limit = max(boundary_times, default=origin)
    if limit <= origin:
        return []

    values = speed_overrides or {}
    result: list[dict[str, Any]] = []
    current = origin
    speed = 1.0
    for event in events:
        event_time = event["time"]
        if event_time < origin:
            speed = values.get(event_time, event["value"])
            continue
        if event_time > limit:
            break
        if event_time > current:
            result.append(
                {
                    "start": current,
                    "end": event_time,
                    "speed": float(speed),
                    "event_time": current,
                }
            )
        speed = float(values.get(event_time, event["value"]))
        current = event_time
    if current < limit:
        result.append({"start": current, "end": limit, "speed": float(speed), "event_time": current})
    return result


def _profile(segments: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Build prefix-integral lookup arrays for a piecewise constant speed."""

    starts: list[float] = []
    prefix: list[float] = []
    speeds: list[float] = []
    running: list[float] = []
    max_index: list[int] = []
    position = 0.0
    current_max = -math.inf
    current_index = -1
    for index, segment in enumerate(segments):
        starts.append(float(segment["start"]))
        prefix.append(position)
        speeds.append(float(segment["speed"]))
        if position >= current_max - SAFE_EPSILON:
            current_max = position
            current_index = index
        running.append(current_max)
        max_index.append(current_index)
        position += float(segment["speed"]) * (float(segment["end"]) - float(segment["start"]))
    return {
        "starts": starts,
        "prefix": prefix,
        "speeds": speeds,
        "running_max": running,
        "max_index": max_index,
        "final_position": position,
    }


def _prefix_at(profile: dict[str, Any], segments: Sequence[dict[str, Any]], timestamp: float) -> float:
    starts = profile["starts"]
    if not starts:
        return 0.0
    index = bisect.bisect_right(starts, timestamp) - 1
    if index < 0:
        return 0.0
    segment = segments[index]
    return profile["prefix"][index] + profile["speeds"][index] * (timestamp - segment["start"])


def _ambiguous_boundaries(
    boundaries: Sequence[dict[str, Any]], segments: Sequence[dict[str, Any]], profile: dict[str, Any]
) -> list[dict[str, Any]]:
    """Find boundaries whose integrated path already reached the line."""

    if not segments:
        return []
    starts = profile["starts"]
    running_max = profile["running_max"]
    max_index = profile["max_index"]
    result: list[dict[str, Any]] = []
    for boundary in boundaries:
        timestamp = float(boundary["time"])
        index = bisect.bisect_left(starts, timestamp)
        if index <= 0:
            continue
        position = _prefix_at(profile, segments, timestamp)
        prior_max = running_max[index - 1]
        if position <= prior_max + SAFE_EPSILON:
            high_index = max_index[index - 1]
            result.append(
                dict(
                    boundary,
                    position=position,
                    prior_max=prior_max,
                    high_time=starts[high_index],
                    high_index=high_index,
                )
            )
    return result


def _counts(
    records: Sequence[Sequence[int | float]],
    boundaries: Sequence[dict[str, Any]],
    segments: Sequence[dict[str, Any]],
) -> dict[str, int]:
    kind0 = [record for record in records if int(record[2]) == 0]
    profile = _profile(segments)
    return {
        "kind0_controls": len(kind0),
        "negative_controls": sum(float(record[3]) < 0 for record in kind0),
        "zero_controls": sum(float(record[3]) == 0 for record in kind0),
        "negative_intervals": sum(segment["speed"] < 0 for segment in segments),
        "zero_intervals": sum(segment["speed"] == 0 for segment in segments),
        "playable_boundaries": len(boundaries),
        "ambiguous_pre_crossings": len(_ambiguous_boundaries(boundaries, segments, profile)),
    }


def _repair_records(
    records: Sequence[Sequence[int | float]], boundaries: Sequence[dict[str, Any]]
) -> tuple[list[list[int | float]], dict[str, Any]]:
    """Return repaired records and the internal decisions used to audit them."""

    repaired = [list(record) for record in records]
    events = _kind0_events(repaired)
    speeds = {event["time"]: event["value"] for event in events}
    changed_times: dict[float, dict[str, Any]] = {}

    # An actual zero-speed freeze at a playable boundary is always ambiguous.
    # Promote it before deciding which reversals are safe to retain.
    for segment in _segments(repaired, boundaries, speeds):
        if segment["speed"] == 0 and _contains_boundary(segment, boundaries):
            event_time = segment["event_time"]
            speeds[event_time] = REPAIR_SPEED
            changed_times[event_time] = {"from": 0.0, "to": REPAIR_SPEED, "reason": "zero_boundary"}

    # Repeatedly remove only the negative/flat drawdown that explains an
    # early crossing.  A reversal that recovers before the next playable
    # boundary remains untouched.
    for _ in range(max(1, len(events) + 1)):
        segments = _segments(repaired, boundaries, speeds)
        profile = _profile(segments)
        ambiguous = _ambiguous_boundaries(boundaries, segments, profile)
        if not ambiguous:
            break
        changed = False
        for crossing in ambiguous:
            high_time = crossing["high_time"]
            timestamp = float(crossing["time"])
            for segment in segments:
                if segment["start"] >= timestamp or segment["end"] <= high_time:
                    continue
                speed = float(segment["speed"])
                if speed > 0:
                    continue
                event_time = segment["event_time"]
                has_boundary = _contains_boundary(segment, boundaries)
                # A flat drawdown is an unsafe freeze even when the next
                # boundary is exactly at its end: the note would still have
                # reached the same visual distance earlier.  Keep the
                # positive floor small, but make the integral strictly move.
                target = REPAIR_SPEED if speed == 0 or has_boundary else 0.0
                if speed == target:
                    continue
                prior = changed_times.get(event_time, {}).get("from", speed)
                speeds[event_time] = target
                changed_times[event_time] = {
                    "from": prior,
                    "to": target,
                    "reason": (
                        "negative_boundary"
                        if has_boundary and speed < 0
                        else "unsafe_freeze"
                        if speed == 0
                        else "unsafe_drawdown"
                    ),
                }
                changed = True
        if not changed:
            # This is only reachable for a malformed/non-finite input.  Do
            # not silently claim safety when the integrated proof cannot move.
            raise AssertionError("unable to remove an ambiguous pre-crossing")
    else:
        raise AssertionError("scroll safety repair did not converge")

    for event in events:
        target = speeds[event["time"]]
        if target == event["value"]:
            continue
        for index in event["indexes"]:
            repaired[index][3] = target

    final_segments = _segments(repaired, boundaries)
    final_ambiguous = _ambiguous_boundaries(boundaries, final_segments, _profile(final_segments))
    assert not final_ambiguous, "repaired chart still has ambiguous pre-crossings"
    return repaired, {
        "changed_times": changed_times,
        "segments_before": _segments(records, boundaries),
        "segments_after": final_segments,
    }


def repair_chart(chart: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Copy and repair one parsed chart, preserving note and clock records."""

    original = decode_timing_data(chart.get("timing_data_hex", ""))
    boundaries = playable_boundaries(chart)
    before_segments = _segments(original, boundaries)
    before = _counts(original, boundaries, before_segments)
    repaired_records, decisions = _repair_records(original, boundaries)
    repaired = copy.deepcopy(chart)
    repaired["timing_data_hex"] = encode_timing_data(repaired_records)
    after_segments = decisions["segments_after"]
    after = _counts(repaired_records, boundaries, after_segments)
    changed_controls = sum(
        old[3] != new[3] for old, new in zip(original, repaired_records) if int(old[2]) == 0
    )
    musical_before = [record for record in original if int(record[2]) in (1, 2)]
    musical_after = [record for record in repaired_records if int(record[2]) in (1, 2)]
    assert musical_before == musical_after
    assert [note.get("start") for note in chart.get("notes", ())] == [
        note.get("start") for note in repaired.get("notes", ())
    ]
    assert [note.get("end") for note in chart.get("notes", ())] == [
        note.get("end") for note in repaired.get("notes", ())
    ]
    report = {
        "before": before,
        "after": after,
        "changed_controls": changed_controls,
        "changed_times": decisions["changed_times"],
        "safe_negative_intervals_preserved": sum(
            old["speed"] < 0 and old["event_time"] not in decisions["changed_times"]
            for old in decisions["segments_before"]
        ),
        "event_times_preserved": [old[1] for old in original] == [new[1] for new in repaired_records],
        "musical_controls_preserved": musical_before == musical_after,
        "note_times_preserved": [
            (note.get("start"), note.get("end")) for note in chart.get("notes", ())
        ]
        == [(note.get("start"), note.get("end")) for note in repaired.get("notes", ())],
    }
    assert report["after"]["ambiguous_pre_crossings"] == 0
    return repaired, report


def audit_chart(chart: dict[str, Any]) -> dict[str, Any]:
    """Return the repair receipt for one parsed chart without mutating it."""

    _, report = repair_chart(chart)
    return report


def audit_corpus(
    root: str | Path,
    *,
    expected_count: int | None = None,
    report_path: str | Path | None = None,
) -> dict[str, Any]:
    """Audit every chart JSON below ``root`` and optionally write only a report."""

    root = Path(root)
    chart_dir = root if root.name.lower() == "charts" else root / "charts"
    paths = sorted(chart_dir.glob("*.json"))
    if expected_count is not None:
        assert len(paths) == expected_count, f"expected {expected_count} charts, found {len(paths)}"
    per_chart: list[dict[str, Any]] = []
    for path in paths:
        chart = json.loads(path.read_text(encoding="utf-8-sig"))
        repaired, receipt = repair_chart(chart)
        assert repaired is not chart
        assert receipt["after"]["ambiguous_pre_crossings"] == 0
        per_chart.append(
            {
                "name": path.name,
                "before": receipt["before"],
                "after": receipt["after"],
                "changed_controls": receipt["changed_controls"],
                "changed_times": receipt["changed_times"],
                "event_times_preserved": receipt["event_times_preserved"],
                "musical_controls_preserved": receipt["musical_controls_preserved"],
                "note_times_preserved": receipt["note_times_preserved"],
            }
        )
    before_ambiguous = sum(item["before"]["ambiguous_pre_crossings"] for item in per_chart)
    after_ambiguous = sum(item["after"]["ambiguous_pre_crossings"] for item in per_chart)
    assert after_ambiguous == 0
    report = {
        "schema": "rewind-safety-v20",
        "status": "pass",
        "chart_directory": str(chart_dir),
        "charts": len(per_chart),
        "before_ambiguous_pre_crossings": before_ambiguous,
        "after_ambiguous_pre_crossings": after_ambiguous,
        "changed_charts": sum(bool(item["changed_controls"]) for item in per_chart),
        "changed_controls": sum(item["changed_controls"] for item in per_chart),
        "per_chart": per_chart,
    }
    if report_path is not None:
        destination = Path(report_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _synthetic_chart(records: Sequence[Sequence[int | float]], notes: Sequence[dict[str, Any]]) -> dict[str, Any]:
    return {"notes": copy.deepcopy(list(notes)), "timing_data_hex": encode_timing_data(records)}


def self_test() -> None:
    """Small assert-only checks for reversal, unsafe rewind, and freeze rules."""

    safe = _synthetic_chart(
        [[0, 0, 0, 1.0, 4.0, 0.0], [1, 100, 0, -1.0, 4.0, 0.0], [2, 200, 0, 2.0, 4.0, 0.0], [3, 250, 1, 120.0, 4.0, 0.0]],
        [{"id": 1, "type": 1, "start": 300, "end": 300}],
    )
    repaired, receipt = repair_chart(safe)
    assert receipt["changed_controls"] == 0
    assert decode_timing_data(repaired["timing_data_hex"])[1][3] == -1.0

    unsafe = _synthetic_chart(
        [[0, 0, 0, 1.0, 4.0, 0.0], [1, 100, 0, -2.0, 4.0, 0.0], [2, 200, 0, 1.0, 4.0, 0.0], [3, 250, 1, 120.0, 4.0, 0.0]],
        [{"id": 1, "type": 1, "start": 210, "end": 210}],
    )
    repaired, receipt = repair_chart(unsafe)
    assert receipt["changed_controls"] == 1
    assert decode_timing_data(repaired["timing_data_hex"])[1][3] == 0.0

    boundary = _synthetic_chart(
        [[0, 0, 0, 1.0, 4.0, 0.0], [1, 100, 0, -1.0, 4.0, 0.0], [2, 200, 0, 1.0, 4.0, 0.0], [3, 250, 1, 120.0, 4.0, 0.0]],
        [{"id": 1, "type": 2, "start": 120, "end": 150}],
    )
    repaired, receipt = repair_chart(boundary)
    assert receipt["changed_controls"] == 1
    assert decode_timing_data(repaired["timing_data_hex"])[1][3] == REPAIR_SPEED

    freeze = _synthetic_chart(
        [[0, 0, 0, 1.0, 4.0, 0.0], [1, 100, 0, 0.0, 4.0, 0.0], [2, 200, 0, 1.0, 4.0, 0.0], [3, 250, 1, 120.0, 4.0, 0.0]],
        [{"id": 1, "type": 1, "start": 150, "end": 150}],
    )
    repaired, receipt = repair_chart(freeze)
    assert receipt["changed_controls"] == 1
    assert decode_timing_data(repaired["timing_data_hex"])[1][3] == REPAIR_SPEED




