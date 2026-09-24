"""Tier width and readable target allocation before native encoding."""
import itertools


def apply(notes, level):
    keys=lambda n: set(range(round(n['x0'][0]/n['x0'][1]*4),
        round((n['x0'][0]/n['x0'][1]+n['w0'][0]/n['w0'][1])*4)))
    rows=sorted((n for n in notes if n['type'] in (1,2)),key=lambda n:(n['start'],n['end']))
    last=[-10000]*6; busy=[-1]*6; changed=0
    for t,group in itertools.groupby(rows,key=lambda n:n['start']):
        group=list(group);used=set()
        for n in sorted(group,key=lambda n:len(keys(n))):
            original=keys(n); wide=len(original)>1
            overlaps=any(original & keys(other) for other in group if other is not n)
            if wide and (level<3 or overlaps):
                free=[k for k in range(6) if k not in used and busy[k]<=t]
                reserved=set().union(*(keys(o) for o in group if o is not n and len(keys(o))==1))
                choices=[k for k in free if k not in reserved]
                if not choices:raise ValueError(f'Unplayable keyboard overlap at {t} ms')
                k=min(choices,key=lambda k:(k not in original,k in (0,5),last[k],abs(k-sum(original)/len(original))))
                n.update(side=2 if k==0 else 3 if k==5 else 1,x0=[k,4],x1=[k,4],w0=[1,4],w1=[1,4])
                original={k};changed+=1
            available=[k for k in original if k not in used and busy[k]<=t]
            if not available:raise ValueError(f'Unplayable keyboard overlap at {t} ms')
            chosen=min(available,key=lambda k:last[k]);used.add(chosen);last[chosen]=t
            if n['type']==2:busy[chosen]=n['end']
    return dict(narrowed_targets=changed,physical_assignment_checked=True)
