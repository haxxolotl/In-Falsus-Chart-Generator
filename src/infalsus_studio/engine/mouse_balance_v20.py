"""Rebalance source sky accents into mouse flicks on active cursor fields.

This is a read-only corpus audit by default.  ``transform``/``apply`` are the
only entry points that alter a note list, and they keep the action count and
source-event identity intact.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import sys
from collections import defaultdict
from fractions import Fraction
from functools import lru_cache
from pathlib import Path

B = Path(__file__).resolve().parent

from .native_curve_fit_v14 import edges
from .source_formats import parse_source

LEFT = 1024
RIGHT = 4096
FLICK_MASK = 0x1E00
FLICK_WIDTH = 13 / 50


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf8"))


def _rational(number: float):
    fraction = Fraction(float(number)).limit_denominator(65536)
    return [fraction.numerator, fraction.denominator]


def _number(value_: object):
    if isinstance(value_, (list, tuple)) and len(value_) >= 2:
        try:
            return float(Fraction(int(value_[0]), int(value_[1])))
        except (TypeError, ValueError, ZeroDivisionError):
            return None
    if isinstance(value_, (int, float)) and math.isfinite(float(value_)):
        return float(value_)
    return None


def _level(metadata: dict) -> int | None:
    raw = metadata.get("level", metadata.get("difficulty", metadata.get("tier")))
    if isinstance(raw, str):
        names = {"ULT": 2, "FBD": 3, "FUTURE": 2, "BEYOND": 3, "BYD": 3}
        if raw.upper() in names:
            return names[raw.upper()]
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _rating(metadata: dict) -> float | None:
    raw = metadata.get("rating", metadata.get("Rating", metadata.get("level_rating")))
    try:
        rating = float(raw)
    except (TypeError, ValueError):
        return None
    return rating if math.isfinite(rating) else None


def target_rate(level: int | str | None, rating: float | int | None) -> float:
    """Return target mouse actions per second of active field time."""
    try:
        level = _level({"level": level})
        rating = float(rating)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(rating):
        return 0.0
    if level == 2:
        return min(5.5, 2.5 + 0.5 * (rating - 8.0))
    if level == 3:
        return min(6.25, 3.5 + 0.65 * (rating - 10.0))
    return 0.0


def _mapping_value(mapping: object, keys: tuple[str, ...]):
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def _metadata_event(note: dict, metadata: dict):
    """Resolve a source event from attached metadata without changing it."""
    index = note.get("_source_event", note.get("source_event"))
    if not isinstance(index, int):
        return None
    collections = (
        metadata.get("source_notes"),
        metadata.get("source_events"),
        metadata.get("source_chart_notes"),
    )
    for events in collections:
        if isinstance(events, dict):
            event = events.get(index, events.get(str(index)))
            if isinstance(event, dict):
                return event
        elif isinstance(events, (list, tuple)) and 0 <= index < len(events):
            if isinstance(events[index], dict):
                return events[index]
    return None


def _source_path(metadata: dict) -> Path | None:
    raw = metadata.get("source_chart", metadata.get("source_path"))
    if not raw:
        return None
    path = Path(str(raw))
    candidates = [
        path,
        B / "revision7" / "sources" / "aff" / path,
        B / "revision18" / "sources" / "aff" / path,
        B.parent / path,
        B.parent / "work" / "revision7" / "sources" / "aff" / path,
    ]
    return next((candidate for candidate in candidates if candidate.is_file()), None)


@lru_cache(maxsize=256)
def _source_notes(path: str):
    try:
        parsed = parse_source(Path(path))
    except (OSError, ValueError, KeyError, IndexError):
        return ()
    return tuple(parsed.get("notes", ()))


def _source_event(note: dict, metadata: dict):
    event = _metadata_event(note, metadata)
    if event is not None:
        return event
    path = _source_path(metadata)
    index = note.get("_source_event", note.get("source_event"))
    if path is None or not isinstance(index, int):
        return None
    events = _source_notes(str(path))
    return events[index] if 0 <= index < len(events) else None


def _source_curve_x(event: dict, source_time: float):
    """Evaluate an AFF source event's x at a sky timestamp."""
    start = float(event.get("arc_start", event.get("start", source_time)))
    end = float(event.get("arc_end", event.get("end", source_time)))
    u = min(1.0, max(0.0, (source_time - start) / max(1e-9, end - start)))
    kind = str(event.get("easing", "s"))
    kind = kind[:2] if len(kind) == 4 else kind
    if kind == "si":
        u = math.sin(math.pi * u / 2.0)
    elif kind == "so":
        u = 1.0 - math.cos(math.pi * u / 2.0)
    elif kind == "b":
        u = 3.0 * u * u - 2.0 * u * u * u
    x0 = float(event.get("x0", 0.5))
    return x0 + (float(event.get("x1", x0)) - x0) * u


def _source_kind(note: dict, metadata: dict) -> str | None:
    keys = ("_source_type", "source_type", "source_kind", "kind")
    direct = _mapping_value(note, keys)
    if isinstance(direct, str):
        return direct.lower()
    for key in ("source", "source_metadata", "source_note", "metadata"):
        nested = note.get(key)
        kind = _mapping_value(nested, keys + ("type",))
        if isinstance(kind, str):
            return kind.lower()
    event = _source_event(note, metadata)
    kind = _mapping_value(event, ("_source_type", "source_type", "type", "kind"))
    return kind.lower() if isinstance(kind, str) else None


def _source_position(note: dict, metadata: dict):
    """Return an optional source x position or source x movement."""
    # Explicit source coordinates take precedence over the converted keyboard
    # coordinate that every native note carries in x0/x1.
    maps = []
    for key in ("source", "source_metadata", "source_note", "metadata"):
        if isinstance(note.get(key), dict):
            maps.append(note[key])
    event = _source_event(note, metadata)
    if event is not None:
        maps.append(event)
    for mapping in (note,):
        if any(key in mapping for key in ("_source_x0", "source_x0", "_source_x1", "source_x1", "_source_x", "source_x")):
            maps.append(mapping)
    for mapping in maps:
        pair0 = _mapping_value(mapping, ("_source_x0", "source_x0", "x0"))
        pair1 = _mapping_value(mapping, ("_source_x1", "source_x1", "x1"))
        point = _mapping_value(mapping, ("_source_x", "source_x", "x", "position"))
        x0, x1 = _number(pair0), _number(pair1)
        if x0 is not None:
            if x1 is None:
                x1 = x0
            # AFF arc-tap metadata is evaluated at the tap's source time.
            if mapping is event and "arc_start" in mapping and "arc_end" in mapping:
                try:
                    alignment = metadata.get("alignment", {})
                    offset = float(alignment.get("offset_seconds", 0.0)) * 1000.0
                    scale = float(alignment.get("time_scale", 1.0)) or 1.0
                    source_time = (float(note["start"]) - offset) / scale
                    x0 = _source_curve_x(mapping, source_time)
                    x1 = float(x0)
                except (KeyError, TypeError, ValueError, ZeroDivisionError):
                    pass
            return float(x0), float(x1)
        point = _number(point)
        if point is not None:
            return point, point
    # Native keyboard x0/x1 is not source metadata.  Without an attached
    # source coordinate, the caller deliberately uses the carrier center.
    return None


def _field_geometry(field: dict, timestamp: int):
    span = max(1, int(field["end"]) - int(field["start"]))
    u = min(1.0, max(0.0, (float(timestamp) - field["start"]) / span))
    left, right = edges(field, u)
    left, right = sorted((float(left), float(right)))
    left, right = max(0.0, left), min(1.0, right)
    if right < left:
        left, right = right, left
    return left, right


def _active_fields(notes: list[dict], timestamp: int):
    return [
        field for field in notes
        if field.get("type") == 5 and field.get("start", 1) <= timestamp <= field.get("end", 0)
    ]


def _field_seconds(notes: list[dict]) -> float:
    intervals = sorted(
        (float(field["start"]), float(field["end"]))
        for field in notes if field.get("type") == 5 and field.get("end", 0) > field.get("start", 0)
    )
    merged = []
    for start, end in intervals:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return sum(end - start for start, end in merged) / 1000.0


def _even_indices(size: int, wanted: int):
    if wanted <= 0 or size <= 0:
        return []
    if wanted >= size:
        return list(range(size))
    if wanted == 1:
        return [size // 2]
    indices = [int((i * (size - 1) + (wanted - 1) / 2) // (wanted - 1)) for i in range(wanted)]
    return list(dict.fromkeys(indices))


def _direction(source_x: tuple[float, float] | None, center: float, field: dict, timestamp: int) -> int:
    if source_x is not None:
        start, end = source_x
        if abs(end - start) > 1e-6:
            return LEFT if end < start else RIGHT
        if start < center - 1e-6:
            return LEFT
        if start > center + 1e-6:
            return RIGHT
    # A missing source position follows the carrier's local native motion.
    span = max(1, int(field["end"]) - int(field["start"]))
    u = min(1.0, max(0.0, (float(timestamp) - field["start"]) / span))
    before = edges(field, max(0.0, u - 1e-3))
    after = edges(field, min(1.0, u + 1e-3))
    if sum(after) < sum(before) - 1e-9:
        return LEFT
    return RIGHT


def _convert(note: dict, field: dict, source_x: tuple[float, float] | None):
    left, right = _field_geometry(field, int(note["start"]))
    field_width = max(0.0, right - left)
    width = min(FLICK_WIDTH, field_width) if field_width else 0.0
    center = (left + right) / 2.0
    wanted = center if source_x is None else (source_x[0] + source_x[1]) / 2.0
    if not math.isfinite(wanted):
        wanted = center
    wanted = min(right - width / 2.0, max(left + width / 2.0, wanted)) if field_width else center
    direction = _direction(source_x, center, field, int(note["start"]))
    note.update(
        type=4,
        side=4,
        x0=_rational(wanted),
        x1=_rational(wanted),
        w0=_rational(width),
        w1=_rational(width),
        flags=direction,
    )
    assert note["flags"] in (LEFT, RIGHT)
    fl, fr = wanted - width / 2.0, wanted + width / 2.0
    assert fl >= left - 1e-9 and fr <= right + 1e-9, (note, field)
    return direction, (left, right), (fl, fr)


def transform(notes: list[dict], metadata: dict):
    """Convert an evenly spaced subset of eligible sky notes in place.

    The return value is a receipt; call ``project`` when a non-mutating result
    is needed.  Every selected note retains its timestamp and ``_source_event``.
    """
    level = _level(metadata)
    rating = _rating(metadata)
    before = copy.deepcopy(notes)
    actions_before = sum(note.get("type") in (1, 2, 4) for note in notes)
    rate = target_rate(level, rating)
    fields_by_timestamp = {}
    candidates = defaultdict(list)
    for index, note in enumerate(notes):
        if note.get("type") != 1 or note.get("side", 1) not in (1, 2, 3):
            continue
        if _source_kind(note, metadata) != "sky":
            continue
        fields = _active_fields(notes, int(note["start"]))
        if not fields:
            continue
        fields_by_timestamp[int(note["start"])] = fields[0]
        candidates[int(note["start"])].append((index, note))
    unique = []
    for timestamp in sorted(candidates):
        # Prefer a note with resolvable source position, then stable native id.
        choices = candidates[timestamp]
        chosen = min(
            choices,
            key=lambda pair: (
                _source_position(pair[1], metadata) is None,
                pair[1].get("id", pair[0]),
                pair[0],
            ),
        )
        unique.append((timestamp, *chosen))
    active_seconds = _field_seconds(notes)
    existing_flick_times = {
        int(note['start']) for note in notes
        if note.get('type') == 4 and _active_fields(notes, int(note['start']))
    }
    target_total = max(0, int(round(rate * active_seconds))) if level in (2, 3) else 0
    target = min(len(unique), max(0, target_total - len(existing_flick_times)))
    selected = _even_indices(len(unique), target)
    converted = []
    for selection in selected:
        timestamp, index, note = unique[selection]
        field = fields_by_timestamp[timestamp]
        source_x = _source_position(note, metadata)
        direction, bounds, flick_bounds = _convert(note, field, source_x)
        converted.append(dict(
            index=index,
            id=note.get("id", index),
            timestamp=timestamp,
            source_event=note.get("_source_event"),
            direction=direction,
            field_bounds=list(bounds),
            flick_bounds=list(flick_bounds),
        ))
    actions_after = sum(note.get("type") in (1, 2, 4) for note in notes)
    assert actions_after == actions_before
    assert sum(note.get("type") == 4 for note in notes) == sum(note.get("type") == 4 for note in before) + len(converted)
    return dict(
        status="projected" if level in (2, 3) else "preserved",
        level=level,
        rating=rating,
        target_rate_per_active_second=rate,
        active_field_seconds=round(active_seconds, 6),
        existing_flick_timestamps=len(existing_flick_times),
        target_total_mouse_actions=target_total,
        target_actions=target,
        source_sky_candidates=len(unique),
        candidate_timestamps=len(unique),
        selected=len(converted),
        converted=converted,
        actions_before=actions_before,
        actions_after=actions_after,
        total_notes_before=len(before),
        total_notes_after=len(notes),
        action_count_unchanged=actions_before == actions_after,
        at_most_one_conversion_per_timestamp=len({item["timestamp"] for item in converted}) == len(converted),
    )


def apply(notes: list[dict], metadata: dict):
    """Compatibility alias for the repository's in-place stage convention."""
    return transform(notes, metadata)


def project(notes: list[dict], metadata: dict):
    """Return a transformed copy and receipt without changing ``notes``."""
    projected = copy.deepcopy(notes)
    receipt = transform(projected, metadata)
    return projected, receipt


def audit_corpus(revision_root: str | Path = B / "revision18"):
    """Dry-project every chart in revision18; never writes chart or receipt files."""
    root = Path(revision_root)
    rows = _read(root / "all-charts.json")
    inventory = []
    for row in rows:
        for metadata in row.get("charts", []):
            chart_path = root / "charts" / f'{metadata["name"]}.json'
            chart = _read(chart_path)
            original = copy.deepcopy(chart["notes"])
            projected, receipt = project(original, metadata)
            item = dict(receipt)
            item.update(
                title=row.get("title"),
                base_name=row.get("base_name"),
                name=metadata.get("name"),
                notes_before=len(original),
                notes_projected=len(projected),
                actions_before=receipt["actions_before"],
                actions_projected=receipt["actions_after"],
                flicks_before=sum(note.get("type") == 4 for note in original),
                flicks_projected=sum(note.get("type") == 4 for note in projected),
            )
            inventory.append(item)
    assert len(inventory) == 380, len(inventory)
    assert all(item["notes_before"] == item["notes_projected"] for item in inventory)
    assert all(item["actions_before"] == item["actions_projected"] for item in inventory)
    return dict(
        status="passed",
        mode="dry_audit",
        revision=root.name,
        songs=len(rows),
        charts=len(inventory),
        before=dict(
            notes=sum(item["notes_before"] for item in inventory),
            actions=sum(item["actions_before"] for item in inventory),
            flicks=sum(item["flicks_before"] for item in inventory),
        ),
        projected=dict(
            notes=sum(item["notes_projected"] for item in inventory),
            actions=sum(item["actions_projected"] for item in inventory),
            flicks=sum(item["flicks_projected"] for item in inventory),
            converted=sum(item["selected"] for item in inventory),
        ),
        inventory=inventory,
        files_written=0,
    )


def dry_audit(revision_root: str | Path = B / "revision18"):
    return audit_corpus(revision_root)




