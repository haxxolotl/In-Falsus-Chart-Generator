"""Contextual source-guided cursor contours; never changes input notes or islands.

AFF recovery samples the inverse audio alignment and follows one source color.
Raster sources keep their existing macrogeometry. Width and mouse effort follow
keyboard attack density (a sustained key is not repeated keyboard work). Output
uses the game's real independent sine/cosine edge flags, not sampled polygons.
"""
import collections
import copy
import functools
import hashlib
import json
import math
import sys
from fractions import Fraction
from pathlib import Path

B = Path(__file__).resolve().parent
import numpy as np
from scipy.ndimage import gaussian_filter1d, median_filter
from .native_curve_fit_v14 import edges, ease
from .native_field_geometry import contain, check
from .cursor_paths_v14 import audit, rational
from .cursor_motion_v17 import peak
from .source_formats import parse_source
from .source_scroll_effects_v14 import read, save

BUSY_LIMITS = (1.0, 1.3, 1.6, 2.0)
QUIET_LIMITS = (1.3, 1.8, 2.8, 3.4)

# Kept as an explicit policy so revision 17/18 remains reproducible while a
# later corpus pass can change high-tier mouse demand without cloning this
# fairly delicate native-curve fitter.
DEFAULT_POLICY = dict(
    name='revision17',
    busy_limits=BUSY_LIMITS,
    quiet_limits=QUIET_LIMITS,
    source_busy_floor=(0., 0., 0., 0.),
    busy_min_width=(.34, .34, .34, .34),
    busy_max_width=(.65, .65, .65, .65),
    busy_forced_width=(.46, .46, .46, .46),
)

V20_POLICY = dict(
    name='revision20-balanced-mouse',
    busy_limits=(1.0, 1.3, 1.9, 2.6),
    quiet_limits=(1.3, 1.8, 3.0, 4.0),
    source_busy_floor=(.15, .20, .30, .50),
    busy_min_width=(.34, .34, .30, .26),
    busy_max_width=(.65, .65, .58, .52),
    busy_forced_width=(.46, .46, .40, .34),
)


@functools.lru_cache(maxsize=24)
def source_arcs(path):
    return [(i, n) for i, n in enumerate(parse_source(Path(path))['notes'])
            if n['type'] in ('arc', 'field') and n['end'] > n['start'] and not n.get('trace')]


def _source_path(meta):
    path = Path(meta.get('source_chart', ''))
    for root in (B / 'revision7/sources/aff', B / 'revision10/sources/aff', B):
        candidate = root / path
        if candidate.is_file():
            return candidate


def _sample(ns, ts):
    ends = np.array([n['end'] for n in ns])
    ix = np.minimum(np.searchsorted(ends, ts, side='right'), len(ns) - 1)
    lr = np.array([edges(ns[i], min(1, max(0, (t - ns[i]['start']) /
                    (ns[i]['end'] - ns[i]['start'])))) for t, i in zip(ts, ix)])
    return lr.mean(axis=1), lr[:, 1] - lr[:, 0], ix


def _position(note, timestamp):
    start = note.get('arc_start', note['start'])
    end = note.get('arc_end', note['end'])
    u = float(np.clip((timestamp - start) / max(1, end - start), 0, 1))
    if note['type'] == 'field':
        return (float(note['x0']) + (float(note['x1']) - float(note['x0'])) * u,
                float(note['w0']) + (float(note['w1']) - float(note['w0'])) * u)
    kind = note.get('easing', 's')
    kx = kind[:2] if len(kind) == 4 else kind
    ky = kind[2:] if len(kind) == 4 else ('b' if kind == 'b' else 's')
    def source_ease(value, mode):
        if mode == 'si': return math.sin(math.pi * value / 2)
        if mode == 'so': return 1 - math.cos(math.pi * value / 2)
        if mode == 'b': return 3 * value * value - 2 * value * value * value
        return value
    return (float(note['x0']) + (float(note['x1']) - float(note['x0'])) * source_ease(u, kx),
            float(note['y0']) + (float(note['y1']) - float(note['y0'])) * source_ease(u, ky))


def _density(notes, ts):
    # Gaussian attacks/second; chord attacks count individually, held keys once.
    density = np.zeros(len(ts))
    for n in notes:
        if n['type'] not in (1, 2, 3):
            continue
        for t, weight in ((n['start'], 1.), (n['end'], .25 if n['type'] == 2 else 0)):
            if weight and ts[0] - 1600 < t < ts[-1] + 1600:
                density += weight * np.exp(-.5 * ((ts - t) / 450) ** 2) / (math.sqrt(2 * math.pi) * .45)
    return density


def _min_width(p, q, ml, mr):
    # Exact stationary points of right(u)-left(u), plus endpoints.
    c = a = b = 0.
    for amp, mode in ((q[1] - p[1], mr), (p[0] - q[0], ml)):
        if mode == 0:
            c += amp
        elif mode == 1:
            a += amp * math.pi / 2
        else:
            b += amp * math.pi / 2
    theta = [0., math.pi / 2]
    radius = math.hypot(a, b)
    if radius and abs(c) <= radius:
        phi = math.atan2(b, a)
        z = math.acos(max(-1., min(1., -c / radius)))
        for root in (phi + z, phi - z):
            for shift in (-2 * math.pi, 0., 2 * math.pi):
                if 0 < root + shift < math.pi / 2:
                    theta.append(root + shift)
    return min(p[1] + (q[1] - p[1]) * ease(t * 2 / math.pi, mr) -
               p[0] - (q[0] - p[0]) * ease(t * 2 / math.pi, ml) for t in theta)


def _islands(notes):
    groups = collections.defaultdict(list)
    for n in notes:
        if n['type'] == 5:
            groups[n['group']].append(n)
    return {g: sorted(ns, key=lambda n: n['start']) for g, ns in groups.items()}


def apply(notes, level, meta, policy=None):
    """Restyle all field groups in place; return preservation and geometry proof."""
    policy = DEFAULT_POLICY if policy is None else policy
    busy_limit = policy['busy_limits'][level]
    quiet_limit = policy['quiet_limits'][level]
    before_nonfields = copy.deepcopy([n for n in notes if n['type'] != 5])
    groups = _islands(notes)
    before_boundaries = {g: (ns[0]['start'], ns[-1]['end']) for g, ns in groups.items()}
    initial_audit = audit(notes)
    if initial_audit['errors']:
        raise ValueError(('contextual_fields requires already continuous groups', initial_audit['errors'][:3]))
    path = _source_path(meta)
    arcs = source_arcs(str(path)) if path and path.suffix in ('.aff', '.json') else []
    alignment = meta.get('alignment', {})
    offset = alignment.get('offset_seconds', 0) * 1000
    scale = alignment.get('time_scale', 1)
    next_id = max((n.get('id', -1) for n in notes), default=-1) + 1
    out = [n for n in notes if n['type'] != 5]
    reports = []
    for group, ns in groups.items():
        start, end = before_boundaries[group]
        if end - start < 120:
            for n in ns:
                n['_motion_limit'] = busy_limit
            out.extend(ns)
            continue
        ts = np.linspace(start, end, max(3, math.ceil((end - start) / 10) + 1))
        dt = ts[1] - ts[0]
        old_x, old_w, original_ix = _sample(ns, ts)
        busy = np.clip((_density(before_nonfields, ts) - 1.0) / 3.5, 0, 1)
        quiet = 1 - busy
        limits = busy_limit * busy + quiet_limit * quiet
        # Median removes isolated raster errors; a short lowpass removes chatter
        # while leaving source phrase turns and width gestures in place.
        x = gaussian_filter1d(median_filter(old_x, size=3, mode='nearest'), 55 / dt, mode='nearest')
        w = gaussian_filter1d(median_filter(old_w, size=5, mode='nearest'), 95 / dt, mode='nearest')
        chosen_ids = np.full(len(ts), -1, dtype=int)
        source_x, source_w = x.copy(), w.copy()
        nearby = [(i, n) for i, n in arcs if n['start'] * scale + offset < end and n['end'] * scale + offset > start]
        color = None
        if nearby:
            # Fix a color for the entire island. Do not average opposing hands,
            # or switch color at every little source event boundary.
            coverage = collections.Counter()
            for _, n in nearby:
                coverage[n.get('color', 0)] += max(0, min(end, n['end'] * scale + offset) - max(start, n['start'] * scale + offset))
            color = max(coverage, key=lambda k: (coverage[k], -k))
            selected = sorted([(i, n) for i, n in nearby if n.get('color', 0) == color], key=lambda p: p[1]['start'])
            cursor = 0
            active = []
            previous_id = None
            for j, t in enumerate(ts):
                source_t = (t - offset) / scale
                while cursor < len(selected) and selected[cursor][1]['start'] <= source_t + .6:
                    active.append(selected[cursor]); cursor += 1
                active = [(i, n) for i, n in active if n['end'] >= source_t - .6]
                if not active:
                    continue
                i, n = min(active, key=lambda p: (p[0] != previous_id,
                           abs(float(_position(p[1], source_t)[0]) - (source_x[j - 1] if j else old_x[0])), p[0]))
                if n['type'] == 'field':
                    u = np.clip((source_t - n['start']) / max(1, n['end'] - n['start']), 0, 1)
                    sx = .05 + .9 * (float(n['x0']) + (float(n['x1']) - float(n['x0'])) * u)
                    sw = .9 * (float(n['w0']) + (float(n['w1']) - float(n['w0'])) * u)
                else:
                    sx, sy = _position(n, source_t)
                    # Smooth monotone projection retains authored offscreen turns.
                    sx = .5 + .44 * math.tanh((float(sx) - .5) / .55)
                    sw = (.18, .17, .15, .14)[level] + .48 * (1 - np.clip(float(sy), 0, 1))
                # A smooth minimum tapers near either side without pinning an
                # edge into a flat plateau for the whole source excursion.
                cap = max(.14, 2 * min(sx, 1 - sx) - .01)
                sw = max(.14, min(.75, sw / (1 + (sw / cap) ** 4) ** .25))
                source_x[j], source_w[j], chosen_ids[j] = sx, sw, i
                previous_id = i
            available = chosen_ids >= 0
            # A gradual blend at missing source spans avoids a handoff corner.
            availability = gaussian_filter1d(available.astype(float), 100 / dt, mode='nearest')
            if path and path.suffix == '.json':
                # Ongeki's forbidden-area boundary is the authored play lane,
                # so keyboard density must not flatten it into a static band.
                blend = availability
            else:
                blend = availability * (quiet + busy * policy['source_busy_floor'][level])
            source_x = gaussian_filter1d(source_x, 55 / dt, mode='nearest')
            source_w = gaussian_filter1d(source_w, 75 / dt, mode='nearest')
            x = x * (1 - blend) + source_x * blend
            w = w * (1 - blend) + source_w * blend
        # Busy keyboard work gets a broad safe region. Quiet source height can
        # narrow and expand the cursor contour rather than stay at one width.
        minimum = .14 * quiet + policy['busy_min_width'][level] * busy
        maximum = .75 * quiet + policy['busy_max_width'][level] * busy
        w = np.clip(w, minimum, maximum)
        w = np.maximum(w, busy * policy['busy_forced_width'][level])
        if nearby:
            # Preserve an authored center excursion by narrowing at the wall.
            # Letting the generic busy-width floor push the center inward was
            # the main reason Ongeki lane sweeps became nearly static.
            center_cap = np.maximum(.14, 2 * np.minimum(x, 1 - x) - .01)
            w = np.where(blend > .25, np.minimum(w, center_cap), w)
        # Preserve flick contact regions; input-note geometry is immutable here.
        for n in before_nonfields:
            if n['type'] == 4 and start - 180 <= n['start'] <= end + 180:
                keep = np.exp(-.5 * ((ts - n['start']) / 65) ** 2)
                x = x * (1 - keep) + old_x * keep
                w = w * (1 - keep) + old_w * keep
        w = np.clip(w, .14, .75)
        x = np.clip(x, w / 2, 1 - w / 2)
        # Local slew restriction prevents one fast source ornament from
        # flattening an entire phrase. Final native limiter reads _motion_limit.
        steps = np.minimum(limits[:-1], limits[1:]) * dt / 1000 * .78
        for _ in range(3):
            for indexes in (range(len(x) - 1), range(len(x) - 2, -1, -1)):
                for j in indexes:
                    delta = x[j + 1] - x[j]
                    extra = max(0, abs(delta) - steps[j]) / 2
                    if extra:
                        shift = math.copysign(extra, delta)
                        x[j] += shift; x[j + 1] -= shift
            x = np.clip(x, w / 2, 1 - w / 2)
        target = np.column_stack((x - w / 2, x + w / 2))
        new = []
        max_fit_error = 0.
        source_events = set()

        def fit(a, b, depth=0):
            nonlocal next_id, max_fit_error
            u = np.linspace(0, 1, 25)
            times = a + (b - a) * u
            wanted = np.column_stack([np.interp(times, ts, target[:, side]) for side in (0, 1)])
            p, q = wanted[0], wanted[-1]
            limit = float(np.min(np.interp(times, ts, limits)))
            center0, center1 = rational(float(p.mean())), rational(float(q.mean()))
            width0, width1 = rational(float(p[1] - p[0])), rational(float(q[1] - q[0]))
            original = ns[min(len(ns) - 1, int(np.searchsorted([n['end'] for n in ns], (a + b) / 2, side='right')))]
            note = copy.deepcopy(original)
            note.update(start=int(a), end=int(b), x0=center0, x1=center1, w0=width0, w1=width1)
            contain([note])
            rp, rq = np.array(edges(note, 0)), np.array(edges(note, 1))
            options = []
            for ml in range(3):
                for mr in range(3):
                    if _min_width(rp, rq, ml, mr) < .139:
                        continue
                    flags = (original.get('flags', 0) & ~252) | (4, 8, 16)[ml] | (32, 64, 128)[mr]
                    probe = dict(note, flags=flags)
                    actual = np.array([edges(probe, float(v)) for v in u])
                    error = float(np.max(np.abs(actual - wanted)))
                    velocity = peak(probe)
                    # Native curvature is preferred when visually equivalent.
                    score = error + (0.0006 if ml == mr == 0 else 0)
                    options.append((score, error, velocity, flags))
            feasible = [v for v in options if v[2] <= limit + .005]
            best = min(feasible or options)
            if (best[1] > .006 or b - a > 1800) and b - a > 60 and depth < 14:
                # Split at the largest geometric feature, not fixed tiny ticks.
                mid = round((a + b) / 2)
                fit(a, mid, depth + 1); fit(mid, b, depth + 1)
                return
            note.update(id=next_id, flags=best[3], _motion_limit=round(limit, 4),
                        _field_context='source_arc' if color is not None else 'source_macrogeometry',
                        _keyboard_load=round(float(np.mean(np.interp(times, ts, busy))), 4))
            next_id += 1
            relevant = chosen_ids[(ts >= a) & (ts <= b)]
            ids = sorted(set(int(i) for i in relevant if i >= 0))
            if ids:
                midpoint_id = int(chosen_ids[min(len(ts) - 1, int(np.searchsorted(ts, (a + b) / 2)))])
                note['_source_event'] = midpoint_id if midpoint_id >= 0 else ids[0]
                note['_source_events'] = ids
                note['_source_color'] = int(color)
                note['_source_type'] = 'field' if path and path.suffix == '.json' else 'arc'
                source_events.update(ids)
            else:
                ids = {k for n in ns if n['start'] < b and n['end'] > a
                       for k in n.get('_source_events', [n['_source_event']] if '_source_event' in n else [])}
                if ids:
                    note['_source_events'] = sorted(ids)
            new.append(note)
            max_fit_error = max(max_fit_error, best[1])

        # Keep substantial source reversals as explicit anchors. Native easing
        # then rounds the incoming/outgoing shoulders instead of a jagged corner.
        cuts = {int(start), int(end)}
        for axis in (x, w):
            derivative = np.diff(axis)
            for j in np.where(derivative[:-1] * derivative[1:] < 0)[0] + 1:
                left = max(0, j - round(110 / dt)); right = min(len(ts) - 1, j + round(110 / dt))
                if abs(axis[j] - axis[left]) + abs(axis[j] - axis[right]) > .022:
                    cuts.add(round(float(ts[j])))
        ordered = sorted(cuts)
        # Coalesce nearby center/width extrema, retaining genuine phrase turns.
        anchors = [ordered[0]]
        for t in ordered[1:-1]:
            if t - anchors[-1] >= 100 and end - t >= 100:
                anchors.append(t)
        anchors.append(ordered[-1])
        for a, b in zip(anchors, anchors[1:]):
            fit(a, b)
        out.extend(new)
        reports.append(dict(group=group, start=start, end=end, before_segments=len(ns), after_segments=len(new),
                            source_color=color, source_events=sorted(source_events), native_curved_segments=sum(bool(n['flags'] & (8 | 16 | 64 | 128)) for n in new),
                            min_width=min(min(float(n[k][0] / n[k][1]) for k in ('w0', 'w1')) for n in new),
                            max_width=max(max(float(n[k][0] / n[k][1]) for k in ('w0', 'w1')) for n in new),
                            max_edge_fit_error=max_fit_error, max_native_speed=max(map(peak, new)),
                            final_limiter_segments=sum(peak(n) > n['_motion_limit'] + .02 for n in new)))
    out.sort(key=lambda n: (n['start'], n['type'] != 5, n['side'], n.get('id', 0)))
    contain(out)
    final_audit = audit(out)
    assert not final_audit['errors'], final_audit['errors'][:3]
    assert {g: (ns[0]['start'], ns[-1]['end']) for g, ns in _islands(out).items()} == before_boundaries
    assert sorted((json.dumps(n, sort_keys=True) for n in out if n['type'] != 5)) == sorted(json.dumps(n, sort_keys=True) for n in before_nonfields)
    check(out)
    notes[:] = out
    return dict(groups=reports, changed_groups=len(reports), source=meta.get('source_chart'), policy=policy['name'],
                native_curved_segments=sum(r['native_curved_segments'] for r in reports),
                final_limiter_segments=sum(peak(n) > n['_motion_limit'] + .02 for n in out if n['type'] == 5),
                zero_gaps=True, same_groups_and_entrance_exit_times=True, all_nonfield_notes_identical=True,
                exact_rational_endpoint_containment=True, native_interior_width_verified=True)


def run_canaries():
    dest = B / 'revision17/contextual-fields'
    dest.mkdir(parents=True, exist_ok=True)
    results = []
    for row in read(B / 'revision17/all-charts.json'):
        if row['title'].lower() not in ('sheriruth', 'deinos phainein', 'testify', 'recollect lines'):
            continue
        level = 2 if row['title'].lower() == 'sheriruth' else 3
        meta = next(m for m in row['charts'] if m['level'] == level)
        source_path = B / 'revision17/charts' / (meta['name'] + '.json')
        chart = read(source_path)
        before = copy.deepcopy(chart)
        report = apply(chart['notes'], level, meta)
        save(dest / (meta['name'] + '.json'), chart)
        save(dest / (meta['name'] + '.before.json'), before)
        results.append(dict(title=row['title'], name=meta['name'], level=level,
                            baseline_sha256=hashlib.sha256(source_path.read_bytes()).hexdigest(), report=report))
        print(row['title'], report['changed_groups'], report['native_curved_segments'], 'requires_limiter', report['final_limiter_segments'], flush=True)
    save(dest / 'receipt.json', results)
    render_canaries(dest, results)


def render_canaries(dest, results):
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 22)
    small = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 16)
    image = Image.new('RGB', (1600, 340 * len(results) * 2), '#101520')
    draw = ImageDraw.Draw(image)
    slot = 0
    for row in results:
        if row['title'].lower() == 'sheriruth':
            start, end = 111000, 123000
        else:
            selected = max(row['report']['groups'], key=lambda r: (r['end'] - r['start']) * (r['max_width'] - r['min_width']))
            start, end = selected['start'], min(selected['end'] + 300, selected['start'] + 14000)
        for label, suffix in (('Current staged baseline', '.before.json'), ('Contextual candidate', '.json')):
            top = slot * 340; slot += 1
            draw.text((25, top + 12), f"{row['title']} | {label} | {start/1000:.1f}-{end/1000:.1f}s", font=font, fill='white')
            for second in range(math.ceil(start / 1000), math.floor(end / 1000) + 1):
                xx = 30 + (second * 1000 - start) / (end - start) * 1540
                draw.line([(xx, top + 61), (xx, top + 282)], fill='#263041')
                draw.text((xx - 10, top + 305), str(second), font=small, fill='#b5bfd0')
            for y in (top + 62, top + 282):
                draw.line([(30, y), (1570, y)], fill='#8994a5')
            for n in read(dest / (row['name'] + suffix))['notes']:
                if n['end'] < start or n['start'] > end:
                    continue
                if n['type'] != 5:
                    if n['type'] in (1, 2, 3) and start <= n['start'] <= end:
                        xx = 30 + (n['start'] - start) / (end - start) * 1540
                        draw.line([(xx, top + 285), (xx, top + 299)], fill='#7cd9d2', width=2)
                    continue
                lo, hi = [], []
                for t in np.linspace(max(start, n['start']), min(end, n['end']), max(3, int((min(end, n['end']) - max(start, n['start'])) / 4))):
                    l, r = edges(n, (t - n['start']) / (n['end'] - n['start']))
                    xx = 30 + (t - start) / (end - start) * 1540
                    lo.append((xx, top + 62 + l * 220)); hi.append((xx, top + 62 + r * 220))
                draw.polygon(lo + hi[::-1], fill='#7653bc')
                draw.line(lo, fill='#e4c9ff', width=2); draw.line(hi, fill='#e4c9ff', width=2)
    image.save(dest / 'before-after.png')


