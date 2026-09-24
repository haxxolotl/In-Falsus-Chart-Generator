"""Official-conditioned attack budgets, whole sustain scenes and physical lane checks."""
import pathlib,copy,collections,functools,itertools
import numpy as np,joblib
from .learn_phrase_library import descriptor
from .arrangement_metrics import extended,jack_runs,lane,width
B=pathlib.Path(__file__).parent.resolve()
@functools.lru_cache(maxsize=1)
def learned():return joblib.load(B/'revision4/arrangement-model.joblib')
def keys(n):return list(range(lane(n),lane(n)+width(n)))
def place(n,k,w=1):n.update(side=2 if k==0 else 3 if k==5 else 1,x0=[k,4],x1=[k,4],w0=[w,4],w1=[w,4])

def repair_short_cursor_segments(ns):
 """Absorb native-short or faster-than-official cursor handoff pieces."""
 repairs=[]
 while True:
  n=next((x for x in ns if x['type']==5 and (x['end']-x['start']<=2 or abs(x['x1'][0]/x['x1'][1]-x['x0'][0]/x['x0'][1])/max(.001,(x['end']-x['start'])/1000)>22.5)),None)
  if n is None:break
  token=n.get('_group',n.get('group'))
  before=ns[:ns.index(n)];after=ns[ns.index(n)+1:]
  previous=next((x for x in reversed(before) if x['type']==5 and x.get('_group',x.get('group'))==token and x['end']==n['start']),None)
  following=next((x for x in after if x['type']==5 and x.get('_group',x.get('group'))==token and x['start']==n['end']),None)
  # A source-arc handoff may change logical groups across a rounded 0/1 ms
  # piece. Preserve continuous cursor coverage by absorbing that piece into
  # the touching gesture even when the authored group changes.
  previous=previous or next((x for x in reversed(before) if x['type']==5 and x['end']==n['start']),None)
  following=following or next((x for x in after if x['type']==5 and x['start']==n['end']),None)
  choices=[]
  if previous is not None:
   speed=abs(n['x1'][0]/n['x1'][1]-previous['x0'][0]/previous['x0'][1])/max(.001,(n['end']-previous['start'])/1000);choices.append((speed,'previous',previous))
  if following is not None:
   speed=abs(following['x1'][0]/following['x1'][1]-n['x0'][0]/n['x0'][1])/max(.001,(following['end']-n['start'])/1000);choices.append((speed,'following',following))
  choice=min(choices,default=(float('inf'),None,None))
  if choice[1]=='previous':
   previous['end']=n['end'];previous['x1']=list(n['x1']);previous['w1']=list(n['w1']);mode='absorbed_into_previous'
  elif choice[1]=='following':
   following['start']=n['start'];following['x0']=list(n['x0']);following['w0']=list(n['w0']);mode='absorbed_into_following'
  else:mode='removed_isolated_sub_2ms_segment'
  repairs.append(dict(start=n['start'],end=n['end'],group=token,mode=mode));ns.remove(n)
 return ns,repairs

class Arrangement:
 def __init__(self,t,f,duration,grid,origin,size):
  self.t=t;self.f=f;self.duration=duration;self.grid=grid;self.size=size;self.bank=learned();self.starts=np.arange(origin,duration-.6,size)
  self.X=np.stack([descriptor(t,f,s,min(duration-.5,s+size)) for s in self.starts]);self.pred={l:self.bank['models'][l].predict(self.X) for l in range(4)};self.last=[-10]*4
 def choose(self,rows,level,i,times):
  start=self.starts[i];end=min(self.duration-.5,start+self.size);pred=self.pred[level][i];budget=max(0,round(pred[0]*(end-start)))
  gap=[.24,.16,.095,.07][level];selected=[];beat=60/self.grid['bpm'];peak=max(1,int(np.ceil(pred[4])))
  def importance(r):
   tm=times[r[0]];off=abs((tm-self.grid['phase'])/beat-round((tm-self.grid['phase'])/beat))
   return r[1]*(1+.18*np.exp(-off*12))
  for r in sorted(rows,key=importance,reverse=True):
   tm=times[r[0]]
   if tm-self.last[level]<gap or any(abs(tm-times[q[0]])<gap for q in selected):continue
   trial=sorted([tm]+[times[q[0]] for q in selected])
   if any(np.searchsorted(trial,v+1)-j>peak for j,v in enumerate(trial)):continue
   if len(selected)>=budget:break
   selected.append(r)
  selected.sort(key=lambda r:times[r[0]])
  if selected:self.last[level]=times[selected[-1][0]]
  return selected,dict(start=float(start),end=float(end),predicted_moments=float(pred[0]*(end-start)),predicted_strikes=float(pred[1]*(end-start)),peak_strikes=max(1,int(np.ceil(pred[5]))),selected=len(selected))
 def chord_budget(self,ns,info,events,times):
  groups=collections.defaultdict(list)
  for n in ns:
   if n['type'] in (1,2,4):groups[n['start']].append(n)
  budget=max(len(groups),round(info['predicted_strikes']));strength={round(times[r[0]]*1000):r[1] for r in events};extra=[];keep=set()
  for ms,g in groups.items():
   g.sort(key=lambda n:(n['type']==2,n['side'] in (2,3),n['type']!=4),reverse=True);keep.add(id(g[0]))
   for n in g[1:]:extra.append((strength.get(ms,0)+(0.2 if n['type']==4 else 0),n))
  accepted=sorted(ms/1000 for ms in groups)
  for _,n in sorted(extra,key=lambda x:x[0],reverse=True):
   if len(keep)>=budget:break
   trial=sorted(accepted+[n['start']/1000])
   if any(np.searchsorted(trial,v+1)-j>info['peak_strikes'] for j,v in enumerate(trial)):continue
   keep.add(id(n));accepted=trial
  out=[n for n in ns if n['type']==5 or id(n) in keep];info['chord_targets_removed']=len(ns)-len(out);return out
 def sustain(self,ns,level):
  pred=self.pred[level];quiet=self.X[:,48]<=np.quantile(self.X[:,48],.6)
  eligible=(self.X[:,5]>.12)&(((pred[:,2]>.48)&quiet)|(pred[:,3]>.22)|(pred[:,2]>.65))
  intervals=[];run=[]
  for i,ok in enumerate(eligible):
   if not ok or (run and len(run)*self.size>=10):
    if run:intervals.append(run);run=[]
   if ok:run.append(i)
  if run:intervals.append(run)
  scenes=[s for s in self.bank['scenes'] if s['level']==level];receipt=[]
  if not scenes:return ns,receipt
  scaler=self.bank['scene_scaler'];Z=scaler.transform(np.stack([s['descriptor'] for s in scenes]))
  for ri,indices in enumerate(intervals):
   start=max(2.25,float(self.starts[indices[0]]),receipt[-1]['end']+.12 if receipt else 0);end=min(self.duration-.55,float(self.starts[indices[-1]]+self.size));span=end-start
   if span<1.5:continue
   z=scaler.transform([descriptor(self.t,self.f,start,end)])[0];p=np.mean(pred[indices],axis=0)
   scores=np.mean((Z-z)**2,axis=1)+.6*np.array([abs(np.log((s['end']-s['start'])/span)) for s in scenes])
   for k,s in enumerate(scenes):
    if p[2]>.48:scores[k]+=4*max(0,.7-s['field_coverage'])
    if p[3]>.22 and not s['paired_sides']:scores[k]+=.5
   source=scenes[int(np.argmin(scores))];ratio=span/(source['end']-source['start'])
   if not .45<=ratio<=2.4:continue
   # Replace the chopped gesture layer; keep keyboard attacks to coordinate with the sustained scene.
   retained=[]
   for n in ns:
    replace='sustain_locked' not in n and (n['type']==5 or n['type']==2 and n['side'] in (2,3)) and n['end']>=start*1000 and n['start']<=end*1000
    if not replace:retained.append(n)
    elif n['type']==2 and n['start']<start*1000:
     # Keep the audible attack before this scene, ending at the scene handoff.
     n['end']=round(start*1000)-40
     if n['end']-n['start']<100:n['type']=1;n['end']=n['start']
     retained.append(n)
   ns=retained
   added=[]
   for n0 in source['notes']:
    n=copy.deepcopy(n0);n['start']=round((start+(n0['start']/1000-source['start'])*ratio)*1000);n['end']=round((start+(n0['end']/1000-source['start'])*ratio)*1000)
    if n['type']==2:
     nearby=[x for x in ns if x['type'] in (1,2) and 'sustain_locked' not in x and x['start']>=start*1000 and abs(x['start']-n['start'])<=100]
     if nearby:
      victim=min(nearby,key=lambda x:abs(x['start']-n['start']));n['start']=victim['start'];ns.remove(victim)
    for k in ['x0','x1','w0','w1']:n[k]=list(n[k][:2])
    n['_group']=f'sustain:{ri}:'+str(n.pop('group'));n['sustain_locked']=ri;ns.append(n);added.append(dict(start=n['start'],end=n['end'],type=n['type'],side=n['side']))
   receipt.append(dict(start=start,end=end,source_song=source['song'],source_chart=source['chart'],source_start=source['start'],source_end=source['end'],paired_sides=source['paired_sides'],source_field_coverage=source['field_coverage'],notes=added,complete_scene=True))
  return ns,receipt

def finalize(ns):
 repairs=[];shortened=0;rerouted=0;removed=0
 for attempt in range(4):
  ns.sort(key=lambda n:(n['start'],n['type']!=5,n['side']))
  for run in jack_runs(ns):
   k=lane(run[0]);start=run[0]['start'];end=run[-1]['end']
   if k in (0,5):
    last_side=-10000
    for i,n in enumerate(run):
     if n['start']-last_side<=180:
      # At sub-90ms spacing even alternating sides creates two rapid jacks.
      # Keep spaced side accents and give the intervening attacks a wide pair.
      pairs=sorted([1,2,3],key=lambda p:abs(p-(1 if k==0 else 3)))
      pair=next((p for p in pairs if not any(h['type']==2 and h['start']<=n['start']<=h['end'] and set(keys(h))&{p,p+1} for h in ns)),None)
      place(n,pair,2) if pair is not None else place(n,1,4)
     else:place(n,k);last_side=n['start']
    kind='side accents with wide intervening attacks'
   else:
    options=[k-1 if k in (2,4) else k+1,k-1,k+1];partner=next((p for p in options if 1<=p<=4 and not any(h['type']==2 and h['start']<end and h['end']>start and p in keys(h) for h in ns)),None)
    for n in run:place(n,min(k,partner),2) if partner is not None else place(n,1,4)
    kind='wide repeated figure'
   repairs.append(dict(start=start,end=end,lane=k,notes=len(run),mode=kind))
  busy=[-1]*6;last=[-10000]*6;assigned=[]
  attacks=[n for n in ns if n['type'] in (1,2)]
  for ms,g in itertools.groupby(attacks,key=lambda n:n['start']):
   g=list(g)
   def solve(todo,used,answer):
    if not todo:return answer
    n=min(todo,key=lambda n:(not ('sustain_locked' in n),len([k for k in keys(n) if k not in used and busy[k]<ms])))
    for k in sorted(keys(n),key=lambda k:last[k]):
     if k in used or busy[k]>=ms:continue
     r=solve([x for x in todo if x is not n],used|{k},answer+[(n,k)])
     if r is not None:return r
    return None
   answer=solve(g,set(),[])
   if answer is None:
    for n in sorted(g,key=lambda n:'sustain_locked' in n):
     if 'sustain_locked' in n:continue
     candidates=sorted(range(1,5),key=lambda k:(last[k]>ms-180,abs(k-lane(n)),last[k]))+[0,5]
     original=copy.deepcopy(n)
     for k in candidates:
      if busy[k]>=ms:continue
      w=width(original)
      if w>1 and not 1<=k<=5-w:continue
      place(n,k,w);answer=solve(g,set(),[])
      if answer is not None:rerouted+=1;break
     if answer is not None:break
     n.clear();n.update(original)
   if answer is None:
    for h,k in reversed(assigned):
     if busy[k]==h['end'] and h['end']>=ms and h['start']<ms and 'sustain_locked' not in h:
      h['end']=max(h['start'],ms-40);busy[k]=h['end'];shortened+=1
      if h['end']-h['start']<100:h['type']=1;h['end']=h['start']
      answer=solve(g,set(),[])
      if answer is not None:break
   while answer is None:
    candidate=next((n for n in reversed(g) if 'sustain_locked' not in n),None);assert candidate is not None,'Conflicting protected source holds'
    g.remove(candidate);ns.remove(candidate);removed+=1;answer=solve(g,set(),[])
   for n,k in answer:n['_physical_key']=k;assigned.append((n,k));last[k]=ms;busy[k]=n['end'] if n['type']==2 else ms
  if not jack_runs(ns):break
 else:raise AssertionError('Interleaved rapid-run repair failed to converge')
 ns.sort(key=lambda n:(n['start'],n['type']!=5,n['side']));groups={}
 for i,n in enumerate(ns):n['id']=i;n['group']=groups.setdefault(n.pop('_group',f'old:{n.get("group",i)}'),i)
 return ns,dict(rapid_run_repairs=repairs,blocking_unprotected_holds_shortened=shortened,conflicting_targets_rerouted=rerouted,redundant_targets_removed=removed,protected_holds_shortened=0,physical_key_assignment_witness=True)

def rating(metrics,level):
 stock=[x for x in learned()['stock'] if x['level']==level];fields=['density','peak_2s','key_peak_1s','field_coverage','flick_fraction','hold_key_fraction','side_key_fraction']
 X=np.array([[r[k] for k in fields] for r in stock]);scale=np.maximum(np.std(X,axis=0),.05);q=np.array([metrics[k] for k in fields]);distance=np.mean(((X-q)/scale)**2,axis=1);near=np.argsort(distance)[:7]
 estimate=float(np.average([stock[i]['rating'] for i in near],weights=1/(distance[near]+.1)))
 # A rating cannot understate simultaneous/peak demand beyond the usual envelope at that level.
 guard=1
 highest=max(r['rating'] for r in stock)
 for target in range(1,highest+1):
  pool=[r for r in stock if r['rating']<=target]
  if len(pool)>=3 and all(metrics[k]<=np.quantile([r[k] for r in pool],.85)*1.08 for k in ['density','peak_2s','key_peak_1s']):guard=target;break
 else:
  # Do not label an out-of-range boss chart 15 just because the calibration
  # roster ends there. Extrapolation is explicit and remains a playtest estimate.
  top=[r for r in stock if r['rating']>=highest-1]
  excess=max(metrics[k]/max(.01,float(np.quantile([r[k] for r in top],.85))*1.08) for k in ['density','peak_2s','key_peak_1s'])
  guard=highest+max(0,int(np.ceil(np.log(max(1,excess))/np.log(1.15))))
 return max(guard,round(estimate)),dict(nearest_official=[stock[i]['chart'] for i in near],estimated_rating=estimate,demand_floor=guard,extrapolated_above_official=guard>highest,hard_rating_ceiling=None)
