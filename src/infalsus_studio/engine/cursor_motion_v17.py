"""Bound actual native curved cursor velocity, after geometric containment."""
import math,collections
from fractions import Fraction
from .native_field_geometry import contain
from .native_curve_fit_v14 import edges
LIMITS=(1.15,1.45,1.7,2.3)
def value(n,k):return n[k][0]/n[k][1]
def modes(n):
 f=n.get('flags',0);return (1 if f&8 else 2 if f&16 else 0,1 if f&64 else 2 if f&128 else 0)
def peak(n):
 dx=value(n,'x1')-value(n,'x0');dw=value(n,'w1')-value(n,'w0');t=(n['end']-n['start'])/1000
 c=a=b=0.
 for amp,mode in zip(((dx-dw/2)/(2*t),(dx+dw/2)/(2*t)),modes(n)):
  if mode==0:c+=amp
  elif mode==1:a+=amp*math.pi/2
  else:b+=amp*math.pi/2
 ts=[0,math.pi/2];theta=math.atan2(b,a)
 for z in (theta,theta+math.pi,theta-math.pi):
  if 0<z<math.pi/2:ts.append(z)
 return max(abs(c+a*math.cos(z)+b*math.sin(z)) for z in ts)
def delta_range(n,limit):
 ml,mr=modes(n);t=(n['end']-n['start'])/1000;dw=value(n,'w1')-value(n,'w0');lo=-100.;hi=100.
 for i in range(65):
  theta=i/64*math.pi/2
  d=lambda mode:1 if mode==0 else math.pi/2*math.cos(theta) if mode==1 else math.pi/2*math.sin(theta)
  dl,dr=d(ml),d(mr);co=(dl+dr)/2;bias=dw*(dr-dl)/4
  if co>1e-10:lo=max(lo,(-limit*t-bias)/co);hi=min(hi,(limit*t-bias)/co)
 return lo,hi
def rational(x):
 f=Fraction(float(x)).limit_denominator(65536);return [f.numerator,f.denominator]
def apply(notes,level):
 groups=collections.defaultdict(list)
 for n in notes:
  if n['type']==5:groups[n['group']].append(n)
 changed=0;mode_changes=0;before=0;after=0
 for ns in groups.values():
  ns.sort(key=lambda n:n['start']);maximum=max(map(peak,ns));before=max(before,maximum)
  if all(peak(n)<=n.get('_motion_limit',LIMITS[level])+.005 for n in ns):after=max(after,maximum);continue
  changed+=1;ranges=[]
  for n in ns:
   limit=n.get('_motion_limit',LIMITS[level])*.99
   lo,hi=delta_range(n,limit)
   if not lo<=0<=hi:
    # Strong independent edge easing can move the center even when x0=x1.
    # Keep the nearest shared easing in this segment, retaining curved shape.
    old=[edges(n,i/16) for i in range(17)];options=[]
    for mode in range(3):
     probe=dict(n,flags=(n.get('flags',0)&~252)|(4,8,16)[mode]|(32,64,128)[mode]);err=max(abs(a-b) for i,p in enumerate(old) for a,b in zip(p,edges(probe,i/16)));options.append((err,probe['flags']))
    n['flags']=min(options)[1];mode_changes+=1;lo,hi=delta_range(n,limit)
   ranges.append((lo,hi))
  widths=[value(ns[0],'w0')]+[value(n,'w1') for n in ns]
  wanted=[value(ns[0],'x0')]+[value(n,'x1') for n in ns]
  feasible=[[w/2,1-w/2] for w in widths]
  for i in range(len(ns)-1,-1,-1):
   lo,hi=ranges[i];feasible[i]=[max(feasible[i][0],feasible[i+1][0]-hi),min(feasible[i][1],feasible[i+1][1]-lo)];assert feasible[i][0]<=feasible[i][1]+1e-9
  x=[min(feasible[0][1],max(feasible[0][0],wanted[0]))]
  for i,(lo,hi) in enumerate(ranges):
   low=max(feasible[i+1][0],x[-1]+lo);high=min(feasible[i+1][1],x[-1]+hi);x.append(min(high,max(low,wanted[i+1])))
  positions=list(map(rational,x))
  for i,n in enumerate(ns):n['x0']=positions[i][:];n['x1']=positions[i+1][:]
  contain(ns);maximum=max(map(peak,ns));assert all(peak(n)<=n.get('_motion_limit',LIMITS[level])+.02 for n in ns);after=max(after,maximum)
 return dict(groups_adjusted=changed,edge_mode_changes=mode_changes,peak_before=before,peak_after=after,default_limit=LIMITS[level],contextual_limits_used=True,timing_unchanged=True)
