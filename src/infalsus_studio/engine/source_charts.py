"""Read source AFF event types and align their attack sequence to local audio."""
import pathlib,sys,re,json,collections
import numpy as np
from scipy import signal,ndimage,optimize
import pathlib
B=pathlib.Path(__file__).parent
R=B/'revision7'
def parse_aff(path):
 text=path.read_text(encoding='utf8');offset=float(re.search(r'AudioOffset:([^\r\n]+)',text)[1]);body=text.split('-',1)[1] if '\n-\n' not in text else text.split('\n-\n',1)[1]
 # Whole-file tokenization retains timing-group boundaries and rejects unknown syntax.
 token=re.compile(r'\s*(?:(timinggroup)\(([^)]*)\)\s*\{|(\})\s*;?|([a-z]*)\(([^)]*)\)(\s*\[[^\]]*\])?\s*;)')
 pos=0;stack=[dict(id=0,noinput=False)];groups=0;notes=[];timings=[];ignored=collections.Counter()
 while pos<len(body):
  if not body[pos:].strip():break
  m=token.match(body,pos)
  if not m:raise ValueError((str(path),body[pos:pos+100]))
  pos=m.end();g=stack[-1]
  if m[1]:groups+=1;stack.append(dict(id=groups,noinput=g['noinput'] or 'noinput' in m[2]));continue
  if m[3]:assert len(stack)>1;stack.pop();continue
  kind=m[4];v=[x.strip() for x in m[5].split(',')]
  if kind=='timing':
   if g['id']==0:timings.append(dict(time=float(v[0])+offset,bpm=float(v[1]),meter=float(v[2])))
   continue
  if g['noinput']:ignored['noinput_'+kind]+=1;continue
  if kind=='':notes.append(dict(type='floor',start=float(v[0])+offset,end=float(v[0])+offset,lane=int(v[1])))
  elif kind=='hold':notes.append(dict(type='hold',start=float(v[0])+offset,end=float(v[1])+offset,lane=int(v[2])))
  elif kind=='arc':
   a=dict(type='arc',start=float(v[0])+offset,end=float(v[1])+offset,x0=float(v[2]),x1=float(v[3]),easing=v[4],y0=float(v[5]),y1=float(v[6]),color=int(v[7]),trace=v[9]=='true')
   if not a['trace']:notes.append(a)
   for value in re.findall(r'arctap\(([^)]+)\)',m[6] or ''):notes.append(dict(a,type='sky',start=float(value)+offset,end=float(value)+offset,arc_start=a['start'],arc_end=a['end']))
  elif kind in ['scenecontrol','camera']:ignored[kind]+=1
  else:raise ValueError(('Unknown source event',kind,path))
 assert len(stack)==1 and timings and notes
 return dict(notes=notes,timings=timings,ignored=dict(ignored),audio_offset=offset)

def align(chart,t,f):
 times=np.array(sorted({n['start']/1000 for n in chart['notes'] if n['type'] in ['floor','hold','sky']}));times=times[times>=0];step=float(t[1]-t[0]);duration=float(t[-1]);last=float(times[-1])
 v=np.maximum(0,.45*f[:,0]+.2*f[:,1]+.35*f[:,2]);v=np.minimum(v,np.quantile(v,.97));v=ndimage.gaussian_filter1d(v,.65);v=(v-ndimage.uniform_filter1d(v,round(2/step)))/np.maximum(ndimage.uniform_filter1d(abs(v),round(4/step)),.12)
 fit_times=times[np.arange(len(times))%3!=0];hold_times=times[np.arange(len(times))%3==0];results=[]
 # Search constant offsets and clock-rate mismatch; never stretch each note onto its nearest sound.
 for scale in np.arange(.996,1.00401,.0005):
  indexes=np.round(fit_times*scale/step).astype(int);k=np.bincount(indexes,minlength=int(last*scale/step)+2);corr=signal.correlate(v,k,mode='full',method='fft');lags=signal.correlation_lags(len(v),len(k))*step
  legal=(lags>=-min(12,times[0]))&(lags<=max(12,duration-last*scale+4));score=corr/max(1,len(fit_times));score[~legal]=-np.inf
  peaks,_=signal.find_peaks(score,distance=max(1,round(.08/step)));ids=sorted(peaks,key=lambda i:score[i],reverse=True)[:6]
  for i in ids:results.append((float(score[i]),float(lags[i]),float(scale)))
 candidates=sorted(results,reverse=True);seeds=[]
 for candidate in candidates:
  if all(abs(candidate[1]-other[1])>.12 for other in seeds):seeds.append(candidate)
  if len(seeds)>=8:break
 def loss(p,ts=fit_times):return -float(np.mean(np.interp(ts*p[1]+p[0],t,v,left=-2,right=-2)))
 # Compare continuous scores to continuous scores. Rounded FFT-bin scores can
 # falsely reject the correct offset when contrasted with an interpolated fit.
 refined=[]
 for _,seed_off,seed_scale in seeds:
  opt=optimize.minimize(loss,[seed_off,seed_scale],method='Nelder-Mead',bounds=[(seed_off-.07,seed_off+.07),(.993,1.007)],options={'xatol':.00005,'maxiter':120});o,s=map(float,opt.x);refined.append((-loss([o,s]),o,s))
 refined.sort(reverse=True);score,off,scale=refined[0];held=-loss([off,scale],hold_times)
 windows=[]
 for start in np.arange(times[0],last-8,12):
  pts=hold_times[(hold_times>=start)&(hold_times<start+12)]
  if len(pts)<10:continue
  shifts=np.arange(-.12,.1201,.002);ys=np.array([np.mean(np.interp(pts*scale+off+s,t,v,left=-2,right=-2)) for s in shifts]);j=int(np.argmax(ys));windows.append(dict(source_start=float(start),events=len(pts),residual_ms=float(shifts[j]*1000),score=float(ys[j])))
 residual=np.array([x['residual_ms'] for x in windows]);matched=(times*scale+off>=1.8)&(times*scale+off<duration-.3)
 alternative=next((x for x in refined if abs(x[1]-off)>.15),None)
 confidence=dict(training_score=score,heldout_score=held,window_residual_median_ms=float(np.median(abs(residual))) if len(residual) else 999,window_residual_p90_ms=float(np.quantile(abs(residual),.9)) if len(residual) else 999,attack_coverage=float(np.mean(matched)),candidate_advantage=score-alternative[0] if alternative else 0)
 passed=confidence['attack_coverage']>.985 and held>.3 and confidence['window_residual_median_ms']<=24 and confidence['window_residual_p90_ms']<=65 and .993<scale<1.007
 return dict(status='aligned' if passed else 'needs_review',offset_seconds=off,time_scale=scale,source_end=last,local_duration=duration,windows=windows,candidates=[dict(score=s,offset=o,rate=r) for s,o,r in refined],**confidence,method='Source attack-sequence correlation, constant offset and rate fit, independent withheld attacks and 12-second residual windows')

