"""Move overloaded translated sky taps onto playable central keys.

Source floor notes, side holds, mouse gestures, and note times are untouched.
The side limits are calibrated against decoded official charts; this is a
physical readability check, not a claim of human playtest acceptance.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right, insort
from collections import Counter

from .playability import _span, DEFAULT_PROFILE


DEFAULT_LIMITS = {"same_side_gap_ms": 120, "combined_250_ms": 4,
                  "combined_500_ms": 6, "combined_1000_ms": 10,
                  "side_targets_per_minute": 88, "side_share": .30,
                  "max_center_chord": 3,
                  "sky_center_alternation_ms": 165}


def _value(pair):
    return pair[0] / pair[1]


def _source_x(note, source):
    if source is not None and isinstance(note.get('_source_event'), int):
        index = note['_source_event']
        if 0 <= index < len(source):
            event = source[index]
            if event.get('type') == 'sky':
                from .engine.convert_source_charts import position
                return float(position(event, event['start'])[0])
    return -.25 if note['side'] == 2 else 1.25


def _window_count(times, start, end):
    return bisect_right(times, end) - bisect_left(times, start)


def _candidate_key(note, source, attacks, holds, wide_near, chord_size,
                   moved_sky, profile, limits):
    time=note['start']
    if chord_size.get(time, 0) >= limits['max_center_chord']:
        return None
    x=_source_x(note, source)
    # AFF floor centers -0.25,+0.25,+0.75,+1.25 map to ASDF.
    preferred=max(1,min(4,round(2*x+1.5)))
    choices=[]
    for key in range(1,5):
        nearby=attacks[key]
        pos=bisect_left(nearby,time)
        left=nearby[pos-1] if pos else -10**9
        right=nearby[pos] if pos<len(nearby) else 10**9
        if min(time-left,right-time) < profile['tap_recovery_ms']:
            continue
        if any(a-profile['tap_recovery_ms']<time<=b for a,b in holds[key]):
            continue
        if _window_count(wide_near[key],time-profile['near_ms'],time+profile['near_ms']):
            continue
        if _window_count(nearby,time-profile['key_window_ms']+1,time) >= profile['max_key_density']:
            continue
        # Adding an early tap must not make a later authored floor note become
        # the fifth hit in its own 500 ms window.
        future=nearby[pos:bisect_right(nearby,time+profile['key_window_ms']-1)]
        if any(_window_count(nearby,t-profile['key_window_ms']+1,t)
               >=profile['max_key_density'] for t in future):
            continue
        last_sky=moved_sky[key]
        alternation=last_sky is not None and time-last_sky<limits['sky_center_alternation_ms']
        choices.append((alternation,abs(key-preferred),
                        _window_count(nearby,time-500,time),key))
    return min(choices)[-1] if choices else None


def _retain_sky(notes, movable, source, limits):
    """Reserve scarce side targets for spatially distinct, isolated accents."""
    keyboard=[n for n in notes if n.get('type') in (1,2) and n.get('side') in (1,2,3)]
    if not keyboard or not movable:return set()
    fixed_side=sum(n['side'] in (2,3) for n in keyboard)-len(movable)
    span=max(n['end'] for n in keyboard)-min(n['start'] for n in keyboard)
    cap=min(int(limits['side_targets_per_minute']*max(span,1)/60000),
            int(limits['side_share']*len(keyboard)))
    retain=max(0,min(len(movable),cap-fixed_side))
    if retain==len(movable):return {id(n) for n in movable}
    times=sorted(n['start'] for n in movable)
    ranked=[]
    for note in movable:
        t=note['start'];pos=bisect_left(times,t)
        # Duplicate onsets are useful chords, but never count as temporal gaps.
        before=times[pos-1] if pos else t-1000
        after=times[bisect_right(times,t)] if bisect_right(times,t)<len(times) else t+1000
        isolation=min(t-before,after-t,1000)
        edge=abs(_source_x(note,source)-.5)
        ranked.append((isolation,edge,-t,-note.get('id',0),id(note)))
    ranked.sort(reverse=True)
    return {item[-1] for item in ranked[:retain]}


def apply(notes, level, source=None, *, limits=None, profile=None):
    """Rebalance excess side taps in place; report every declined move."""
    limits=limits or DEFAULT_LIMITS
    profile=profile or DEFAULT_PROFILE
    source_notes=source.get('notes') if isinstance(source,dict) else source
    side=[[],[]]
    movable=[]
    attacks={key:[] for key in range(1,5)}
    holds={key:[] for key in range(1,5)}
    wide_near={key:[] for key in range(1,5)}
    chord=Counter()
    for note in notes:
        if note.get('type') not in (1,2):continue
        if note.get('type')==1 and note.get('side') in (2,3) and note.get('_source_type')=='sky':
            movable.append(note)
            continue
        if note.get('side') in (2,3):
            insort(side[note['side']-2],note['start'])
        if note.get('side')!=1:continue
        span=_span(note,profile['key_count'])
        chord[note['start']]+=1
        for key in span:
            if key not in attacks:continue
            if note['type']==2:holds[key].append((note['start'],note['end']))
            insort(attacks[key],note['start'])
            if len(span)>1:insort(wide_near[key],note['start'])
    retain_sky=_retain_sky(notes,movable,source_notes,limits)
    result=[];blocked=[];moved_sky={key:None for key in range(1,5)}
    for note in sorted(movable,key=lambda n:(n['start'],n['side'],n.get('id',0))):
        t=note['start'];group=note['side']-2;combined=sorted(side[0]+side[1])
        prior=side[group][bisect_left(side[group],t)-1] if bisect_left(side[group],t) else -10**9
        overloaded=(id(note) not in retain_sky or t-prior<limits['same_side_gap_ms'] or
            _window_count(combined,t-249,t)>=limits['combined_250_ms'] or
            _window_count(combined,t-499,t)>=limits['combined_500_ms'] or
            _window_count(combined,t-999,t)>=limits['combined_1000_ms'])
        if overloaded:
            key=_candidate_key(note,source_notes,attacks,holds,wide_near,chord,
                               moved_sky,profile,limits)
            if key is not None:
                result.append(dict(id=note.get('id'),source_event=note.get('_source_event'),
                                   time=t,from_side=note['side'],to_key=key))
                note.update(side=1,x0=[key,4],x1=[key,4],w0=[1,4],w1=[1,4])
                insort(attacks[key],t);chord[t]+=1;moved_sky[key]=t
                continue
            blocked.append(dict(id=note.get('id'),time=t,side=note['side'],
                                reason='no_available_central_key'))
        insort(side[group],t)
    return dict(status='rebalanced' if result else 'unchanged',
                moved=len(result),retained=len(movable)-len(result),blocked=blocked,
                moves=result,limits=limits.copy(),timing_preserved=True,
                source_floor_unchanged=True,
                target_sky_side_retained=len(retain_sky))
