"""Source-position expression for Deemo's otherwise floor-only translation.

``apply(notes, metadata, source=None)`` edits one staged list in place. It keeps
every existing attack and all authored cursor geometry/timing. The optional
parsed source lets the difficulty selector pass its newly selected difficulty.
CLI writes only revision21/expression; it never encodes or installs charts.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from bisect import bisect_left
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path
from statistics import mean, pstdev

from .chord_balance_v20 import rational, value
from .cursor_continuity import regroup, check as check_continuity
from .cursor_motion_v17 import peak, LIMITS
from .flick_contract_v17 import validate as validate_flicks
from .native_curve_fit_v14 import edges
from .native_field_geometry import canonicalize, check as check_geometry

B = Path(__file__).resolve().parent
OUT = B / 'revision21' / 'expression'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf8'))


def save(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf8')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_path(metadata):
    raw = metadata.get('source_chart')
    if not raw:
        return None
    path = Path(raw)
    candidates = [path, B.parent / path]
    candidates += [B / f'revision{v}' / 'sources' / 'aff' / path for v in (10, 7, 18, 17, 9)]
    return next((p for p in candidates if p.is_file()), None)


@lru_cache(maxsize=64)
def load_source(path):
    from .source_formats import parse_source
    return parse_source(Path(path))


def interaction_metrics(notes):
    """The same six public calibration features as stock-metrics.json."""
    attacks = [n for n in notes if n['type'] in (1, 2, 4)]
    keys = [n for n in attacks if n['type'] in (1, 2)]
    times = sorted(n['start'] for n in attacks)
    span = max(1., (max((n['end'] for n in notes), default=1000)
                   - min((n['start'] for n in notes), default=0)) / 1000)
    covered, end = 0, -1
    for n in sorted((n for n in notes if n['type'] == 5), key=lambda n: n['start']):
        covered += max(0, n['end'] - max(end, n['start']))
        end = max(end, n['end'])
    return dict(attacks=len(attacks), floor=sum(n['side'] == 1 for n in attacks),
                side=sum(n['side'] in (2, 3) for n in attacks),
                flicks=sum(n['type'] == 4 for n in attacks),
                density=len(attacks) / span,
                peak_2s=max(((bisect_left(times, t + 2000) - i) / 2
                             for i, t in enumerate(times)), default=0),
                field_coverage=covered / 1000 / span,
                side_key_fraction=sum(n['side'] in (2, 3) for n in keys) / max(1, len(keys)),
                flick_fraction=sum(n['type'] == 4 for n in attacks) / max(1, len(attacks)),
                hold_key_fraction=sum(n['type'] == 2 for n in keys) / max(1, len(keys)))


def rating_evidence(notes, level):
    """Compare shipped charts without imposing a fictitious tier minimum.

    This is a recommendation, not a difficulty mutation or a playtest claim.
    Feature variance and inverse-distance averaging match the existing rater;
    peak/density neighbours replace its known overbroad pooled demand floor.
    """
    stock = read(B / 'revision2' / 'stock-metrics.json')
    features = ('density', 'peak_2s', 'field_coverage', 'side_key_fraction',
                'flick_fraction', 'hold_key_fraction')
    metrics = interaction_metrics(notes)
    scales = {k: max(.05, pstdev(r[k] for r in stock)) for k in features}
    distances = [(mean(((r[k] - metrics[k]) / scales[k]) ** 2 for k in features), r)
                 for r in stock]
    near = sorted(distances, key=lambda pair: pair[0])[:7]
    estimate = sum(r['rating'] / (d + .1) for d, r in near) / sum(1 / (d + .1) for d, _ in near)
    def q85(values):
        values = sorted(values)
        index = (len(values) - 1) * .85
        lower = int(index)
        return values[lower] + (values[min(lower + 1, len(values) - 1)] - values[lower]) * (index - lower)
    highest = max(r['rating'] for r in stock)
    guard = highest
    for target in range(1, highest + 1):
        neighbours = [r for r in stock if target - 1 <= r['rating'] <= target]
        if len(neighbours) >= 3 and all(metrics[k] <= q85([r[k] for r in neighbours]) * 1.08
                                        for k in ('density', 'peak_2s')):
            guard = target
            break
    return dict(recommended_rating=max(guard, round(estimate)), estimated_rating=estimate,
                demand_floor=guard,
                method='Seven nearest shipped charts across tiers; no artificial tier rating floor',
                metrics=metrics, stock_metrics_sha256=sha(B / 'revision2' / 'stock-metrics.json'),
                nearest=[dict(chart=r['chart'], level=r['level'], rating=r['rating'], distance=d,
                              density=r['density'], peak_2s=r['peak_2s'],
                              field_coverage=r['field_coverage']) for d, r in near],
                limitation='Offline comparison; live difficulty acceptance remains separate')


def _attacks(notes):
    return Counter((n.get('_source_event'), n['start'], n['end'])
                   for n in notes if n['type'] in (1, 2, 4))


def _gesture_fields(notes, events, level):
    """Interpret only uninterrupted, monotonic, single-voice source-position runs.

    No sampled audio pitch is claimed: DS positions are authored choreography.
    A sustained mouse interpretation is admitted only between real adjacent
    attacks, with no rests/chords, and never overlaps an existing authored arc.
    """
    if level < 2:
        return []
    fields = [n for n in notes if n['type'] == 5]
    clusters = defaultdict(list)
    for n in notes:
        if n['type'] in (1, 2, 4):
            clusters[n['start']].append(n)
    runs, run, direction = [], [], 0
    for timestamp, members in sorted(clusters.items()):
        n = members[0]
        event = events.get(n.get('_source_event'), {})
        valid = len(members) == 1 and n['type'] == 1 and event.get('type') == 'floor'
        if not valid:
            if len(run) >= 4:
                runs.append(run)
            run, direction = [], 0
            continue
        x = event['source_x']
        if run:
            dt, dx = timestamp - run[-1][0]['start'], x - run[-1][1]
            sign = 1 if dx > .04 else -1 if dx < -.04 else 0
            if not (130 <= dt <= 600 and sign and (not direction or sign == direction)):
                if len(run) >= 4:
                    runs.append(run)
                run, direction = [], 0
            else:
                direction = sign
        run.append((n, x))
    if len(run) >= 4:
        runs.append(run)
    receipts = []
    for run in runs:
        a, z = run[0][0]['start'], run[-1][0]['start']
        if not (600 <= z - a <= 2400 and abs(run[-1][1] - run[0][1]) >= .35):
            continue
        if any(f['start'] <= z + 180 and f['end'] >= a - 180 for f in fields):
            continue
        group = max((n.get('group', 0) for n in notes), default=0) + 1
        added = []
        for (left, x0), (right, x1) in zip(run, run[1:]):
            # Gentle native sine/cosine edges, bounded before admission.
            field = dict(type=5, side=4, start=left['start'], end=right['start'],
                         x0=rational(.2 + .6 * x0), x1=rational(.2 + .6 * x1),
                         w0=[1, 3], w1=[1, 3], flags=72, group=group,
                         id=len(notes) + len(added), _source_type='floor_contour',
                         _source_event=left['_source_event'],
                         _source_events=[left['_source_event'], right['_source_event']],
                         _expression_v21='authored_monotonic_position_run')
            if peak(field) > LIMITS[level]:
                added = []
                break
            added.append(field)
        if added:
            canonicalize(added)
            notes.extend(added)
            fields.extend(added)
            receipts.append(dict(start=a, end=z,
                                 source_events=[n['_source_event'] for n, _ in run],
                                 source_positions=[x for _, x in run],
                                 interpretation='continuous mouse contour between authored attacks'))
    return receipts


def apply(notes, metadata, source=None):
    level = int(metadata['level'])
    before = interaction_metrics(notes)
    report = dict(status='unchanged', before=before, after=before,
                  source_chart=metadata.get('source_chart'))
    if metadata.get('protected_expression') or (level == 0 and 'badapple' in
            (metadata.get('source_chart', '') + metadata.get('title', '')).lower().replace(' ', '')):
        return dict(report, status='protected', reason='Accepted MIN raster preserved')
    path = source_path(metadata)
    # DS touch-position charts have no native side keys. Other games already
    # author floor/side/sky semantics and are not overwritten by this adapter.
    if source is None and (not path or path.parent.name != 'deemo'):
        return dict(report, reason='Other source input semantics preserved')
    source = source if source is not None else load_source(str(path))
    if source.get('source_format') != 'Deemo DS':
        return dict(report, reason='Other source input semantics preserved')
    report.update(source_sha256=sha(path) if path else None,
                  source_counts=dict(Counter(n['type'] for n in source['notes'])))
    events = {i: e for i, e in enumerate(source['notes'])}
    signature = _attacks(notes)
    original_fields = [copy.deepcopy(n) for n in notes if n['type'] == 5]
    curves = _gesture_fields(notes, events, level)
    fields = [n for n in notes if n['type'] == 5]
    flick_times = [n['start'] for n in notes if n['type'] == 4]
    moved = []
    for n in sorted(list(notes), key=lambda n: (n['start'], n.get('id', 0))):
        e = events.get(n.get('_source_event'), {})
        if n['type'] != 1 or n['side'] != 1 or e.get('type') != 'floor':
            continue
        x, timestamp = e['source_x'], n['start']
        field = next((f for f in fields if f['start'] <= timestamp <= f['end']), None)
        mode = None
        if level >= 1 and field and all(abs(timestamp - t) >= (380, 320, 260, 210)[level]
                                       for t in flick_times):
            u = (timestamp - field['start']) / max(1, field['end'] - field['start'])
            lo, hi = edges(field, min(1., max(0., u)))
            direction = 1024 if value(field, 'x1') < value(field, 'x0') else 4096
            center, width = (lo + hi) / 2, min(.26, hi - lo)
            n.update(type=4, side=4, x0=rational(center), x1=rational(center),
                     w0=rational(width), w1=rational(width), flags=direction)
            flick_times.append(timestamp)
            mode = 'source_attack_on_authored_or_position_contour'
        else:
            outer = (0., 0., .125, .125)[level]
            side = 2 if x <= outer else 3 if x >= 1 - outer else None
            spacing = (400, 300, 210, 160)[level]
            if side and not any(o is not n and o['type'] in (1, 2) and o['side'] == side
                                and o['start'] - spacing < timestamp < o['end'] + spacing
                                for o in notes):
                key = 0 if side == 2 else 5
                n.update(side=side, x0=[key, 4], x1=[key, 4], w0=[1, 4], w1=[1, 4])
                mode = 'authored_outer_position_to_same_side_key'
        if mode:
            n.pop('_physical_key', None)
            n.pop('key_start', None)
            n.pop('key_end', None)
            n['_expression_v21'] = mode
            moved.append(dict(source_event=n['_source_event'], time=timestamp,
                              source_x=x, type=n['type'], side=n['side'], reason=mode))
    if curves or moved:
        notes.sort(key=lambda n: (n['start'], n['type'] != 5, n['side'], n.get('id', 0)))
        continuity = regroup(notes)
        check_continuity(notes)
        validate_flicks(notes)
        check_geometry(notes)
        assert _attacks(notes) == signature, 'An authored attack time/identity was changed'
        # Only IDs/groups may change on existing fields; native curve flags and
        # geometry remain byte-for-byte equivalent after regrouping.
        clean = lambda n: {k: v for k, v in n.items() if k not in ('id', 'group')}
        assert all(clean(a) in [clean(n) for n in notes if n['type'] == 5]
                   for a in original_fields)
        report.update(status='changed', continuity=continuity)
    report.update(after=interaction_metrics(notes), contours=curves, translated=moved,
                  attack_timing_and_identity_preserved=True, existing_native_curves_preserved=True)
    if report['status'] == 'unchanged':
        report['reason'] = 'No eligible authored outer accent or directional run at this tier'
    return report




