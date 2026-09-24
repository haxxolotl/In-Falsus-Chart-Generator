"""Keep simultaneous floor chords inside the official In Falsus envelope."""
import collections
import itertools
from fractions import Fraction

from .native_curve_fit_v14 import edges

LIMITS = (2, 2, 3, 3)
LEFT, RIGHT = 1024, 4096


def value(note, key):
    return note[key][0] / note[key][1]


def lane(note):
    return max(1, min(4, round(value(note, 'x0') * 4)))


def rational(value_):
    fraction = Fraction(float(value_)).limit_denominator(65536)
    return [fraction.numerator, fraction.denominator]


def _clusters(notes, tolerance=15):
    heads = sorted((n for n in notes if n['type'] in (1, 2) and n.get('side') == 1
                    and value(n, 'w0') <= .251), key=lambda n: (n['start'], lane(n), n['id']))
    clusters = []
    for note in heads:
        if clusters and note['start'] - clusters[-1][-1]['start'] <= tolerance:
            clusters[-1].append(note)
        else:
            clusters.append([note])
    return clusters


def _field(notes, timestamp):
    return next((n for n in notes if n['type'] == 5 and n['start'] <= timestamp <= n['end']), None)


def _to_flick(note, field, direction):
    u = (note['start'] - field['start']) / max(1, field['end'] - field['start'])
    left, right = edges(field, max(0., min(1., u)))
    width = min(.26, right - left)
    center = (left + right) / 2
    note.update(type=4, side=4, end=note['start'], x0=rational(center), x1=rational(center),
                w0=rational(width), w1=rational(width), flags=direction)
    note.pop('_physical_key', None)
    note.pop('key_start', None)
    note.pop('key_end', None)


def apply(notes, level):
    """Repair every >tier-limit floor cluster in place and return a receipt."""
    maximum = LIMITS[level]
    removed = []
    converted = []
    reviewed = []
    phrase_index = 0
    for cluster in _clusters(notes):
        by_lane = collections.defaultdict(list)
        for note in cluster:
            by_lane[lane(note)].append(note)
        required = sorted(by_lane)
        if len(required) <= maximum:
            continue
        phrase_index += 1
        # Holds carry more authored information.  For a complete four-key tap
        # accent, alternate which inner lane is omitted so repeated figures
        # form a mirrored pattern rather than the same wall every time.
        ranked = sorted(required, key=lambda key: (-any(n['type'] == 2 for n in by_lane[key]),
                                                    key not in (1, 4),
                                                    abs(key - 2.5), key))
        if required == [1, 2, 3, 4] and maximum == 3 and not any(n['type'] == 2 for n in cluster):
            omitted = 2 if phrase_index % 2 else 3
            keep_lanes = set(required) - {omitted}
        else:
            keep_lanes = set(ranked[:maximum])
        extras = [n for key in required if key not in keep_lanes for n in by_lane[key]]
        timestamp = round(sum(n['start'] for n in cluster) / len(cluster))
        field = _field(notes, timestamp)
        has_flick = any(n['type'] == 4 and abs(n['start'] - timestamp) <= 15 for n in notes)
        # ULT/FBD may translate one excess accent to the mouse when the source
        # already supplies a playable field.  This retains workload while
        # removing the unreadable four-middle-key wall.
        if level >= 2 and field and not has_flick and extras:
            note = extras.pop(0)
            direction = LEFT if lane(note) <= 2 else RIGHT
            _to_flick(note, field, direction)
            converted.append(dict(time=note['start'], id=note.get('id'), direction=direction))
        for note in extras:
            notes.remove(note)
            removed.append(dict(time=note['start'], id=note.get('id'), lane=lane(note), type=note['type']))
        reviewed.append(dict(time=timestamp, before_lanes=required,
                             after_floor_lanes=sorted({lane(n) for n in cluster if n in notes and n['type'] in (1, 2)}),
                             converted_to_flick=bool(converted and converted[-1]['time'] in {n['start'] for n in cluster})))
    notes.sort(key=lambda n: (n['start'], n['type'] != 5, n['side'], n.get('id', 0)))
    groups = {}
    for ident, note in enumerate(notes):
        note['id'] = ident
        token = note.pop('_group', f"old:{note.get('group', ident)}")
        note['group'] = groups.setdefault(token, ident)
    violations = []
    for cluster in _clusters(notes):
        lanes = {lane(n) for n in cluster}
        if len(lanes) > maximum:
            violations.append((cluster[0]['start'], sorted(lanes)))
    assert not violations, violations[:3]
    return dict(policy_limits=list(LIMITS), level_limit=maximum, reviewed_clusters=len(reviewed),
                converted_to_flick=len(converted), removed_heads=len(removed),
                four_floor_clusters_after=0, timing_unchanged=True,
                reviewed=reviewed, converted=converted, removed=removed)
