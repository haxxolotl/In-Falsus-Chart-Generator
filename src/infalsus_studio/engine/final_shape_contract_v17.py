"""Check final geometry after every motion adjustment, without flattening valid arcs."""
from .native_curve_fit_v14 import edges
from .cursor_motion_v17 import peak

def repair(notes):
 changes=[]
 for n in notes:
  if n['type']==2 and n['end']==n['start']:
   n['type']=1;changes.append(dict(id=n['id'],repair='clipped zero-duration hold becomes tap'))
  if n['type']!=5:continue
  samples=[edges(n,i/64) for i in range(65)]
  if min(r-l for l,r in samples)>=.139:continue
  options=[]
  for f in (72,144,36):
   p=dict(n,flags=(n.get('flags',0)&~252)|f)
   if peak(p)>n.get('_motion_limit',1.0)+.005:continue
   err=sum((a-b)**2 for i,old in enumerate(samples) for a,b in zip(old,edges(p,i/64)))
   options.append((err,p['flags']))
  assert options,('no width-safe motion-compatible easing',n)
  n['flags']=min(options)[1];changes.append(dict(id=n['id'],repair='preserve endpoint widths through curved interpolation'))
 return changes
