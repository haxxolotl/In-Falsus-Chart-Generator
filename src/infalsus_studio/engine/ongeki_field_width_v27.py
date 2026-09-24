"""Fit Ongeki raster rails to native mouse fields without flattening their shape."""

from __future__ import annotations

import bisect
import math
import statistics
from collections import defaultdict
from fractions import Fraction


def _value(note, key):
    return Fraction(*note[key][:2])


def _pair(value):
    value = value.limit_denominator(65536)
    return [value.numerator, value.denominator]


def correct(notes):
    """Narrow the raster-derived field; keep a few low-load phrases broad.

    Width changes are constant over each connected field group.  This avoids
    introducing tiny seams or angular width pulses at segment boundaries.
    """
    from .flick_zone_v26 import inspect as inspect_flicks, repair as repair_flicks
    from .native_field_geometry import canonicalize, check as check_fields

    groups=defaultdict(list)
    for note in notes:
        if note['type']==5 and note.get('_source_type')=='field':
            if note.get('_ongeki_width_v27'):
                raise ValueError('Ongeki width correction already applied')
            groups[note['group']].append(note)
    if not groups:
        return dict(fields=0,wide_groups=0,flicks_moved=0)

    times=sorted(n['start'] for n in notes if n['type'] in (1,2))
    candidates=[]
    for group,segments in groups.items():
        start=min(n['start'] for n in segments)
        end=max(n['end'] for n in segments)
        span=max(.1,(end-start)/1000)
        attacks=bisect.bisect_right(times,end)-bisect.bisect_left(times,start)
        width=statistics.median(float(_value(n,'w0')) for n in segments)
        # A wide spotlight belongs to a long, comparatively quiet cursor
        # phrase; busy keyboard sections retain the narrower control range.
        score=width-.012*attacks/span+.008*math.log1p(span)
        if span>=.6:
            candidates.append((score,group))
    wide={group for _,group in sorted(candidates,reverse=True)[:max(1,round(len(groups)*.12))]}

    before=[]
    for group,segments in groups.items():
        factor=Fraction(9,10) if group in wide else Fraction(3,5)
        for note in segments:
            before.append(float(_value(note,'w0')))
            for xkey,wkey in (('x0','w0'),('x1','w1')):
                left,width=_value(note,xkey),_value(note,wkey)
                narrowed=width*factor
                note[xkey]=_pair(left+(width-narrowed)/2)
                note[wkey]=_pair(narrowed)
            note['_ongeki_width_v27']=float(factor)
    changed=repair_flicks(notes)
    canonicalize(notes)
    check_fields(notes)
    assert not inspect_flicks(notes)[0]
    after=[float(_value(n,'w0')) for members in groups.values() for n in members]
    return dict(fields=len(before),wide_groups=len(wide),flicks_moved=len(changed),
                median_before=statistics.median(before),median_after=statistics.median(after),
                wide_fraction_before=sum(w>.5 for w in before)/len(before),
                wide_fraction_after=sum(w>.5 for w in after)/len(after),
                thin_fraction_after=sum(w<.25 for w in after)/len(after))
