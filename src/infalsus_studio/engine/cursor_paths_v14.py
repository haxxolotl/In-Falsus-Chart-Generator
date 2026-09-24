"""One continuous reachable ribbon per cursor island, with native group endpoints.

LogicalNotePlayer._Pb sets 0x2000 on a group's first field and 0x4000 on
its last. A shared group alone cannot make separated geometry continuous.
"""
import collections, copy
from fractions import Fraction
from .cursor_continuity import value, islands
from .native_field_geometry import canonicalize

def rational(x):
 f=Fraction(float(x)).limit_denominator(1024);return [f.numerator,f.denominator]

def audit(notes):
 fields=sorted((n for n in notes if n['type']==5),key=lambda n:(n['start'],n['end']))
 groups=collections.defaultdict(list)
 for n in fields:groups[n['group']].append(n)
 errors=[]
 for group,ns in groups.items():
  for a,b in zip(ns,ns[1:]):
   if a['end']!=b['start']:errors.append(dict(group=group,time=b['start'],kind='gap_or_overlap',delta=b['start']-a['end']))
   for axis in ('x','w'):
    if Fraction(*a[axis+'1'][:2])!=Fraction(*b[axis+'0'][:2]):errors.append(dict(group=group,time=b['start'],kind=axis+'_discontinuity'))
 return dict(fields=len(fields),groups=len(groups),errors=errors)

def repair(notes,level):
 before=audit(notes);out=[n for n in notes if n['type']!=5];overlaps=0;welded=0
 for gi,group in enumerate(islands(notes)):
  # All interval boundaries are retained. The source can have two simultaneous
  # finger arcs; the target has one cursor, so form one path, not two demands.
  times=sorted({t for n in group for t in (n['start'],n['end'])})
  intervals=[]
  for a,b in zip(times,times[1:]):
   active=[n for n in group if n['start']<b and n['end']>a]
   if not active: # only a one-ms rounding crack inside this island
    continue
   overlaps+=len(active)>1
   def point(t):
    vs=[]
    for n in active:
     u=min(1,max(0,(t-n['start'])/(n['end']-n['start'])))
     vs.append(tuple(value(n,k+'0')+(value(n,k+'1')-value(n,k+'0'))*u for k in ('x','w')))
    # Follow the most sustained source role through concurrent arcs. Averaging
    # symmetric arcs would erase the intended movement into a central stripe.
    chosen=max(range(len(active)),key=lambda i:(active[i]['end']-active[i]['start'],-active[i].get('_source_event',0)))
    x,w=vs[chosen];return x,min(.62,max(.30,w))
   intervals.append((a,b,point(a),point(b),active[0]))
  if not intervals:continue
  # Collapse rounding-only slivers into the preceding interval, with no added
  # input requirement. Every shared waypoint is literally the same rational.
  pieces=[]
  for a,b,p,q,n in intervals:
   if pieces and b-a<2:
    pieces[-1][1]=b;pieces[-1][3]=q;continue
   if b-a<2:continue
   if pieces and a-pieces[-1][1]<=1:
    a=pieces[-1][1];prev=pieces[-1][3];joint=((prev[0]+p[0])/2,(prev[1]+p[1])/2)
    pieces[-1][3]=joint;p=joint;welded+=1
   pieces.append([a,b,p,q,n])
  # Limit both continuous velocity and seam jumps as one path. This projection
  # preserves times, reversals and long-form shape instead of flattening widths.
  if not pieces:continue
  pts=[pieces[0][2]]+[p[3] for p in pieces];ts=[pieces[0][0]]+[p[1] for p in pieces]
  w=[p[1] for p in pts];x=[min(1-wi/2,max(wi/2,p[0])) for p,wi in zip(pts,w)];speed=[1.15,1.45,1.7,2.3][level]
  for _ in range(12):
   for indexes in (range(len(x)-1),range(len(x)-2,-1,-1)):
    for i in indexes:
     lim=speed*(ts[i+1]-ts[i])/1000;d=x[i+1]-x[i];extra=max(0,abs(d)-lim)/2
     if extra:sgn=1 if d>0 else -1;x[i]+=extra*sgn;x[i+1]-=extra*sgn
  for i in range(len(x)-1):
   lim=speed*(ts[i+1]-ts[i])/1000;x[i+1]=min(x[i]+lim,max(x[i]-lim,x[i+1]))
  for i,p in enumerate(pieces):
   n=copy.deepcopy(p[4]);n.update(start=ts[i],end=ts[i+1],group=('cursor_v14',gi),x0=rational(x[i]),x1=rational(x[i+1]),w0=rational(w[i]),w1=rational(w[i+1]))
   out.append(n)
 out.sort(key=lambda n:(n['start'],n['type']!=5,n['side'],n.get('id',0)))
 ids={}
 for i,n in enumerate(out):
  token=n['group'] if n['type']==5 else ('input',n['group']);n['group']=ids.setdefault(token,len(ids));n['id']=i
 canonicalize(out);after=audit(out);assert not after['errors'],after['errors'][:3]
 notes[:]=out
 return dict(before_errors=len(before['errors']),after=after,overlapping_source_intervals_collapsed=overlaps,exact_waypoints_welded=welded)
