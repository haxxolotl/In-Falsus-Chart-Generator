import pathlib,json,sys,collections,struct
import numpy as np
import pathlib
B=pathlib.Path(__file__).parent
R=B/'revision3'
def metrics(ns):
 k=sorted([n for n in ns if n['type'] in (1,2)],key=lambda n:n['start'])
 w=[max(1,round(n['w0'][0]/n['w0'][1]*4)) for n in k]
 lanes=[round(n['x0'][0]/n['x0'][1]*4) for n in k]
 bursts=[];run=[]
 for i,n in enumerate(k):
  if n['type']!=1 or w[i]!=1: 
   if len(run)>=3:bursts.append(run)
   run=[];continue
  if run and (lanes[i]!=lanes[run[-1]] or not 0<n['start']-k[run[-1]]['start']<=140):
   if len(run)>=3:bursts.append(run)
   run=[]
  run.append(i)
 if len(run)>=3:bursts.append(run)
 return dict(key_notes=len(k),fat_fraction=sum(x>1 for x in w)/max(1,len(w)),widths=dict(collections.Counter(w)),
    rapid_narrow_runs=len(bursts),rapid_narrow_notes=sum(len(x) for x in bursts),longest_run=max([len(x) for x in bursts] or [0]),
    burst_examples=[dict(start=k[x[0]]['start'],end=k[x[-1]]['start'],lane=lanes[x[0]],notes=len(x)) for x in sorted(bursts,key=len,reverse=True)[:5]])
