"""Recall rhythmic figures at phrase and subphrase scales; vary keyboard orientation."""
import copy,json,pathlib
import numpy as np

B=pathlib.Path(__file__).parent

class PhraseMemory:
 def __init__(self):
  self.small=[];self.medium=[];self.active=None;self.mode='new';self.report=[]
  study=json.loads((B/'revision3/phrase-structure-study.json').read_text())
  self.shift_charts={x['chart'] for x in study['examples'] if x['kind']=='lane shift'}
 def recall(self,bank,times,z,parts=None):
  if len(times)<3:return None
  pattern=np.diff(times)/max(.01,times[-1]-times[0]);best=None;score=1e9
  for old in bank:
   if len(old['times'])!=len(times) or (parts is not None and old['sizes']!=parts):continue
   rhythm=float(np.mean(abs(old['pattern']-pattern)))*(len(times)-1)
   timbre=float(np.mean((old['z']-z)**2));stretch=abs(np.log((times[-1]-times[0])/max(.01,old['times'][-1]-old['times'][0])))
   if rhythm>.13 or timbre>.65 or stretch>.16:continue
   value=rhythm+timbre*.25
   if value<score:score=value;best=old
  return best
 def begin(self,times,z,sizes):
  old=self.recall(self.medium,times,z,sizes)
  if old is None:
   old=dict(times=times.copy(),pattern=np.diff(times)/max(.01,times[-1]-times[0]),z=z.copy(),sizes=sizes,parts=[],uses=0)
   self.medium.append(old);self.mode='new'
  else:old['uses']+=1;self.mode='medium'
  self.active=old
 def choose(self,bi,times,z,fallback):
  if self.mode=='medium':
   item=self.active['parts'][bi];scope='two-measure phrase';uses=self.active['uses']
  else:
   item=self.recall(self.small,times,z)
   if item is None:
    template,anchors,err,speed=fallback()
    item=dict(times=times.copy(),pattern=np.diff(times)/max(.01,times[-1]-times[0]),z=z.copy(),template=template,anchors=anchors,err=err,speed=speed,uses=0)
    self.small.append(item);scope='new figure'
   else:item['uses']+=1;scope='small figure'
   uses=item['uses'];self.active['parts'].append(item)
  template=copy.deepcopy(item['template']);anchors=item['anchors'];keys=[n for n in template['notes'] if n['type'] in (1,2) and anchors[0]-1e-8<=n['start']<=anchors[-1]+1e-8]
  transform='retain';shift=0
  # Call/answer, return, then a shifted answer where the authored span has room.
  # Keep mouse groups intact; keyboard orientation is independent of mouse direction.
  if uses%4 in (1,3) and template['chart'] in self.shift_charts:
   middle=[n for n in keys if n['side']==1]
   if middle:
    low=min(round(n['x0'][0]/n['x0'][1]*4) for n in middle)
    high=max(round((n['x0'][0]/n['x0'][1]+n['w0'][0]/n['w0'][1])*4)-1 for n in middle)
    shift=1 if high<4 else -1 if low>1 else 0
    if shift:transform='shift'
  if uses%2 and not shift:transform='mirror'
  for n in keys:
   lo=round(n['x0'][0]/n['x0'][1]*4);width=max(1,round(n['w0'][0]/n['w0'][1]*4))
   target=6-width-lo if transform=='mirror' else lo+shift if n['side']==1 else lo
   n['x0']=n['x1']=[target,4];n['w0']=n['w1']=[width,4];n['side']=2 if target==0 else 3 if target==5 else 1
  info=dict(scope=scope,transform=transform,lane_shift=shift,occurrence=uses+1,motif_start=float(item['times'][0]),phrase_origin=float(self.active['times'][0]),target_start=float(times[0]),target_end=float(times[-1]))
  self.report.append(info)
  rhythm=float(np.mean(abs(np.diff(anchors)/(anchors[-1]-anchors[0])-np.diff(times)/(times[-1]-times[0]))))*(len(times)-1) if len(times)>1 else 0
  speed=abs(float(np.log(max(.01,(anchors[-1]-anchors[0])*template['duration'])/max(.01,times[-1]-times[0])))) if len(times)>1 else 0
  return template,anchors,rhythm,speed,info
