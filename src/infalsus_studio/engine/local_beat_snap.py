"""Small, local beat corrections against the encoded musical clock.

Never estimates an offset or changes a tempo. Curved field tessellation is
warped continuously between musical anchors rather than snapped individually.
"""
import bisect, collections, struct
from .cursor_continuity import islands

def snap_chart(chart):
    ns=chart['notes']
    events=[];origins=[]
    for v in struct.iter_unpack('<qiidff',bytes.fromhex(chart['timing_data_hex'])):
        if v[2] not in (1,2):continue
        origin=v[1]
        if v[2]==2 and events:
            old_beat=60000/events[-1][1];fraction=((v[1]-origins[-1])/old_beat)%1
            if fraction>1-1e-8:fraction=0
            origin=v[1]-fraction*60000/v[3]
        events.append((v[1],v[3]));origins.append(origin)
    if not events:return {'changed_times':0,'reason':'no encoded musical clock'}
    starts=[t for t,b in events]
    def nearest(t):
        j=max(0,bisect.bisect_right(starts,t)-1)
        _,bpm=events[j];origin=origins[j];beat=60000/bpm
        # Sixteenth-note grid; triplet subdivisions only when they are nearer.
        candidates=[origin+round((t-origin)/beat*d)*beat/d for d in (1,2,3,4,6,8)]
        goal=min(candidates,key=lambda x:abs(x-t))
        tolerance=min(12,beat*.025)
        return round(goal) if abs(goal-t)<=tolerance else t
    anchors={n['start'] for n in ns if n['type'] in (1,2,4)}
    anchors.update(n['end'] for n in ns if n['type']==2)
    for group in islands(ns):
        anchors.add(min(n['start'] for n in group));anchors.add(max(n['end'] for n in group))
    original=sorted(anchors);mapped={t:nearest(t) for t in original}
    # Do not collapse intentional flams, shorten holds, or reverse time.
    for a,b in zip(original,original[1:]):
        if mapped[b]-mapped[a]<min(3,b-a):mapped[a]=a;mapped[b]=b
    # Include every field waypoint in the same continuous time warp. This
    # preserves shared endpoints, visible gaps and tap/flick/field alignment.
    def warp(t):
        if t in mapped:return mapped[t]
        j=bisect.bisect_right(original,t)
        if j==0 or j==len(original):return t
        a,b=original[j-1:j+1]
        return round(mapped[a]+(t-a)/(b-a)*(mapped[b]-mapped[a]))
    alltimes=sorted({n[k] for n in ns for k in ('start','end')})
    result={t:warp(t) for t in alltimes}
    # Very short geometry spans need strict ordering too. Reject the affected
    # local anchors and recalculate, rather than quantizing them to zero length.
    for _ in range(3):
        bad=[(a,b) for a,b in zip(alltimes,alltimes[1:]) if result[b]<=result[a]]
        if not bad:break
        for a,b in bad:
            lo=max(0,bisect.bisect_left(original,a)-1);hi=min(len(original),bisect.bisect_right(original,b)+1)
            for t in original[lo:hi]:mapped[t]=t
        result={t:warp(t) for t in alltimes}
    assert all(result[b]>result[a] for a,b in zip(alltimes,alltimes[1:]))
    changes=[result[t]-t for t in alltimes if result[t]!=t]
    assert all(abs(v)<=12 for v in changes)
    for n in ns:
        n['start']=result[n['start']];n['end']=result[n['end']]
    ns.sort(key=lambda n:(n['start'],n['id']))
    return dict(changed_times=len(changes),maximum_ms=max(map(abs,changes),default=0),mean_ms=sum(changes)/max(1,len(changes)),early=sum(v<0 for v in changes),late=sum(v>0 for v in changes),clock_changed=False,offset_fitted=False,time_map={str(t):result[t] for t in alltimes if t!=result[t]})
