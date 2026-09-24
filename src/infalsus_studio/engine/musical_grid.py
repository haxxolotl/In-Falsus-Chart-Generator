"""Audio-supported local beat clocks, measure anchors and structural previews."""
import pathlib,sys,json,struct
import numpy as np
from scipy import signal,ndimage,optimize
import librosa
KNOWN={'Altale':90.,'Antagonism':160.,'Astral Quantization (Chart View)':185.,'Breach of Faith':172.,'Cataclysm Cry':180.}

def grid(row,t,f,old):
 nominal=KNOWN.get(row['title'],round(old['bpm']))
 onset=.35*f[:,0]+.35*f[:,1]+.2*f[:,2]+.1*f[:,3]
 ids,_=signal.find_peaks(onset,distance=4,prominence=.13);ids=ids[(t[ids]>2)&(f[ids,5]>.12)]
 times=t[ids];weights=np.minimum(onset[ids],np.quantile(onset[ids],.9));centers=np.arange(8,t[-1]-4,4);bpms=[];confidence=[]
 lo,hi=(82,91) if row['title']=='Altale' else (nominal*.92,nominal*1.08)
 candidates=np.arange(lo,hi,.05)
 for center in centers:
  use=abs(times-center)<8;ts=times[use];w=weights[use]
  if len(ts)<12:bpms.append(nominal);confidence.append(0.);continue
  z=np.exp(2j*np.pi*(ts[:,None]-center)*(candidates[None,:]/15));coh=abs(np.sum(w[:,None]*z,axis=0))/sum(w)
  score=coh-.15*abs(candidates-nominal)/nominal;k=np.argmax(score);bpms.append(float(candidates[k]));confidence.append(float(coh[k]))
 bpms=ndimage.median_filter(np.array(bpms),size=3);reliable=np.array(confidence)>.3
 stable=np.mean(abs(bpms[reliable]-nominal)<.4)> .9 if reliable.any() else True
 if row['title']=='Altale':
  stable=False;last=nominal
  for i,q in enumerate(confidence):
   if q>=.2:last=bpms[i]
   else:bpms[i]=last
 if stable:local=np.full(len(t),nominal)
 else:local=np.interp(t,centers,bpms)
 # Dynamic programming keeps a continuous beat sequence through local tempo changes.
 _,indices=librosa.beat.beat_track(onset_envelope=onset,sr=22050,hop_length=256,bpm=local,tightness=150,trim=False)
 beats=t[indices].astype(float);beats=beats[beats>1.8]
 if len(beats)<16:raise ValueError('Insufficient beats: '+row['title'])
 # Choose the strongest metrical phase using bass accents and forward phrase energy.
 bass=np.interp(beats,t,f[:,1]+.3*f[:,6]);smooth=ndimage.uniform_filter1d(f[:,5],size=round(1/(t[1]-t[0])))
 accent=bass+.25*(np.interp(beats+.35,t,smooth)-np.interp(beats-.35,t,smooth))
 phase=int(np.argmax([np.mean(np.minimum(accent[i::4],2)) for i in range(4)]));bars=beats[phase::4]
 if stable:
  step=60/nominal/4;z=np.sum(weights*np.exp(2j*np.pi*times/step));fine=float(np.angle(z)/(2*np.pi)*step)%step
  period=240/nominal;anchor=float(np.median(bars-np.round((bars-bars[0])/period)*period));anchor+=((fine-anchor+step/2)%step-step/2)
  bars=anchor+np.arange(int((float(t[-1])-anchor)/period)+1)*period
 else:
  # Bar anchors are smoothed in beat space, retaining the ritardando rather than frame jitter.
  bars=ndimage.median_filter(bars-np.arange(len(bars))*240/nominal,size=3)+np.arange(len(bars))*240/nominal
  if row['title']=='Altale':
   n=len(bars);A=np.tril(np.ones((n,n)));A[:,0]=1;D=np.diff(np.eye(n),axis=0)[1:];D[:,0]=0
   fit=optimize.lsq_linear(np.r_[A,2*D],np.r_[bars,np.zeros(len(D))],bounds=(np.r_[bars[0]-.12,np.full(n-1,240/90.1)],np.r_[bars[0]+.12,np.full(n-1,240/82.8)]))
   bars=A@fit.x
 bars=np.round(bars*1000)/1000;bars=bars[(bars>=1.8)&(bars<t[-1]-.4)]
 assert np.all(np.diff(bars)>0)
 segments=[dict(start=float(a),end=float(b),bpm=float(240/(b-a)),beats=4) for a,b in zip(bars,bars[1:])]
 assert all(nominal*.7<s['bpm']<nominal*1.3 for s in segments),(row['title'],'tempo discontinuity')
 # Keep one clock for fixed-tempo songs; use phase-preserving BPM changes otherwise.
 if stable:
  segments=[dict(start=float(bars[0]),end=float(t[-1]),bpm=float(nominal),beats=4)]
  bars=np.arange(bars[0],float(t[-1]),240/nominal)
 else:segments.append(dict(start=float(bars[-1]),end=float(t[-1]),bpm=segments[-1]['bpm'],beats=4))
 pulses=[]
 for s in segments:pulses.extend(np.arange(s['start'],s['end']-.001,7.5/s['bpm']))
 pulses=np.array(pulses);delta=np.min(abs(times[:,None]-pulses[None,:]),axis=1)
 return dict(nominal_bpm=nominal,stable=bool(stable),segments=segments,bars=bars.tolist(),local_estimates=[dict(time=float(c),bpm=float(b),confidence=float(q)) for c,b,q in zip(centers,bpms,confidence)],onset_grid_median_ms=float(np.median(delta)*1000),onset_within_25ms=float(np.mean(delta<.025)),method='Local audio tempo estimates, dynamic beat tracking, bass-accent measure phase; explicit native phase event')

def timing_hex(g):
 return b''.join(struct.pack('<qiidff',i,round(s['start']*1000),1 if i==0 else 2,s['bpm'],4.,0.) for i,s in enumerate(g['segments'])).hex()

def ticks(g,duration,division=4):
 return np.array([x for s in g['segments'] for x in np.arange(s['start'],min(s['end'],duration)-.0001,60/s['bpm']/division)])

def preview(row,t,f,g):
 bars=np.array(g['bars']);starts=bars[(bars>max(10,t[-1]*.16))&(bars<t[-1]-21)]
 # Compare four-measure harmonic/timbral phrases, excluding immediate neighbors.
 desc=[]
 for s in bars:
  a=f[(t>=s)&(t<s+min(8,960/g['nominal_bpm']))];desc.append(np.r_[a[:,12:24].mean(0),a[:,6:10].mean(0)*.35] if len(a) else np.zeros(16))
 X=np.array(desc);X/=np.maximum(np.linalg.norm(X,axis=1,keepdims=True),1e-6);sim=X@X.T;sim[abs(bars[:,None]-bars[None,:])<16]=-1
 scores=[]
 for s in starts:
  win=f[(t>=s)&(t<s+20)];pre=f[(t>=s-4)&(t<s)];energy=float(np.mean(win[:,5]));activity=float(np.mean(win[:,:4]));repeat=float(max(0,np.max(sim[np.argmin(abs(bars-s))])))
  rise=float(np.mean(win[t[(t>=s)&(t<s+20)]<s+4,5])-np.mean(pre[:,5]));boundary=float(np.linalg.norm(win[:max(1,round(2/(t[1]-t[0]))),12:24].mean(0)-pre[:,12:24].mean(0)))
  score=energy*(.55+.45*repeat)+.18*activity+.3*max(0,rise)+.12*boundary
  scores.append(dict(start=float(s),end=float(min(s+20,row['duration_seconds']-.5)),score=score,energy=energy,phrase_recurrence=repeat,energy_rise=rise))
 if not scores:raise ValueError('No preview window')
 ranked=sorted(scores,key=lambda x:x['score'],reverse=True);best=ranked[0]
 return dict(**best,previous_start=row.get('preview_start_seconds'),candidates=ranked[:5],method='Measure-aligned energetic recurring phrase with entrance/contrast score, rather than a loudness-window midpoint')
