"""Restore authored non-floor attacks without rebuilding accepted cursor curves.

Call apply(chart, meta, selection, source=None) before restore_floor_events().
The receipt discloses same-time attacks and continuous gestures represented by
one physical input; assert_roles() guards these representations after integration.
"""
import collections
import copy
import hashlib
from pathlib import Path

from .source_difficulty_v21 import _keys, resolve_source, source_clock

DISCRETE = {'hold', 'sky', 'flick', 'fast_arc'}


def _ids(note):
    return set(note.get('_source_events', [])) | ({note['_source_event']} if '_source_event' in note else set())


def _rapid(event):
    return (event['type'] == 'arc' and 0 < event['end'] - event['start'] <= 130
            and abs(event['x1'] - event['x0']) + abs(event['y1'] - event['y0']) >= .23)


def _gaps(start, end, notes):
    """Continuous physical coverage; at most two milliseconds of rounding slack."""
    intervals = sorted((max(start, n['start']), min(end, n['end'])) for n in notes
                       if n['type'] in (2, 5) and n['end'] > start and n['start'] < end)
    reached, gaps = start, []
    for a, b in intervals:
        if a > reached + 2:
            gaps.append((reached, a))
        reached = max(reached, b)
    if reached < end - 2:
        gaps.append((reached, end))
    return gaps


def _keyboard(kind, start, end, lane, serial, role, event):
    return dict(type=kind, side=2 if lane == 0 else 3 if lane == 5 else 1,
                start=start, end=end if kind == 2 else start,
                x0=[lane, 4], x1=[lane, 4], w0=[1, 4], w1=[1, 4],
                flags=0, id=serial, group=serial, _physical_key=lane,
                _source_event=event, _source_type=role, _v21_role_events=[event])


def _free_lanes(start, end, notes, floor_times):
    occupied = set()
    for n in notes:
        if n['type'] in (1, 2) and n['start'] <= end and n['end'] >= start:
            occupied.update(_keys(n))
    for t, lanes in floor_times.items():
        if start <= t <= end:
            occupied.update(lanes)
    return set(range(6)) - occupied


def apply(chart, meta, selection, source=None):
    """Mutate only source-role notes; return explicit timing/merge evidence."""
    protected = (selection['status'] in ('preserved_approved_special', 'exception_original_arrangement')
                 or selection['distinct_sources'] < 3 and selection['level'] < 3)
    if protected:
        return dict(status='preserved', name=meta['name'], reason=selection['status'])
    from .source_formats import parse_source
    from .cursor_paths_v14 import audit
    from .native_field_geometry import check
    from .flick_contract_v17 import validate

    path = resolve_source(meta['source_chart'])
    source = source or parse_source(path)
    clock = source_clock(meta)
    original = chart['notes']
    protected_notes = copy.deepcopy(sorted((n for n in original if n['type'] == 5 or n.get('_source_type') == 'floor'), key=lambda n: n['id']))
    existing = collections.defaultdict(list)
    for n in original:
        if n.get('_source_type') in DISCRETE:
            for i in _ids(n):
                existing[i].append(n)
    counts = collections.Counter()
    attacks, sustained, excluded = [], [], []
    floor_times = collections.defaultdict(set)
    for i, event in enumerate(source['notes']):
        role = event['type']
        try:
            start = clock(event['start'])
        except ValueError as exc:
            excluded.append(dict(event=i, role=role, reason='outside_verified_clock', detail=str(exc)))
            continue
        try:
            end = clock(event['end'])
        except ValueError as exc:
            boundary = max(s['source_end_seconds'] * 1000 for s in meta['alignment']['segments'])
            assert event['start'] <= boundary < event['end'], (meta['name'], event)
            end = clock(boundary)
            excluded.append(dict(event=i, role=role, reason='sustain_tail_outside_verified_clock',
                                 verified_until_source_ms=boundary, detail=str(exc)))
        if start < 1800:
            excluded.append(dict(event=i, role=role, reason='before_lead_in'))
            continue
        audio_end = meta.get('audio_end_ms', float('inf'))
        if start > audio_end:
            excluded.append(dict(event=i, role=role, reason='outside_local_audio', mapped_start_ms=start))
            continue
        if end > audio_end:
            excluded.append(dict(event=i, role=role, reason='sustain_tail_outside_local_audio',
                                 mapped_end_ms=end, local_audio_end_ms=audio_end))
            end = audio_end
        if role == 'floor':
            floor_times[start].add(event['lane'])
            continue
        if role == 'arc' and end <= start:
            counts['non_sustained_zero_arcs'] += 1
            continue
        rapid = _rapid(event)
        role = 'fast_arc' if rapid else role
        counts['source_' + role] += 1
        old = existing[i][0] if existing[i] else None
        attack = end if rapid else start
        # Recollect's accepted nonlinear raster fit includes pre-warp snaps.
        # The source-clock helper intentionally preserves those accepted notes.
        raster = meta['alignment'].get('status') == 'raster_quarter_clock_repaired'
        if old and raster:
            attack = old['start']
            if role == 'hold':
                start, end = old['start'], old['end'] + 1
            counts['accepted_raster_clock_preserved'] += 1
        if role in DISCRETE:
            matches = [n for n in existing[i] if n['type'] in (1, 2, 4)]
            exact = any(abs(n['start'] - attack) <= 2 for n in matches)
            if not exact:
                counts[('missing_' if not matches else 'mistimed_') + role] += 1
            if rapid and not matches and not _gaps(start, end, original):
                # Some accepted short arcs already use a sustained merged
                # gesture. Do not demand an extra flick over that same phrase.
                counts['rapid_arc_existing_sustained_equivalent'] += 1
                sustained.append(dict(event=i, role=role, start=start, end=end))
                continue
            attacks.append(dict(event=i, role=role, start=attack,
                                end=max(attack, end - 1) if role == 'hold' else attack,
                                source=event, previous=old))
        if role in ('arc', 'field', 'hold') and end > start:
            sustained.append(dict(event=i, role=role, start=start, end=end - (role == 'hold')))

    handled = {a['event'] for a in attacks}
    notes = [n for n in original if not (n.get('_source_type') in DISCRETE and _ids(n) & handled)]
    # Source floors are immutable here; the next helper restores their exact
    # lane/time contract, so reserve those desired keys instead of old layouts.
    layout = [n for n in notes if n.get('_source_type') != 'floor']
    serial = max((max(n['id'], n['group']) for n in original), default=0) + 1
    evidence, merges = [], []

    def append(note):
        nonlocal serial
        note.update(id=serial, group=serial)
        serial += 1
        notes.append(note)
        layout.append(note)
        return note

    def tag(note, event):
        note['_v21_role_events'] = sorted(set(note.get('_v21_role_events', [])) | {event})

    def attack_evidence(a, note, representation):
        tag(note, a['event'])
        evidence.append(dict(event=a['event'], role=a['role'], time=a['start'],
                             representation=representation))

    # Recover sustained authored holds before allocating accents around them.
    deferred = []
    for a in sorted((a for a in attacks if a['role'] == 'hold'), key=lambda a: (a['start'], a['event'])):
        free = _free_lanes(a['start'], a['end'], layout, floor_times)
        origin = int(a['source'].get('lane', 0))
        if free:
            lane = min(free, key=lambda k: (k != origin, k not in (0, 5), abs(k - origin), k))
            n = _keyboard(2, a['start'], a['end'], lane, serial, 'hold', a['event'])
            n['sustain_locked'] = 'source-role-v21-' + str(a['event'])
            append(n)
            attack_evidence(a, n, 'authored_hold' if lane == origin else 'rerouted_hold')
            if lane != origin:
                merges.append(dict(event=a['event'], role='hold', reason='held_or_authored_key_conflict',
                                   source_lane=origin, lane=lane, interval=[a['start'], a['end']]))
        else:
            deferred.append(a)

    # Keep the accepted cursor itself byte-for-byte. A missing sustained
    # interval uses a side hold; simultaneous source gestures share one cursor.
    sustain_evidence = []
    for s in sorted(sustained, key=lambda s: (s['start'], s['end'], s['event'])):
        tagged = [n for n in layout if s['event'] in _ids(n)]
        if _gaps(s['start'], s['end'], tagged):
            counts['incomplete_tagged_' + s['role']] += 1
        gaps = _gaps(s['start'], s['end'], layout)
        if gaps:
            counts['uncovered_' + s['role']] += 1
        elif _gaps(s['start'], s['end'], tagged):
            counts['merged_covered_' + s['role']] += 1
        for start, end in gaps:
            cursor = start
            while cursor < end - 2:
                free = _free_lanes(cursor, cursor, layout, floor_times)
                if not free:
                    # A six-key instantaneous chord allows a one-ms release.
                    assert _free_lanes(cursor + 1, cursor + 1, layout, floor_times), (meta['name'], s, cursor)
                    cursor += 1
                    free = _free_lanes(cursor, cursor, layout, floor_times)
                def next_block(k):
                    blocked = [n['start'] for n in layout if n['type'] in (1, 2)
                               and k in _keys(n) and cursor < n['start'] < end]
                    blocked += [t for t, lanes in floor_times.items() if k in lanes and cursor < t < end]
                    return min(blocked, default=end)
                # Prefer a side key for as long as it remains free. A handoff
                # is disclosed when no single key can span an entire phrase.
                lane = max(free, key=lambda k: (next_block(k), k in (0, 5), -k))
                stop = next_block(lane)
                n = _keyboard(2, cursor, stop - 1, lane, serial, 'source_gesture', s['event'])
                n['_v21_role_events'] = []
                n['_source_events'] = [s['event']]
                n['sustain_locked'] = 'source-gesture-v21-' + str(s['event'])
                append(n)
                counts['added_sustained_intervals'] += 1
                if cursor != start or stop != end:
                    merges.append(dict(event=s['event'], role=s['role'], reason='sustained_key_handoff',
                                       interval=[cursor, stop - 1], lane=lane))
                cursor = stop
        # Save the interval and disclose the union, without falsely attributing
        # a second independent cursor to an accepted one-cursor arrangement.
        sustain_evidence.append(dict(**s, representation='continuous_single_cursor_or_key_union'))

    def flick(a, reason):
        from .cursor_paths_v14 import rational
        from .native_curve_fit_v14 import edges
        same = [n for n in layout if n['type'] == 4 and n['start'] == a['start']]
        if same:
            n = same[0]
            merges.append(dict(event=a['event'], role=a['role'], reason='one_cursor_same_time_flick',
                               time=a['start'], existing_direction=n['flags'] & 0x1e00))
            attack_evidence(a, n, 'same_time_cursor_attack')
            return
        previous = a['previous']
        if previous and previous['type'] == 4:
            n = copy.deepcopy(previous)
            n.update(start=a['start'], end=a['start'])
        else:
            e = a['source']
            left = e.get('direction', 0) < 0 or e.get('x1', 0) < e.get('x0', 0)
            n = dict(type=4, side=4, start=a['start'], end=a['start'],
                     x0=[1, 2], x1=[1, 2], w0=[1, 3], w1=[1, 3], flags=1024 if left else 4096,
                     _source_type=a['role'], _source_event=a['event'])
        active = [n for n in layout if n['type'] == 5 and n['start'] <= a['start'] <= n['end']]
        if active:
            field = active[0]
            u = (a['start'] - field['start']) / max(1, field['end'] - field['start'])
            l, r = edges(field, u)
            n.update(x0=rational((l + r) / 2), x1=rational((l + r) / 2))
        n['_v21_role_events'] = []
        append(n)
        attack_evidence(a, n, reason)

    for a in sorted([a for a in attacks if a['role'] != 'hold'] + deferred,
                    key=lambda a: (a['start'], a['role'] not in ('flick', 'fast_arc'), a['event'])):
        previous = a['previous']
        if a['role'] in ('flick', 'fast_arc') or previous and previous['type'] == 4:
            flick(a, 'source_flick')
            continue
        free = _free_lanes(a['start'], a['start'], layout, floor_times)
        origin = a['source'].get('lane', 0 if a['source'].get('x0', .5) < .5 else 5)
        if free:
            lane = min(free, key=lambda k: (k != origin, k not in (0, 5), abs(k - origin), k))
            n = append(_keyboard(1, a['start'], a['start'], lane, serial, a['role'], a['event']))
            attack_evidence(a, n, 'source_attack_with_shared_sustain' if a['role'] == 'hold' else 'source_sky_attack')
        else:
            # All six keys are occupied: retain the attack on the one cursor.
            flick(a, 'keyboard_chord_full_cursor_equivalent')
            merges.append(dict(event=a['event'], role=a['role'], reason='six_keys_occupied_cursor_equivalent', time=a['start']))

    chart['notes'] = sorted(notes, key=lambda n: (n['start'], n['end'], n['id']))
    assert protected_notes == sorted((n for n in chart['notes'] if n['type'] == 5 or n.get('_source_type') == 'floor'), key=lambda n: n['id'])
    assert not audit(chart['notes'])['errors'], meta['name']
    check(chart['notes'])
    validate(chart['notes'])
    receipt = dict(status='passed', name=meta['name'], source_chart=str(path),
                   source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), counts=dict(counts),
                   attacks=evidence, sustains=sustain_evidence, adaptations=merges, exclusions=excluded,
                   accepted_cursor_and_floor_unchanged=True, native_runtime_proof=False)
    assert_roles(chart, receipt)
    return receipt


def assert_roles(chart, receipt):
    """Post-pipeline guard, insensitive to renumbered native ids/groups."""
    if receipt['status'] == 'preserved':
        return
    actual = collections.defaultdict(list)
    for n in chart['notes']:
        for i in n.get('_v21_role_events', []):
            if n['type'] in (1, 2, 4):
                actual[i].append(n)
    for a in receipt['attacks']:
        assert any(n['start'] == a['time'] for n in actual[a['event']]), ('source role attack lost', a)
    for s in receipt['sustains']:
        assert not _gaps(s['start'], s['end'], chart['notes']), ('source sustain lost', s)
