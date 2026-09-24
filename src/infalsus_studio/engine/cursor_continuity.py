"""Match stock semantics: one logical field group per visible continuous island."""
import collections
from fractions import Fraction

def value(n,k):return n[k][0]/n[k][1]
def rational(x):
    f=Fraction(float(x)).limit_denominator(4096);return [f.numerator,f.denominator]

def islands(notes):
    result=[];end=-1
    for n in sorted((n for n in notes if n['type']==5),key=lambda n:(n['start'],n['end'])):
        if n['start']>end+1:result.append([])
        result[-1].append(n);end=max(end,n['end'])
    return result

def regroup(notes):
    before=collections.defaultdict(list)
    for n in notes:
        if n['type']==5:before[n['group']].append(n)
    old_gaps=sum(b['start']>a['end']+1 for g in before.values() for a,b in zip(sorted(g,key=lambda n:n['start']),sorted(g,key=lambda n:n['start'])[1:]))
    fs=sorted((n for n in notes if n['type']==5),key=lambda n:n['start'])
    joins=sum(b['start']<=a['end']+1 and a['group']!=b['group'] for a,b in zip(fs,fs[1:]))
    for i,group in enumerate(islands(notes)):
        for n in group:n['_continuity_group']=('cursor',i)
    groups={}
    for i,n in enumerate(notes):
        key=n.pop('_continuity_group',('other',n['group']))
        n['group']=groups.setdefault(key,len(groups));n['id']=i
    return {'internal_entrances_removed':joins,'invisible_group_gaps_split':old_gaps,'visible_cursor_islands':len(islands(notes))}

def check(notes):
    fs=sorted((n for n in notes if n['type']==5),key=lambda n:(n['start'],n['end']))
    seen=set()
    for group in islands(notes):
        ids={n['group'] for n in group};assert len(ids)==1, 'entrance inside continuous zone'
        assert not (ids&seen), 'one cursor group spans an invisible gap';seen|=ids
    return {'islands':len(seen),'internal_entrances':0,'invisible_judgment_gaps':0}

def soften_motion(notes,max_speed):
    """Project source-arc waypoints to a speed limit; preserve timing and widths."""
    changed=0;old_max=0;new_max=0
    for group in islands(notes):
        if not all(n.get('_source_type')=='arc' for n in group):continue
        old_max=max(old_max,max(abs(value(n,'x1')-value(n,'x0'))/max(.001,(n['end']-n['start'])/1000) for n in group))
        if all(abs(value(n,'x1')-value(n,'x0'))<=max_speed*(n['end']-n['start'])/1000+1e-8 for n in group):continue
        points=collections.defaultdict(list)
        for n in group:points[n['start']].append(value(n,'x0'));points[n['end']].append(value(n,'x1'))
        times=sorted(points);x=[sum(points[t])/len(points[t]) for t in times];limits=[max_speed*(b-a)/1000 for a,b in zip(times,times[1:])]
        for _ in range(24):
            for indices in [range(len(x)-1),range(len(x)-2,-1,-1)]:
                for i in indices:
                    delta=x[i+1]-x[i];excess=max(0,abs(delta)-limits[i])/2
                    if excess:
                        sign=1 if delta>0 else -1;x[i]+=sign*excess;x[i+1]-=sign*excess
        for i in range(len(x)-1):x[i+1]=min(x[i]+limits[i],max(x[i]-limits[i],x[i+1]))
        mapped=dict(zip(times,x))
        for n in group:
            for key,t in [('x0',n['start']),('x1',n['end'])]:
                if abs(value(n,key)-mapped[t])>1e-7:n[key]=rational(mapped[t]);changed+=1
    for n in notes:
        if n['type']==5 and n.get('_source_type')=='arc':new_max=max(new_max,abs(value(n,'x1')-value(n,'x0'))/max(.001,(n['end']-n['start'])/1000))
    assert new_max<=max_speed+.002,(new_max,max_speed)
    return {'changed_endpoints':changed,'maximum_before':old_max,'maximum_after':new_max,'limit':max_speed}
