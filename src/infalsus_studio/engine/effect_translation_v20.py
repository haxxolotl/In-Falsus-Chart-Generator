"""Translate AFF stop/teleport pulses without collapsing their scroll distance.

Arcaea charts commonly encode a pause-and-go pulse as a very large BPM for
one millisecond followed by an almost-zero BPM.  Revision 18 discarded the
large impulse but kept the stop, which compressed many judgments into almost
the same screen position.  This module time-expands each safe impulse at a
bounded native speed while preserving its integrated distance.
"""
import collections
import hashlib
import json
import math
import struct
from pathlib import Path

from .source_scroll_effects_v14 import source_events

B = Path(__file__).resolve().parent
ROOTS = (B / 'revision7/sources/aff', B / 'revision10/sources/aff', B)
RECORD = '<qiidff'


def source_path(meta):
    path = Path(meta.get('source_chart', ''))
    for root in ROOTS:
        candidate = root / path
        if candidate.is_file():
            return candidate


def _normalization(root, end_source):
    weights = collections.Counter()
    for i, event in enumerate(root):
        end = root[i + 1]['time'] if i + 1 < len(root) else end_source
        if 20 <= event['bpm'] <= 600:
            weights[event['bpm']] += max(0, min(end, end_source) - max(0, event['time']))
    return weights.most_common(1)[0][0] if weights else None


def planned(meta, duration_ms, impulse_cap=8.0):
    path = source_path(meta)
    if not path or path.suffix.lower() != '.aff' or meta['level'] < 2:
        return [], dict(status='no_high_tier_aff_effect_source')
    groups = source_events(path)
    alignment = meta.get('alignment', {})
    scale = alignment.get('time_scale', 1.)
    offset = alignment.get('offset_seconds', 0.) * 1000
    end_source = (duration_ms - offset) / scale
    root = sorted(groups[0]['timings'], key=lambda event: event['time'])
    base = _normalization(root, end_source)
    if base is None:
        return [], dict(status='no_stable_source_clock')

    raw = []
    for event in root:
        time = max(0, round(event['time'] * scale + offset))
        if time >= duration_ms - 500:
            continue
        raw.append((time, event['bpm'] / base, event['time'], event['bpm']))
    # Same-time controls have last-write semantics in the game.
    raw = [(time, *values) for time, values in sorted({r[0]: r[1:] for r in raw}.items())]

    points = []
    expanded = []
    withheld = []
    i = 0
    while i < len(raw):
        time, ratio, source_time, bpm = raw[i]
        if abs(ratio) > 16 and i + 2 < len(raw):
            next_time, next_ratio, *_ = raw[i + 1]
            following_time = raw[i + 2][0]
            span = next_time - time
            # A 21–30 ms authored flash needs a longer readable replacement.
            # Keep its signed distance while capping the visual speed at 4x.
            cap = min(impulse_cap, 4.0) if 20 < span <= 30 else impulse_cap
            pulse_ms = abs(ratio) * span / cap if cap else math.inf
            # Only absorb a genuine one-shot impulse followed by a stop.  Its
            # replacement fits before the next source event and has the exact
            # same signed integral as the original impulse.
            if 0 < span <= 30 and abs(next_ratio) <= .02 and 2 <= pulse_ms <= following_time - time - 2:
                end = round(time + pulse_ms)
                value = math.copysign(cap, ratio)
                points.extend(((time, value), (end, 0.0)))
                expanded.append(dict(start=time, source_span_ms=span, source_ratio=ratio,
                                     replacement_speed=value, replacement_end=end,
                                     signed_distance_seconds=ratio * span / 1000))
                i += 2
                continue
        if abs(ratio) > 16:
            points.append((time, 1.0))
            withheld.append(dict(start=time, source_time=source_time, source_bpm=bpm,
                                 ratio=ratio, reason='impulse_does_not_fit_before_next_source_control'))
        else:
            points.append((time, 0.0 if abs(ratio) <= .02 else ratio))
        i += 1

    points.append((int(duration_ms) - 1, 1.0))
    merged = []
    for time, value in sorted({int(t): float(v) for t, v in points}.items()):
        if merged and abs(merged[-1][1] - value) < 1e-9:
            continue
        merged.append((time, value))
    expressive = any(abs(value - 1) > 1e-9 for _, value in merged)
    return (merged if expressive else []), dict(
        status='translated' if expressive else 'steady_source',
        source=str(path), source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        normalization_bpm=base, impulse_cap=impulse_cap,
        expanded_impulses=expanded, withheld_impulses=withheld,
        local_timing_groups_omitted=max(0, len(groups) - 1), events=merged if expressive else [])


def apply(chart, meta, duration_ms, impulse_cap=8.0):
    events, report = planned(meta, duration_ms, impulse_cap)
    if not events:
        return report
    old = [list(row) for row in struct.iter_unpack(RECORD, bytes.fromhex(chart['timing_data_hex']))]
    musical = [row[1:] for row in old if row[2] != 0]
    out = [row for row in old if row[2] != 0]
    out.extend([0, time, 0, value, chart.get('beats', 4), 0.] for time, value in events)
    out.sort(key=lambda row: (row[1], row[2] != 0))
    for ident, row in enumerate(out):
        row[0] = ident
    assert [row[1:] for row in out if row[2] != 0] == musical
    chart['timing_data_hex'] = b''.join(struct.pack(RECORD, *row) for row in out).hex()
    report.update(musical_clock_unchanged=True, note_timestamps_unchanged=True)
    return report


def integrated_distance(events, start, end):
    """Signed native scroll distance between two chart times."""
    speed = 1.0
    cursor = start
    total = 0.0
    for time, value in events:
        if time <= start:
            speed = value
            continue
        if time >= end:
            break
        total += (time - cursor) / 1000 * speed
        cursor = time
        speed = value
    return total + (end - cursor) / 1000 * speed


