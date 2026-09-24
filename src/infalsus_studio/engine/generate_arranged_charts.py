"""Rhythm-matched one-to-one phrase transfer with native wide-note semantics."""
import pathlib,sys,json,copy,struct,collections,argparse,hashlib
import numpy as np,joblib
from scipy import signal
from .generate_phrase_charts import musical_events,salience_features,choose_events
from .learn_phrase_library import descriptor,chart_metrics,rating_features
from .prepare_charts import tempo
from .prepare_audio import guid,sha,save
from .chart_encoder import Encoder
from .chart_emulator import Decoder
from .phrase_memory import PhraseMemory
from .arrangement_policy import Arrangement,finalize as arranged_finalize,rating as arranged_rating
from .arrangement_metrics import extended
import pathlib
B=pathlib.Path(__file__).parent
R=B/'revision4';O=B.parent/'outputs/arcaea-custom-pack-v4';VERSION='arranged-v4.0'

def keyrange(n):
 lo=round(n['x0'][0]/n['x0'][1]*4);w=max(1,round(n['w0'][0]/n['w0'][1]*4))
 return list(range(lo,lo+w))
def setkey(n,lo,w=1):
 n['side']=2 if lo==0 else 3 if lo==5 else 1
 n['x0']=n['x1']=[lo,4];n['w0']=n['w1']=[w,4]
def rhythm_grid(t,events,bpm):
 times=np.array([t[r[0]] for r in events]);weights=np.array([r[1] for r in events])
 # Estimate a quarter-beat lattice. Only snap near it when independently fitted support is sufficient.
 take=np.argsort(weights)[-min(1000,len(times)):];ts=times[take];ws=np.minimum(weights[take],np.quantile(weights,.9))
 bpms=np.arange(bpm*.97,bpm*1.03,.02);best=(-1,None,None)
 for candidate in bpms:
  step=60/candidate/4;z=np.sum(ws*np.exp(2j*np.pi*ts/step))/np.sum(ws)
  if abs(z)>best[0]:best=(abs(z),candidate,float(np.angle(z)/(2*np.pi)*step)%step)
 confidence,bpm,phase=best;step=60/bpm/4
 deltas=phase+np.round((times-phase)/step)*step-times
 support=float(np.mean(abs(deltas)<=.018));use=confidence>=.25 and support>=.45
 corrected=times.copy()
 if use:
  mask=abs(deltas)<=.018;corrected[mask]+=deltas[mask]
 else:mask=np.zeros(len(times),bool)
 return dict(bpm=float(bpm),phase=phase,confidence=float(confidence),near_grid_fraction=support,snapped=int(mask.sum()),method='Bounded quarter-beat snapping; uncertain and distant attacks preserved'),{r[0]:float(s) for r,s in zip(events,corrected)}

class Matcher:
 def __init__(self):
  lib=joblib.load(B/'revision2/phrase-library.joblib');self.bank=lib['phrases'];self.scaler=lib['scaler']
  self.z=self.scaler.transform(np.stack([p['descriptor'] for p in self.bank]))
  self.moments=[np.array(sorted(set(n['start'] for n in p['notes'] if n['type'] in (1,2,4)))) for p in self.bank];self.cache={}
 def pool(self,level,n):
  key=(level,n)
  if key in self.cache:return self.cache[key]
  ids=[];starts=[];patterns=[];spans=[]
  for i,p in enumerate(self.bank):
   if p['level']!=level:continue
   ms=self.moments[i]
   if len(ms)<n:continue
   for st in range(0,len(ms)-n+1,max(1,n//3)):
    v=ms[st:st+n];span=(v[-1]-v[0])*p['duration'] if n>1 else p['duration']/max(1,len(ms))
    if span<=0:continue
    ids.append(i);starts.append(st);patterns.append(np.diff(v)/(v[-1]-v[0]) if n>1 else [0]);spans.append(span)
  result=(np.asarray(ids),np.asarray(starts),np.asarray(patterns),np.asarray(spans));self.cache[key]=result;return result
 def select(self,level,times,z,previous):
  n=len(times);ids,starts,patterns,spans=self.pool(level,n)
  assert len(ids),(level,n)
  target=np.diff(times)/(times[-1]-times[0]) if n>1 else np.array([0.])
  rhythm=np.mean(abs(patterns-target),axis=1)*max(1,n-1)
  audio=np.mean((self.z-z)**2,axis=1)[ids]
  speed=np.abs(np.log(spans/max(.12,times[-1]-times[0]))) if n>1 else np.zeros(len(ids))
  score=3*rhythm+.22*audio+.3*speed
  if previous is not None:score+=np.array([.08 if self.bank[i]['song']!=previous else 0 for i in ids])
  k=int(np.argmin(score));return self.bank[ids[k]],self.moments[ids[k]][starts[k]:starts[k]+n],float(rhythm[k]),float(speed[k])
def partition(rows,times):
 if len(rows)<=12:return [(rows,times)]
 mid=len(rows)//2;lo=max(2,mid-3);hi=min(len(rows)-2,mid+3)
 split=max(range(lo,hi+1),key=lambda k:times[k]-times[k-1])
 return partition(rows[:split],times[:split])+partition(rows[split:],times[split:])

def ergonomic(notes,pass_number=0):
 notes.sort(key=lambda n:(n['start'],n['type']!=5,n['side']))
 keys=[n for n in notes if n['type'] in (1,2)];repairs=[];run=[]
 def finish(run):
  if len(run)<3:return
  lane=keyrange(run[0])[0];first=run[0]['start'];last=run[-1]['end']
  # The rapid phrase must offer distinct physical keys. Native wide taps accept either key.
  if lane in (0,5):
   for j,n in enumerate(run):setkey(n,lane if j%2==0 else 5-lane)
   mode='alternate side keys'
  else:
   partner=lane+1 if lane<4 else 3;pair=min(lane,partner)
   occupied=any(n['type']==2 and n['start']<last and n['end']>first and partner in keyrange(n) for n in keys if n not in run)
   if len(run)>=5 and not occupied:
    for n in run:setkey(n,pair,2)
    mode='two-key wide taps'
   else:
    for j,n in enumerate(run):
     if j%2:
      choices=[x for x in (partner,1,2,3,4) if x!=lane and not any(h['type']==2 and h['start']<=n['start']<h['end'] and x in keyrange(h) for h in keys)]
      if choices:setkey(n,choices[0])
      else:setkey(n,pair,2)
    mode='alternating narrow taps'
  repairs.append(dict(start=first,end=last,notes=len(run),mode=mode))
 for n in keys:
  if n['type']!=1 or len(keyrange(n))!=1:
   finish(run);run=[];continue
  if run and (keyrange(n)!=keyrange(run[-1]) or not 0<n['start']-run[-1]['start']<=140):finish(run);run=[]
  run.append(n)
 finish(run)
 # Greedy physical-key witness, with complete per-moment matching; wide notes are alternatives.
 busy=[-1]*6;last=[-10000]*6;assignments=[];shortened=0
 for ms,group in __import__('itertools').groupby(keys,key=lambda n:n['start']):
  group=list(group)
  def solve(todo,used,chosen):
   if not todo:return chosen
   n=min(todo,key=lambda n:len([k for k in keyrange(n) if k not in used and busy[k]<ms]))
   for k in sorted(keyrange(n),key=lambda k:last[k]):
    if k in used or busy[k]>=ms:continue
    ans=solve([x for x in todo if x is not n],used|{k},chosen+[(n,k)])
    if ans is not None:return ans
   return None
  solution=solve(group,set(),[])
  if solution is None:
   # End only an earlier hold that actually blocks every physical assignment.
   for h,k in assignments:
    if h['end']>=ms and h['start']<ms:
     h['end']=max(h['start'],ms-40);busy[k]=h['end'];shortened+=1
     if h['end']-h['start']<100:h['type']=1;h['end']=h['start']
   solution=solve(group,set(),[])
  if solution is None:
   # Two overlapping tap targets at one instant are one playable target, not repeated hits.
   occupied=set();kept=[]
   for n in group:
    available=[k for k in keyrange(n) if k not in occupied]
    if not available:notes.remove(n);continue
    k=min(available,key=lambda k:last[k]);occupied.add(k);kept.append((n,k))
   solution=kept
  for n,k in solution:
   assignments.append((n,k));last[k]=ms;busy[k]=n['end'] if n['type']==2 else ms
 # Removing an impossible overlapping target can expose a new narrow run across its gap.
 from .ergonomic_metrics import metrics as ergonomic_metrics
 if ergonomic_metrics(notes)['rapid_narrow_runs']:
  assert pass_number<2,'Rapid-run repair failed to converge'
  revised,more=ergonomic(notes,pass_number+1)
  more['rapid_run_repairs']=repairs+more['rapid_run_repairs']
  more['blocking_holds_shortened']+=shortened
  return revised,more
 groups={}
 for i,n in enumerate(notes):n['id']=i;n['group']=groups.setdefault(n.pop('_group'),i) if '_group' in n else i
 return notes,dict(rapid_run_repairs=repairs,blocking_holds_shortened=shortened,physical_key_assignment_witness=True)

def generate(row,matcher,model,features_path=None):
 fdata=np.load(features_path or B/'arcaea-preparation/features'/(row['base_name']+'.npz'));t=fdata['t'];f=fdata['features'];events,_=musical_events(t,f)
 sal=joblib.load(B/'revision2/salience-model.joblib');prob=sal['model'].predict_proba(salience_features(t,f,events))[:,1];strong=np.quantile([r[1] for r in events],.92)
 events=[r for r,p in zip(events,prob) if p>=sal['threshold'] or r[1]>=strong]
 grid,time_by_id=rhythm_grid(t,events,row.get('nominal_bpm') or tempo(t,f));duration=row['duration_seconds'];width=8*60/grid['bpm']
 origin=grid['phase']+np.floor((2-grid['phase'])/(60/grid['bpm']))*(60/grid['bpm'])
 policy=Arrangement(t,f,duration,grid,origin,width)
 outputs=[];details=[]
 for level in range(4):
  notes=[];trace=[];selected=[];previous=None;memory=PhraseMemory();budgets=[]
  for pi,start in enumerate(np.arange(origin,duration-.6,width)):
   end=min(duration-.5,start+width);part=[r for r in events if start<=time_by_id[r[0]]<end];chosen,budget=policy.choose(part,level,pi,time_by_id);budgets.append(budget);phrase_begin=len(notes)
   if not chosen:continue
   times=np.array([time_by_id[r[0]] for r in chosen]);order=np.argsort(times);chosen=[chosen[i] for i in order];times=times[order]
   # Snapping can merge nearby detections; one onset is one moment.
   unique=[];uts=[]
   for r,s in zip(chosen,times):
    if uts and round(s*1000)==round(uts[-1]*1000):continue
    unique.append(r);uts.append(s)
   selected.extend(round(s*1000) for s in uts)
   parts=partition(unique,np.array(uts))
   phrase_z=matcher.scaler.transform([descriptor(t,f,start,end)])[0]
   memory.begin(np.array(uts),phrase_z,[len(p[0]) for p in parts])
   for bi,(chunk,at) in enumerate(parts):
    z=matcher.scaler.transform([descriptor(t,f,max(2,at[0]-.2),min(duration,at[-1]+.3))])[0]
    template,anchors,err,speed,motif=memory.choose(bi,at,z,lambda:matcher.select(level,at,z,previous));previous=template['song']
    # Monotone one-to-one alignment: no source strike is duplicated or spread over extra attacks.
    left=max(start,at[0]-.15);right=min(end,at[-1]+.3)
    src_left=max(0,anchors[0]-.15/template['duration']);src_right=min(1,anchors[-1]+.3/template['duration'])
    if len(at)>1:
     def warp(v):return round(float(np.interp(v,np.r_[src_left,anchors,src_right],np.r_[left,at,right]))*1000)
    else:
     def warp(v):return round((at[0]+(v-anchors[0])*template['duration'])*1000)
    group_prefix=f'{pi}:{bi}:'
    for n0 in template['notes']:
     if n0['type'] in (1,2,4):
      ids=np.flatnonzero(np.isclose(anchors,n0['start'],atol=1e-8))
      if not len(ids):continue
      j=int(ids[0]);n=copy.deepcopy(n0);n['start']=round(at[j]*1000)
      n['end']=n['start'] if n['type'] in (1,4) else min(round(right*1000),max(n['start'],warp(n0['end'])))
     else:
      if n0['start']<src_left or n0['start']>=src_right:continue
      n=copy.deepcopy(n0);n['start']=max(round(left*1000),warp(n0['start']));n['end']=min(round(right*1000),warp(n0['end']))
      if n['end']-n['start']<15:continue
     for k in ['x0','x1','w0','w1']:n[k]=list(n[k][:2])
     n['_group']=group_prefix+str(n.pop('group'));n.pop('id',None)
     n['start']=max(1800,min(round(duration*1000)-600,n['start']));n['end']=max(n['start'],min(round(duration*1000)-500,n['end']))
     if n['type']==2 and n['end']-n['start']<100:n['type']=1;n['end']=n['start']
     notes.append(n)
    trace.append(dict(target_start=float(at[0]),target_end=float(at[-1]),events=len(at),source_song=template['song'],source_chart=template['chart'],source_start=template['start']+float(anchors[0])*template['duration'],rhythm_error=err,speed_log_ratio=speed,source_notes_duplicated=0,motif=motif))
   notes[phrase_begin:]=policy.chord_budget(notes[phrase_begin:],budget,chosen,time_by_id)
  notes,sustains=policy.sustain(notes,level)
  notes,repairs=arranged_finalize(notes);m=extended(notes,duration);rating,rating_proof=arranged_rating(m,level)
  actual={n['start'] for n in notes if n['type'] in (1,2,4)};missing=[x for x in selected if x not in actual and not any(round(z['start']*1000)<=x<=round(z['end']*1000) for z in sustains)];coverage=1-len(missing)/max(1,len(selected));assert not missing,(row['title'],level,missing,repairs)
  outputs.append(dict(name=f'{row["base_name"]}{level}.spc',bpm=grid['bpm'],beats=4,timing_data_hex='',notes=notes))
  details.append(dict(level=level,rating=rating,metrics=m,selected_musical_events=len(selected),selected_event_coverage=coverage,grid=grid,phrases=trace,ergonomics=repairs,attack_budgets=budgets,sustained_scenes=sustains,rating_evidence=rating_proof,motif_reuse=dict(__import__('collections').Counter(x['scope'] for x in memory.report)),motif_variations=dict(__import__('collections').Counter(x['transform'] for x in memory.report))))
 for i in range(1,4):details[i]['rating']=max(details[i]['rating'],details[i-1]['rating'])
 return outputs,details
def verify(chart,decoded,duration):
 assert len(chart['notes'])==len(decoded['notes'])
 for a,b in zip(chart['notes'],decoded['notes']):
  for k in ['id','group','side','type','start','end']:assert a[k]==b[k],k
  for k in ['x0','x1','w0','w1']:assert a[k][:2]==list(b[k][:2]),k
  assert a['flags']==struct.unpack_from('<I',bytes.fromhex(b['extra']),36)[0]
  assert 1800<=b['start']<=b['end']<duration*1000
  if b['type'] in (1,2):assert 0<=b['key_start']<=b['key_end']<=5
