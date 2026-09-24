"""Phrase-load fit that retains salient source attacks at their authored times."""

from __future__ import annotations

import bisect
import collections
import hashlib
import math
import struct


def _onsets(audio_path):
    if audio_path is None:
        return None
    import numpy as np
    import soundfile as sf

    samples, rate = sf.read(audio_path, dtype='float32')
    if samples.ndim == 2:
        samples = np.mean(samples, axis=1)
    hop = max(1, round(rate*.02))
    samples = samples[:len(samples)//hop*hop]
    rms = np.sqrt(np.mean(samples.reshape(-1,hop)**2,axis=1))
    before = np.r_[rms[0],rms[:-1]]
    novelty = np.maximum(0,np.log1p(100*rms)-np.log1p(100*before))
    norm = max(.01,float(np.quantile(novelty,.95)))
    return novelty/norm, hop/rate


def _score(notes, chart, audio_path):
    import numpy as np

    audio = _onsets(audio_path)
    floors = sorted(((i,n) for i,n in enumerate(notes) if n['type']==1 and n.get('_source_type')=='floor'),
                    key=lambda item:(item[1]['start'],item[0]))
    by_time=collections.Counter(n['start'] for _,n in floors)
    times=[n['start'] for _,n in floors]
    controls=sorted((r[1],r[3]) for r in struct.iter_unpack('<qiidff',bytes.fromhex(chart['timing_data_hex']))
                    if r[2] in (1,2) and r[3]>0)
    control_times=[r[0] for r in controls]
    scores={}
    for j,(index,note) in enumerate(floors):
        t=note['start']
        previous=floors[j-1][1] if j else None
        following=floors[j+1][1] if j+1<len(floors) else None
        ci=bisect.bisect_right(control_times,t)-1
        if ci>=0:
            origin,bpm=controls[ci]
            phase=((t-origin)*bpm/60000)%1
            accent=max(0,1-8*min(phase,1-phase))
            half=max(0,1-8*abs(phase-.5))
        else:
            accent=half=0
        sound=0
        if audio is not None:
            novelty,hop=audio
            at=round(t/1000/hop)
            low=max(0,at-2);high=min(len(novelty),at+3)
            if high>low:sound=float(np.max(novelty[low:high]))
        lane=round(4*note['x0'][0]/note['x0'][1])
        repeat=bool(previous and previous['start']>=t-120 and
                    round(4*previous['x0'][0]/previous['x0'][1])==lane)
        change=bool(previous and previous['start']>=t-180 and
                    round(4*previous['x0'][0]/previous['x0'][1])!=lane)
        gap=bool(previous is None or t-previous['start']>=260 or
                 following is None or following['start']-t>=260)
        simultaneous=by_time[t]
        jitter=int.from_bytes(hashlib.blake2s(str(note.get('_source_event',index)).encode(),digest_size=2).digest(),'little')/65535
        scores[index]=(2*min(1,sound)+1.7*accent+.7*half+1.0*change+
                       1.4*gap-.9*repeat-.3*max(0,simultaneous-2)+.01*jitter)
    return scores


def fit(chart, *, target_density, max_four_seconds, max_one_second, audio_path=None, source_type='floor'):
    """Remove weak subdivisions until local and whole-phrase load are readable.

    Notes kept retain exact source times and lanes.  Holds, sky accents, side
    hits, flicks, and every field segment are never eligible for removal.
    """
    notes=chart['notes']
    scores=_score(notes,chart,audio_path)
    eligible={i for i,n in enumerate(notes) if n['type']==1 and n.get('_source_type')==source_type}
    active=set(range(len(notes)))
    attacks=sorted((n['start'],i) for i,n in enumerate(notes) if n['type'] in (1,2))
    if not attacks:
        return dict(removed=0,remaining=0)
    span=(max(n['end'] for n in notes)-min(n['start'] for n in notes))/1000
    target_total=round(target_density*span)

    def remove_lowest(window):
        candidates=[i for _,i in window if i in active and i in eligible]
        if not candidates:
            raise ValueError('Load target requires deleting a protected note')
        victim=min(candidates,key=lambda i:scores[i])
        active.remove(victim)

    # Every possible overflowing window begins at an attack timestamp.
    for seconds,cap in ((4,max_four_seconds),(1,max_one_second)):
        times=[t for t,_ in attacks]
        for pos,(start,_) in enumerate(attacks):
            end=bisect.bisect_left(times,start+seconds*1000,pos)
            window=attacks[pos:end]
            while sum(i in active for _,i in window)>cap:
                remove_lowest(window)
    while sum(i in active for _,i in attacks)>target_total:
        # Thin the busiest remaining four-second phrase first, and prefer its
        # weakest source attack.  This preserves quiet phrase punctuation.
        times=[t for t,_ in attacks]
        ranked=[]
        for pos,(start,_) in enumerate(attacks):
            end=bisect.bisect_left(times,start+4000,pos)
            window=attacks[pos:end]
            count=sum(i in active for _,i in window)
            if any(i in active and i in eligible for _,i in window):
                ranked.append((count,pos,end))
        _,pos,end=max(ranked)
        remove_lowest(attacks[pos:end])

    before=len(notes)
    removed_events=sorted((notes[i].get('_source_event'),notes[i]['start']) for i in eligible-active)
    chart['notes']=[n for i,n in enumerate(notes) if i in active]
    groups={}
    for i,n in enumerate(chart['notes']):
        original=n['group']
        n['id']=i;n['group']=groups.setdefault(original,i)
    times=[n['start'] for n in chart['notes'] if n['type'] in (1,2)]
    peak=max((bisect.bisect_left(times,t+1000,i)-i for i,t in enumerate(times)),default=0)
    bins=collections.Counter(t//1000 for t in times)
    return dict(before=before,after=len(chart['notes']),removed=len(removed_events),
                removed_source_events=removed_events,attacks_remaining=len(times),
                density_remaining=len(times)/span,peak_one_second=peak,
                seconds_with_15_attacks=sum(c>=15 for c in bins.values()),
                note_times_moved=0,held_or_flick_or_field_deleted=0)
