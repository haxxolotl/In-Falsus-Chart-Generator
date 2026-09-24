"""Local timing cleanup for readable special-scroll passages.

This never fits a global offset.  It only merges sub-frame attacks that were
meant to be simultaneous and raises positive slow-scroll speed just enough to
keep adjacent judgments visually separable.
"""
import struct

from .effect_translation_v20 import integrated_distance

RECORD = '<qiidff'
REVIEW_FLOOR = (0.0, 0.0, 0.020, 0.015)


def snap_micro_attacks(notes, tolerance_ms=3):
    attacks = sorted({n['start'] for n in notes if n['type'] in (1, 2, 4)})
    mapping = {}
    anchor = None
    for time in attacks:
        if anchor is not None and time - anchor <= tolerance_ms:
            mapping[time] = anchor
        else:
            anchor = time
    changed = {}
    for source, target in mapping.items():
        affected = [n for n in notes if n['start'] == source or n['end'] == source]
        if any(n['start'] < source == n['end'] and source - n['start'] <= tolerance_ms for n in affected):
            continue
        for note in affected:
            if note['start'] == source:
                note['start'] = target
            if note['end'] == source:
                note['end'] = target
        if all(n['start'] <= n['end'] and (n['type'] not in (2, 5) or n['start'] < n['end']) for n in affected):
            changed[source] = target
        else:
            for note in affected:
                if note['start'] == target:
                    note['start'] = source
                if note['end'] == target:
                    note['end'] = source
    return dict(changed_attack_times=len(changed), maximum_shift_ms=max((a-b for a,b in changed.items()), default=0),
                local_only=True, global_offset_fitted=False, mapping=changed)


def _state(events, time):
    value = 1.0
    for at, candidate in events:
        if at > time:
            break
        value = candidate
    return value


def enforce_scroll_separation(chart, level):
    threshold = REVIEW_FLOOR[level]
    if not threshold:
        return dict(patched_intervals=0, threshold=threshold)
    rows = [list(row) for row in struct.iter_unpack(RECORD, bytes.fromhex(chart['timing_data_hex']))]
    events = sorted((int(row[1]), float(row[3])) for row in rows if row[2] == 0)
    attacks = sorted({n['start'] for n in chart['notes'] if n['type'] in (1, 2, 4)})
    patches = []
    for start, end in zip(attacks, attacks[1:]):
        span = end - start
        if span < 8 or span > 600:
            continue
        controls = [(time, value) for time, value in events if start < time < end]
        states = [_state(events, start), *(value for _, value in controls)]
        if any(value < 0 for value in states):
            continue
        distance = abs(integrated_distance(events, start, end))
        if distance + 1e-9 >= threshold:
            continue
        patches.append((start, end, threshold / (span / 1000)))
    if not patches:
        return dict(patched_intervals=0, threshold=threshold)

    times = sorted({time for time, _ in events} | {p[i] for p in patches for i in (0, 1)})
    rebuilt = []
    for time in times:
        base = _state(events, time)
        floor = max((speed for start, end, speed in patches if start <= time < end), default=0.0)
        value = max(base, floor) if base >= 0 else base
        if not rebuilt or abs(rebuilt[-1][1] - value) > 1e-9:
            rebuilt.append((time, value))

    musical = [row[1:] for row in rows if row[2] != 0]
    out = [row for row in rows if row[2] != 0]
    out.extend([0, time, 0, value, chart.get('beats', 4), 0.0] for time, value in rebuilt)
    out.sort(key=lambda row: (row[1], row[2] != 0))
    for ident, row in enumerate(out):
        row[0] = ident
    assert [row[1:] for row in out if row[2] != 0] == musical
    chart['timing_data_hex'] = b''.join(struct.pack(RECORD, *row) for row in out).hex()
    return dict(patched_intervals=len(patches), threshold=threshold,
                minimum_applied_speed=min(speed for _, _, speed in patches),
                maximum_applied_speed=max(speed for _, _, speed in patches),
                musical_clock_unchanged=True, negative_scroll_unchanged=True, intervals=patches)


def apply(chart, level):
    snap = snap_micro_attacks(chart['notes'])
    scroll = enforce_scroll_separation(chart, level)
    return dict(micro_snap=snap, scroll=scroll)
