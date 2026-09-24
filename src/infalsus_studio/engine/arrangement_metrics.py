"""Physical repetition and sustained-gesture measures shared by study and checks."""
import collections
import numpy as np
from .learn_phrase_library import chart_metrics,union_duration

def lane(n):return round(n['x0'][0]/n['x0'][1]*4)
def width(n):return max(1,round(n['w0'][0]/n['w0'][1]*4))
def jack_runs(ns,gap=180):
 out=[]
 attacks=sorted((n for n in ns if n['type'] in (1,2) and width(n)==1),key=lambda n:n['start'])
 def alternating(a,b,k):
  return b['start']-a['start']>=100 and any(a['start']<x['start']<b['start'] and lane(x)!=k for x in attacks)
 for k in range(6):
  run=[]
  for n in sorted((n for n in ns if n['type'] in (1,2) and n['end']-n['start']<=250 and width(n)==1 and lane(n)==k),key=lambda n:n['start']):
   if run and (n['start']-run[-1]['start']>gap or alternating(run[-1],n,k)):
    if len(run)>=3:out.append(run)
    run=[]
   run.append(n)
  if len(run)>=3:out.append(run)
 return out
def extended(ns,duration):
 m=chart_metrics(ns,duration);keys=[n for n in ns if n['type'] in (1,2)];fields=[n for n in ns if n['type']==5]
 times=sorted(n['start']/1000 for n in keys);peak=max((np.searchsorted(times,t+1)-i for i,t in enumerate(times)),default=0)
 holds=[(n['end']-n['start'])/1000 for n in keys if n['type']==2];spans=[];end=-1;start=0
 for n in sorted(fields,key=lambda n:n['start']):
  if n['start']>end+100:
   if end>=0:spans.append((end-start)/1000)
   start=n['start']
  end=max(end,n['end'])
 if end>=0:spans.append((end-start)/1000)
 m.update(key_peak_1s=int(peak),unique_density=len({n['start'] for n in ns if n['type'] in (1,2,4)})/m['span'],jack_runs=len(jack_runs(ns)),hold_p90=float(np.quantile(holds,.9)) if holds else 0,field_span_p90=float(np.quantile(spans,.9)) if spans else 0,hold_seconds=sum(holds),sustained_field_seconds=sum(x for x in spans if x>=1.5),fat_fraction=sum(width(n)>1 for n in keys)/max(1,len(keys)))
 return m
