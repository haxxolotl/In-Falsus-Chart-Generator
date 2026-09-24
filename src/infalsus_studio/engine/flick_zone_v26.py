"""Keep each flick's whole contact region inside the active native cursor ribbon."""

from fractions import Fraction

from .native_curve_fit_v14 import edges


def value(note, key):
    return note[key][0] / note[key][1]


def _contacts(notes):
    fields = sorted((n for n in notes if n['type'] == 5), key=lambda n: n['start'])
    flicks = sorted((n for n in notes if n['type'] == 4), key=lambda n: (n['start'], n['id']))
    active = []
    index = 0
    for note in flicks:
        if note['start'] != note['end'] or note['x0'] != note['x1'] or note['w0'] != note['w1']:
            raise ValueError(('expected instantaneous flick geometry', note['id']))
        at = note['start']
        while index < len(fields) and fields[index]['start'] <= at:
            active.append(fields[index])
            index += 1
        active = [n for n in active if n['end'] >= at]
        if not active:
            yield note, None, []
            continue
        bounds = [edges(n, (at - n['start']) / max(1, n['end'] - n['start'])) for n in active]
        yield note, (max(b[0] for b in bounds), min(b[1] for b in bounds)), active


def inspect(notes, tolerance=1e-5):
    """Return every flick outside its active hold, plus free flicks separately."""
    violations, free = [], []
    for note, bounds, candidates in _contacts(notes):
        at = note['start']
        if bounds is None:
            free.append(dict(id=note['id'], time_ms=at))
            continue
        left, right = bounds
        flick_left = value(note, 'x0') - value(note, 'w0') / 2
        flick_right = value(note, 'x0') + value(note, 'w0') / 2
        if flick_left < left - tolerance or flick_right > right + tolerance:
            violations.append(dict(id=note['id'], time_ms=at, source_type=note.get('_source_type'),
                                   field_ids=[n['id'] for n in candidates],
                                   field=[left, right], flick=[flick_left, flick_right],
                                   outside_left=max(0., left - flick_left),
                                   outside_right=max(0., flick_right - right),
                                   disjoint=flick_right < left or flick_left > right))
    return violations, free


def repair(notes):
    """Move or narrow only invalid flicks; keep each native hold curve unchanged."""
    changes = []
    for note, bounds, _ in _contacts(notes):
        if bounds is None:
            continue
        left, right = bounds
        center, width = value(note, 'x0'), value(note, 'w0')
        if center - width / 2 >= left - 1e-5 and center + width / 2 <= right + 1e-5:
            continue
        # A small inset absorbs rational rounding while retaining the original
        # target width whenever the native ribbon has room for it.
        low, high = left + 1e-4, right - 1e-4
        if high <= low or width <= 0:
            raise ValueError(('no valid flick contact inside cursor ribbon', note['id'], bounds))
        width = min(width, high - low)
        center = min(high - width / 2, max(low + width / 2, center))
        old = (note['x0'][:], note['w0'][:])
        x = Fraction(center).limit_denominator(65536)
        w = Fraction(width).limit_denominator(65536)
        note.update(x0=[x.numerator, x.denominator], x1=[x.numerator, x.denominator],
                    w0=[w.numerator, w.denominator], w1=[w.numerator, w.denominator])
        changes.append(dict(id=note['id'], time_ms=note['start'], old_x=old[0], old_w=old[1],
                            new_x=note['x0'], new_w=note['w0']))
    remaining, _ = inspect(notes)
    if remaining:
        raise ValueError(('flick contact repair incomplete', remaining[:3]))
    return changes
