"""Source-rank selection and floor-rhythm recovery; no installation writes.

Use inventory() once, then prepare_mapped_chart() and restore_floor_events().
Run this file to emit the full mapping review and the Tempestissimo canary.
Do not run old density caps, rapid thinning, or wide-jack substitution afterward:
assert_source_floor() is the post-pipeline acceptance guard for these events.
"""
import argparse
import bisect
import collections
import copy
import hashlib
import json
import re
from pathlib import Path

B = Path(__file__).resolve().parent
OUT = B / 'revision21/difficulty'
TIER = ('MIN', 'EVO', 'ULT', 'FBD')


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf8')


def resolve_source(raw):
    if not raw:
        return None
    p = Path(raw)
    candidates = (p, B / p, B / 'revision7/sources/aff' / p,
                  B / 'revision10/sources/aff' / p, B.parent / p)
    return next((p.resolve() for p in candidates if p.is_file()), None)


def select_source_ranks(count):
    """Indices in the ordered *distinct* source list, not difficulty aliases."""
    if count == 4:
        return (0, 1, 2, 3)
    if count == 3:
        return (0, 1, 2, 2)
    raise ValueError(f'Only three/four-source mapping is specified; received {count}')


def available_sources(song, source_inventory):
    record = source_inventory.get(song['base_name'], {})
    excluded = {d['ratingClass'] for d in record.get('song_metadata', {}).get('difficulties', [])
                if d.get('audioOverride')}
    pairs = list(record.get('chart_paths', {}).items())
    if not pairs:
        pairs = [(re.search(r'(\d+)\.aff$', p)[1], p) for p in record.get('paths', [])
                 if p.endswith('.aff')]
    if not pairs:
        pairs = [(c['level'], c['source_chart']) for c in song['charts'] if c.get('source_chart')]
    unique = []
    for difficulty, path in sorted(pairs, key=lambda p: int(p[0])):
        if int(difficulty) in excluded or path in [s['path'] for s in unique]:
            continue
        match = re.search(r'(\d+)\.aff$', path)
        source_diff = int(match[1]) if match else difficulty
        unique.append(dict(path=path, source_difficulty=source_diff,
                           exists=resolve_source(path) is not None))
    return unique, sorted(excluded)


def inventory(rows=None):
    rows = rows or read(B / 'revision20/all-charts.json')
    source_inventory = {s['base_name']: s for s in read(B / 'revision10/source-inventory.json')}
    reviewed = []
    for song in rows:
        sources, excluded = available_sources(song, source_inventory)
        ranks = select_source_ranks(len(sources)) if len(sources) >= 3 else None
        for meta in song['charts']:
            level = meta['level']
            special = song['title'] == 'Bad Apple!!' and level == 0
            selected = meta.get('source_chart')
            expected = sources[ranks[level]] if ranks else next((s for s in sources if s['path'] == selected), None)
            same = bool(expected and selected == expected['path'])
            donor = next((c['level'] for c in song['charts']
                          if expected and c.get('source_chart') == expected['path']), None)
            status = ('preserved_approved_special' if special else
                      'exception_original_arrangement' if not sources else
                      'blocked_missing_source' if not expected['exists'] else
                      'correction_required' if not same else
                      'exception_fewer_than_three_sources' if len(sources) < 3 else 'passed')
            reviewed.append(dict(title=song['title'], base_name=song['base_name'], name=meta['name'],
                                 level=level, tier=TIER[level], status=status,
                                 distinct_sources=len(sources), available_sources=sources,
                                 excluded_audio_override_difficulties=excluded,
                                 selected_source=selected, selected_difficulty=meta.get('source_difficulty'),
                                 expected_source=selected if special else expected['path'] if expected else None,
                                 expected_difficulty=meta.get('source_difficulty') if special else expected['source_difficulty'] if expected else None,
                                 source_rank=None if special or not ranks else ranks[level], donor_level=donor,
                                 ease_highest=not special and len(sources) == 3 and level == 2,
                                 preserve_full_highest=not special and bool(sources) and level == 3,
                                 next_action='preserve' if special or not sources else
                                 'clone_matching_source_donor_then_restore_floor' if not same else
                                 'restore_source_floor_then_ease_highest' if len(sources) == 3 and level == 2 else
                                 'restore_source_floor_without_density_thinning'))
    assert len(reviewed) == 380 and len({r['base_name'] for r in reviewed}) == 95
    return dict(songs=95, charts=380, states=dict(collections.Counter(r['status'] for r in reviewed)), rows=reviewed)


def prepare_mapped_chart(song, charts_by_name, level, selection):
    """Clone the selected donor's verified clock/gestures; retain destination identity.

    Returns (chart, metadata). This handles Recollect's repaired clock without
    regenerating its guide or using the obsolete nominal BPM alignment.
    """
    target = next(c for c in song['charts'] if c['level'] == level)
    donor = next(c for c in song['charts'] if c['level'] == (selection['donor_level']
                 if selection['status'] == 'correction_required' else level))
    chart, meta = copy.deepcopy(charts_by_name[donor['name']]), copy.deepcopy(donor)
    chart['name'] = target['name']
    for key in ('name', 'guid', 'level', 'rating'):
        if key in target:
            meta[key] = target[key]
    meta['source_difficulty'] = selection['expected_difficulty']
    meta.setdefault('conversion', {})['v21_source_mapping'] = copy.deepcopy(selection)
    return chart, meta


def _interp(x, xs, ys):
    i = max(0, min(len(xs) - 2, bisect.bisect_right(xs, x) - 1))
    return ys[i] + (x - xs[i]) / (xs[i + 1] - xs[i]) * (ys[i + 1] - ys[i])


def source_clock(meta):
    alignment = meta['alignment']
    if 'offset_seconds' in alignment:
        return lambda ms: round(alignment['offset_seconds'] * 1000 + ms * alignment['time_scale'])
    if alignment.get('segments'):
        segments = alignment['segments']
        def mapped(ms):
            t = ms / 1000
            s = next((s for s in segments if s['source_start_seconds'] - .0001 <= t <= s['source_end_seconds'] + .0001), None)
            if s is None:
                raise ValueError(f'Source time {ms} outside verified piecewise clock')
            return round(1000 * s['local_offset_seconds'] + ms * s['time_scale'])
        return mapped
    if alignment.get('status') == 'raster_quarter_clock_repaired':
        old = read(B / 'revision17/recollect/pixel-clock-fit.json')
        grid = read(B / 'revision17/recollect/beat-grid-mapping.json')
        times = read(B / 'revision17/recollect-final/receipt.json')['quarter_times_ms']
        return lambda ms: round(_interp(_interp(ms * 203 / 60000, old['beats'], old['pixels']),
                                       grid['quarter_pixels'], times))
    raise ValueError(f'Unsupported source clock: {alignment.get("status")}')


def normalize(notes):
    notes.sort(key=lambda n: (n['start'], n['end'], n.get('id', 0)))
    groups = {}
    for i, n in enumerate(notes):
        n['id'] = i
        n['group'] = groups.setdefault(n.get('group', ('new', i)), i)


def _keys(n):
    lane = round(n['x0'][0] / n['x0'][1] * 4)
    width = round(n['w0'][0] / n['w0'][1] * 4)
    return set(range(lane, lane + max(1, width)))


def _conflict(a, b):
    return bool(_keys(a) & _keys(b)) and (a['start'] == b['start'] or
           a['type'] == 2 and a['start'] <= b['start'] <= a['end'] or
           b['type'] == 2 and b['start'] <= a['start'] <= b['end'])


def restore_floor_events(chart, meta, *, ease_highest=False, source=None):
    """Restore source tap onsets and lanes, preserving existing holds and gestures.

    A collision with a real held key can reroute one tap; note density alone
    cannot remove a hit, widen a target, or replace an alternating phrase.
    Returned expected_floor is an exact post-pipeline guard, including reroutes.
    """
    from .source_formats import parse_source
    path = resolve_source(meta.get('source_chart'))
    source = source or parse_source(path)
    clock = source_clock(meta)
    before = chart['notes']
    originals = {n['_source_event']: n for n in before if n.get('_source_type') == 'floor'
                 and '_source_event' in n}
    taps = [(i, n) for i, n in enumerate(source['notes']) if n['type'] == 'floor']
    omitted = set()
    if ease_highest:
        # Ease by removing an extra voice in a simultaneous chord only. Fast
        # alternating single hits retain their complete rhythmic sequence.
        grouped = collections.defaultdict(list)
        for i, n in taps:
            grouped[clock(n['start'])].append((i, n))
        for group in grouped.values():
            if len(group) >= 2:
                victim = sorted(group, key=lambda p: (abs(p[1]['lane'] - 2.5), p[0]))[0]
                omitted.add(victim[0])
    fixed = [copy.deepcopy(n) for n in before if n.get('_source_type') != 'floor']
    generated, outside, past_audio = [], [], []
    serial = max((n['group'] for n in before), default=0) + 1
    for i, n in taps:
        if i in omitted:
            continue
        start = clock(n['start'])
        if start < 1800:
            outside.append(i)
            continue
        if start > meta.get('audio_end_ms', float('inf')):
            past_audio.append(i)
            continue
        previous = originals.get(i)
        if previous and (abs(previous['start'] - start) <= 2 or
                         meta['alignment'].get('status') == 'raster_quarter_clock_repaired'):
            # The repaired raster clock magnifies pre-warp pixel snaps during
            # rubato. Preserve accepted events; map only the missing ones.
            start = previous['start']
        lane = n['lane']
        note = dict(type=1, side=2 if lane == 0 else 3 if lane == 5 else 1,
                    start=start, end=start, x0=[lane, 4], x1=[lane, 4], w0=[1, 4], w1=[1, 4],
                    flags=0, id=serial, group=serial, _source_event=i, _source_type='floor',
                    _physical_key=lane, _v21_source_floor=True)
        serial += 1
        generated.append(note)
    # Hold intervals and non-floor attacks are fixed. Change only truly blocked
    # restored taps, with no minimum temporal separation test.
    keyboard = [n for n in fixed if n['type'] in (1, 2)]
    accepted, reroutes = [], []
    for n in sorted(generated, key=lambda n: (n['start'], n['_source_event'])):
        active = [a for a in keyboard if a['start'] <= n['start'] <= a['end']]
        active += [a for a in accepted if a['start'] == n['start']]
        if any(_conflict(n, a) for a in active):
            origin = n['x0'][0]
            free = [k for k in range(1, 5) if all(k not in _keys(a) for a in active)]
            if not free:
                # Six-key source chords can fill all four central keys while a
                # sustain remains active. Retain the attack on a free side key,
                # including the earlier accepted arrangement's choice if free.
                free = [k for k in (0, 5) if all(k not in _keys(a) for a in active)]
            if not free:
                raise ValueError(f'Genuine blocked source chord at {n["start"]} ms; event {n["_source_event"]}')
            lane = min(free, key=lambda k: (abs(k - origin), k))
            n.update(side=2 if lane == 0 else 3 if lane == 5 else 1,
                     x0=[lane, 4], x1=[lane, 4], _physical_key=lane)
            reroutes.append(dict(event=n['_source_event'], time=n['start'], source_lane=origin, free_lane=lane))
        accepted.append(n)
    chart['notes'] = fixed + accepted
    normalize(chart['notes'])
    expected = [dict(event=n['_source_event'], time=n['start'], lane=n['x0'][0]) for n in accepted]
    receipt = dict(status='prepared', source_chart=str(path), source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                   source_floor=len(taps), restored_floor=len(accepted),
                   previously_missing=sum(n['_source_event'] not in originals for n in accepted),
                   ease_highest=ease_highest, easier_chord_voices_omitted=sorted(omitted),
                   before_lead_in_omitted=outside, true_hold_or_chord_reroutes=reroutes,
                   outside_local_audio_omitted=past_audio,
                   expected_floor=expected, source_holds_and_existing_gestures_preserved=True)
    assert_source_floor(chart, receipt)
    return receipt


def assert_source_floor(chart, receipt):
    actual = {n.get('_source_event'): n for n in chart['notes'] if n.get('_source_type') == 'floor'}
    for e in receipt['expected_floor']:
        n = actual.get(e['event'])
        assert n is not None, ('source floor removed', e)
        assert (n['type'], n['start'], n['end'], n['x0'][0] / n['x0'][1], n['w0'][0] / n['w0'][1]) == (
            1, e['time'], e['time'], e['lane'] / 4, .25), ('source floor changed', e)
    assert [n['id'] for n in chart['notes']] == list(range(len(chart['notes'])))
    return True




