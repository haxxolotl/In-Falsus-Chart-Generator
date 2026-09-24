"""Fit smooth stock sine/cosine edge curves to source-guided cursor paths.

The shipped _lA._Yb (0x53BE20) evaluates each ribbon edge with linear,
sin(u*pi/2), or 1-cos(u*pi/2). Fit both edges independently, as stock does.
"""
import math,collections,copy
from .cursor_continuity import value
from .native_field_geometry import canonicalize, contain

def ease(u,mode):return u if mode==0 else math.sin(u*math.pi/2) if mode==1 else 1-math.cos(u*math.pi/2)
def edges(n,u):
 a=value(n,'x0');b=value(n,'x1');wa=value(n,'w0');wb=value(n,'w1');f=n.get('flags',0)
 ml=1 if f&8 else 2 if f&16 else 0;mr=1 if f&64 else 2 if f&128 else 0
 return a-wa/2+(b-wb/2-a+wa/2)*ease(u,ml),a+wa/2+(b+wb/2-a-wa/2)*ease(u,mr)

def fit_existing(notes,level):
 groups=collections.defaultdict(list)
 for n in notes:
  if n['type']==5:groups[n['group']].append(n)
 out=[n for n in notes if n['type']!=5];before=sum(map(len,groups.values()));curved=0;max_error=0
 limit=[1.15,1.45,1.7,2.3][level]
 for ns in groups.values():
  ns.sort(key=lambda n:n['start']);i=0
  while i<len(ns):
   best=None
   for j in range(i+1,min(len(ns),i+32)):
    a=ns[i]['start'];b=ns[j]['end'];span=b-a
    if span>2200:break
    if ns[j-1]['end']!=ns[j]['start']:break
    left0,right0=edges(ns[i],0);left1,right1=edges(ns[j],1)
    samples=[]
    for n in ns[i:j+1]:
     for u in (0,.25,.5,.75,1):samples.append(((n['start']+u*(n['end']-n['start'])-a)/span,edges(n,u)))
    modes=[];errors=[]
    for side,p0,p1 in [(0,left0,left1),(1,right0,right1)]:
     opts=[]
     for mode in (0,1,2):
      error=max(abs(p0+(p1-p0)*ease(u,mode)-p[side]) for u,p in samples)
      opts.append((error,mode))
     error,mode=min(opts);modes.append(mode);errors.append(error)
    if max(errors)>.012:continue
    peak=(abs(left1-left0)+abs(right1-right0))/2/(span/1000)*max(1 if m==0 else math.pi/2 for m in modes)
    if peak>limit+.015:continue
    best=(j,modes,max(errors))
   if best:
    j,modes,error=best;n=copy.deepcopy(ns[i]);n.update(end=ns[j]['end'],x1=ns[j]['x1'][:],w1=ns[j]['w1'][:],flags=(n.get('flags',0)&~252)|[4,8,16][modes[0]]|[32,64,128][modes[1]])
    out.append(n);curved+=any(modes);max_error=max(max_error,error);i=j+1
   else:out.append(ns[i]);i+=1
 out.sort(key=lambda n:(n['start'],n['type']!=5,n['side'],n.get('id',0)))
 for i,n in enumerate(out):n['id']=i
 contain(out);notes[:]=out
 return dict(before_segments=before,after_segments=sum(n['type']==5 for n in out),native_curved_segments=curved,max_edge_fit_error=max_error,judgment_and_render_use_same_native_curve=True)

def apply(notes,level):
 """Shape-preserving smooth paths, encoded with the game's actual edge curves.

 Keep real breaks and reversals. Simplify sub-pixel source jitter before fitting
 a C1 path; do not ease each tiny source segment separately into stop/start motion.
 """
 import numpy as np
 from scipy.interpolate import PchipInterpolator
 from .cursor_paths_v14 import rational
 # Guide rasters sometimes fragment one gesture into a burst of tiny islands.
 # Fill only near-identical reachable cracks, with actual visible geometry.
 fields=sorted((n for n in notes if n['type']==5),key=lambda n:n['start'])
 bridges=[];joined=0
 for a,b in zip(fields,fields[1:]):
  gap=b['start']-a['end']
  if 0<gap<=110 and abs(value(a,'x1')-value(b,'x0'))<=min(.12,gap/1000*[1.15,1.45,1.7,2.3][level]) and abs(value(a,'w1')-value(b,'w0'))<=.12:
   n=copy.deepcopy(a);n.update(start=a['end'],end=b['start'],x0=a['x1'][:],w0=a['w1'][:],x1=b['x0'][:],w1=b['w0'][:]);bridges.append(n);joined+=1
 notes.extend(bridges)
 from .cursor_continuity import regroup,islands
 regroup(notes)
 # A flash-sized field without any flick input has no useful tracking gesture.
 # Keep short fields that are the native target region for a real flick.
 tiny=set();removed=0
 for island in islands(notes):
  a=min(n['start'] for n in island);b=max(n['end'] for n in island)
  if b-a<100 and not any(n['type']==4 and a<=n['start']<=b for n in notes):
   tiny.update(id(n) for n in island);removed+=1
 notes[:]=[n for n in notes if id(n) not in tiny]
 groups=collections.defaultdict(list)
 for n in notes:
  if n['type']==5:groups[n['group']].append(n)
 out=[n for n in notes if n['type']!=5];before=sum(map(len,groups.values()));curved=0;max_error=0;max_speed=0
 limit=[1.15,1.45,1.7,2.3][level]
 for ns in groups.values():
  ns.sort(key=lambda n:n['start']);ts=np.array([ns[0]['start']]+[n['end'] for n in ns],dtype=float)
  xy=np.array([[value(ns[0],'x0'),value(ns[0],'w0')]]+[[value(n,'x1'),value(n,'w1')] for n in ns])
  keep={0,len(ts)-1}
  def simplify(a,b):
   if b-a<2:return
   u=(ts[a+1:b]-ts[a])/(ts[b]-ts[a]);delta=np.max(np.abs(xy[a+1:b]-(xy[a]+u[:,None]*(xy[b]-xy[a]))),axis=1)
   j=a+1+int(np.argmax(delta))
   if max(delta)>.018:keep.add(j);simplify(a,j);simplify(j,b)
  simplify(0,len(ts)-1);ix=sorted(keep);t=ts[ix];v=xy[ix]
  cx=PchipInterpolator(t,v[:,0]);cw=PchipInterpolator(t,v[:,1])
  probe=np.unique(np.concatenate([np.linspace(a,b,9) for a,b in zip(t,t[1:])]))
  peak=float(np.max(np.abs(cx.derivative()(probe))))*1000
  # The smoother may have a higher instantaneous speed than its source chord.
  # Contract only the motion amplitude, retaining timing, turns, and widths.
  scale=min(1,limit/max(peak,1e-9));center=float(np.mean(v[:,0]))
  def desired(ms):
   x=center+(cx(ms)-center)*scale;w=cw(ms)
   x=np.clip(x,w/2,1-w/2)
   return np.stack((x-w/2,x+w/2),axis=-1)
  def emit(a,b,depth=0):
   nonlocal curved,max_error,max_speed
   u=np.linspace(0,1,17);target=desired(a+(b-a)*u);p=target[0];q=target[-1];choices=[]
   for ml in range(3):
    for mr in range(3):
     left=p[0]+(q[0]-p[0])*np.array([ease(z,ml) for z in u]);right=p[1]+(q[1]-p[1])*np.array([ease(z,mr) for z in u])
     err=float(np.max(np.abs(np.stack((left,right),axis=-1)-target)))
     def derivative(mode):return np.ones_like(u) if mode==0 else np.cos(u*math.pi/2)*math.pi/2 if mode==1 else np.sin(u*math.pi/2)*math.pi/2
     speed=float(np.max(np.abs(((q[0]-p[0])*derivative(ml)+(q[1]-p[1])*derivative(mr))/2)))*1000/(b-a)
     if min(right-left)>=.28 and speed<=limit+.012:choices.append((err,ml,mr,speed))
   best=min(choices,default=(float('inf'),0,0,0))
   if (best[0]>.006 or b-a>2200) and b-a>=24 and depth<12:
    mid=round((a+b)/2);emit(a,mid,depth+1);emit(mid,b,depth+1);return
   if not choices:
    # At tiny intervals use the chord, whose mean velocity is bounded.
    best=(float(np.max(np.abs(p+u[:,None]*(q-p)-target))),0,0,abs(sum(q-p)/2)*1000/(b-a))
   err,ml,mr,speed=best;n=copy.deepcopy(ns[0])
   n.update(start=int(a),end=int(b),x0=rational(sum(p)/2),x1=rational(sum(q)/2),w0=rational(p[1]-p[0]),w1=rational(q[1]-q[0]),flags=(n.get('flags',0)&~252)|[4,8,16][ml]|[32,64,128][mr])
   out.append(n);curved+=bool(ml or mr);max_error=max(max_error,err);max_speed=max(max_speed,speed)
  # Keep genuine source turns as anchors; the interpolator has zero velocity
  # at extrema, so adjacent fitted arcs approach the turn rather than snap.
  for a,b in zip(t,t[1:]):emit(int(a),int(b))
 out.sort(key=lambda n:(n['start'],n['type']!=5,n['side'],n.get('id',0)))
 for i,n in enumerate(out):n['id']=i
 contain(out);notes[:]=out
 return dict(before_segments=before,after_segments=sum(n['type']==5 for n in out),native_curved_segments=curved,max_edge_fit_error=max_error,maximum_cursor_speed=max_speed,speed_limit=limit,near_identical_cracks_visibly_bridged=joined,empty_sub100ms_flashes_removed=removed,other_gaps_preserved=True,judgment_and_render_use_same_native_curve=True)
