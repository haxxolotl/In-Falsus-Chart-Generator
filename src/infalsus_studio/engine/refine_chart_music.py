"""Focused v4 corrections: native clock, musical attacks and a coherent mouse layer."""
import pathlib,sys,json,copy,struct,hashlib,argparse,collections
import numpy as np
from scipy import ndimage
from fractions import Fraction
from .musical_grid import grid,preview,ticks,timing_hex
from .arrangement_policy import finalize,rating
from .arrangement_metrics import extended
from .chart_encoder import Encoder
from .chart_emulator import Decoder
from .generate_arranged_charts import verify
from .prepare_audio import sha,save
import pathlib
B=pathlib.Path(__file__).parent
R=B/'revision5';O=B.parent/'outputs/arcaea-custom-pack-v5'
FOCUS=['Altale','Antagonism','Astral Quantization (Chart View)','Breach of Faith','Cataclysm Cry']
def frac(x):
 f=Fraction(float(x)).limit_denominator(4096);return [f.numerator,f.denominator]
def value(n,k):return n[k][0]/n[k][1]
def foreground(t,f):
 chroma=ndimage.uniform_filter1d(f[:,12:24],size=5,axis=0);change=np.abs(chroma-np.roll(chroma,4,axis=0)).sum(axis=1)
 return ndimage.maximum_filter1d((.5*f[:,3]+.25*f[:,2]+.25*f[:,1])*(.6+np.minimum(change*3,1.5))*np.sqrt(np.minimum(f[:,5],1.5)),size=3)
def retarget(ns,t,f,g):
 score=foreground(t,f);pulses=ticks(g,float(t[-1]),4);groups=collections.defaultdict(list)
 for n in ns:
  if n['type'] in (1,2) and 'sustain_locked' not in n:groups[n['start']].append(n)
 changes=[];remove=set();bars=g['bars']+[float(t[-1])]
 for a,b in zip(bars,bars[1:]):
  old=sorted(ms for ms in groups if a*1000<=ms<b*1000)
  if not old:continue
  cand=pulses[(pulses>=a)&(pulses<b-.001)];s=np.interp(cand,t,score);valid=np.flatnonzero(s>=max(.08,np.quantile(score,.55)))
  selected=sorted(valid[np.argsort(s[valid])[-min(len(old),len(valid)):]]) if len(valid) else []
  # Keep the ordered layout of each figure; change which audible attacks it follows.
  dest=cand[selected];chosen_old=np.linspace(0,len(old)-1,len(dest)).round().astype(int) if len(dest) else []
  chosen=set(chosen_old)
  for i,ms in enumerate(old):
   if i not in chosen:remove.update(id(n) for n in groups[ms])
  for i,new in zip(chosen_old,dest):
   ms=old[i];new=round(new*1000)
   for n in groups[ms]:
    duration=n['end']-n['start'];n['start']=new;n['end']=new if n['type']==1 else min(round(b*1000)-1,new+duration)
    if n['end']-n['start']<100:n['type']=1;n['end']=new
   changes.append(dict(before=ms,after=new,foreground_before=float(np.interp(ms/1000,t,score)),foreground_after=float(np.interp(new/1000,t,score))))
 return [n for n in ns if id(n) not in remove],dict(retimed_moments=len(changes),removed_targets=len(remove),foreground_before=float(np.mean([x['foreground_before'] for x in changes])),foreground_after=float(np.mean([x['foreground_after'] for x in changes])),changes=changes)
def expressive_middle(ns,t,f,g):
 bars=np.array(g['bars']);candidates=[(bars[i],bars[i+8]) for i in range(len(bars)-8) if .3*t[-1]<bars[i]<.64*t[-1]]
 a,b=max(candidates,key=lambda z:float(np.mean((f[:,8]*(.4+f[:,10]*4)+.25*f[:,3])[(t>=z[0])&(t<z[1])])))
 a=round(a*1000);b=round(b*1000);kept=[]
 for n in ns:
  if n['type']!=5 or n['end']<=a or n['start']>=b:kept.append(n);continue
  for start,end in [(n['start'],min(a,n['end'])),(max(b,n['start']),n['end'])]:
   if end<=start:continue
   m=copy.deepcopy(n)
   for k in ['x','w']:
    u=value(n,k+'0');v=value(n,k+'1');m[k+'0']=frac(np.interp(start,[n['start'],n['end']],[u,v]));m[k+'1']=frac(np.interp(end,[n['start'],n['end']],[u,v]))
   m.update(start=start,end=end,flags=0);kept.append(m)
 anchors=[x for x in bars if a<=round(x*1000)<=b];count=0
 for i,(start,end) in enumerate(zip(anchors,anchors[1:])):
  beat=(end-start)/4;pattern=[.3,.5,.72,.5] if i%2==0 else [.7,.5,.28,.5]
  for j in range(3):
   kept.append(dict(type=5,side=4,start=round((start+j*beat)*1000),end=round((start+(j+1)*beat)*1000),x0=frac(pattern[j]),x1=frac(pattern[j+1]),w0=frac(.42),w1=frac(.42),flags=0,_group='cat-middle:'+str(i)));count+=1
 return kept,dict(start=a/1000,end=b/1000,field_segments=count,design='Eight measures of beat-aligned sweeps, alternating mirrored phrases and a beat of breathing room; wide cursor zones, no added tap density')
def mouse_fix(ns):
 fields=[n for n in ns if n['type']==5];changes=[]
 for n in ns:
  if n['type']!=4:continue
  active=[h for h in fields if h['start']-80<=n['start']<=h['end']+80]
  if not active:continue
  ongoing=[h for h in active if h['start']<=n['start']<=h['end']]
  if ongoing:active=ongoing
  h=min(active,key=lambda h:abs(value(n,'x0')-(value(h,'x0')+value(h,'x1'))/2));lo=min(value(h,'x0'),value(h,'x1'));hi=max(value(h,'x0'),value(h,'x1'))
  # Cover the entire source path, so unknown native easing cannot put the flick outside tracking.
  left=max(0,lo-.08);right=min(1,hi+.08);center=(left+right)/2;width=max(value(n,'w0'),right-left)
  before=[n['x0'],n['w0']];n['x0']=n['x1']=frac(center);n['w0']=n['w1']=frac(width)
  changes.append(dict(time=n['start'],before=before,field_start=h['start'],field_end=h['end'],path_min=lo,path_max=hi))
 return changes
