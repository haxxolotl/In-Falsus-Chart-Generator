"""Audio-driven draft charts, with explicit ergonomic rules after learned timing scores."""
import pathlib,sys,json,math,struct,time,hashlib,argparse
BASE=pathlib.Path(__file__).parent.resolve()
import numpy as np,joblib
from scipy import signal,ndimage
from .chart_encoder import Encoder
from .chart_emulator import Decoder
from .prepare_audio import OUT,STAGE,guid,sha,save

VERSION='audio-chart-v1'
DENSITY=[1.05,2.07,3.89,5.78]
LEVELS=['ORI','EVO','ULT','FBD']

def tempo(t,f):
    step=float(t[1]-t[0]);v=f[:,0]-np.mean(f[:,0])
    ac=signal.fftconvolve(v,v[::-1],mode='full')[len(v)-1:]
    lags=np.arange(round(60/210/step),round(60/80/step))
    # Tempo is a scrolling estimate; note timestamps stay on actual detected attacks.
    score=ac[lags]+.5*ac[np.minimum(lags*2,len(ac)-1)]
    lag=int(lags[np.argmax(score)])
    return round(60/(lag*step),2)

def candidates(t,f,model):
    ctx=np.column_stack([f,ndimage.maximum_filter1d(f[:,:5],size=7,axis=0),
        ndimage.uniform_filter1d(f[:,5],size=35),np.roll(ndimage.uniform_filter1d(f[:,5],size=17),-9)])
    ix,_=signal.find_peaks(f[:,0],distance=4,prominence=.025,height=.06)
    ix=ix[(t[ix]>2.5)&(t[ix]<t[-1]-.7)&(f[ix,5]>.06)]
    scores=[]
    for level in range(1,5):
        scores.append(model.predict_proba(np.column_stack([ctx[ix],np.full(len(ix),level)]))[:,1])
    return ix,np.array(scores)

def select_levels(t,f,ix,scores,duration):
    selected=set();out=[]
    active=np.flatnonzero(f[:,5]>.08)
    span=float(t[active[-1]]-t[active[0]])
    for level in range(4):
        target=round(span*DENSITY[level]);gap=[.24,.16,.105,.075][level]
        ranked=sorted(range(len(ix)),key=lambda j:float(scores[level,j]*(.5+.5*min(1.5,f[ix[j],0]))),reverse=True)
        selected_times=sorted(float(t[i]) for i in selected)
        import bisect
        for j in ranked:
            k=int(ix[j]);sec=float(t[k]);pos=bisect.bisect_left(selected_times,sec)
            if k in selected:continue
            if (pos and sec-selected_times[pos-1]<gap) or (pos<len(selected_times) and selected_times[pos]-sec<gap):continue
            selected.add(k);selected_times.insert(pos,sec)
            if len(selected)>=target:break
        out.append(sorted(selected))
    return out

def make_chart(t,f,indices,level,bpm,duration):
    beat=60/bpm;notes=[]
    patterns=[[1,2,3,4,3,2],[1,3,2,4],[4,3,2,1,2,3],[1,2,4,3],[1,4,2,3]]
    busy=[0]*6;flick_end=-10000;flick_direction=0
    def key_note(ms,lane,typ=1,end=None):
        n={'side':2 if lane==0 else 3 if lane==5 else 1,'type':typ,'start':ms,'end':ms if end is None else end,
            'x0':[lane,4],'x1':[lane,4],'w0':[1,4],'w1':[1,4],'flags':0,'lane':lane}
        notes.append(n);return n
    for j,ix in enumerate(indices):
        ms=round(float(t[ix])*1000);phrase=int(float(t[ix])/(beat*8))
        pattern=patterns[phrase%len(patterns)];lane=pattern[j%len(pattern)]
        bass=f[ix,1];high=f[ix,4]
        if level>=1 and bass>1.05 and j%(8-level)==0:lane=0 if phrase%2==0 else 5
        if level>=1 and high>1.05 and ms-flick_end>[2400,2000,1400,1000][level] and j%7==0:
            notes.append({'side':4,'type':4,'start':ms,'end':ms,'x0':[1,2],'x1':[1,2],
                'w0':[1,1],'w1':[1,1],'flags':1024 if flick_direction%2==0 else 4096})
            flick_direction+=1;flick_end=ms
        else:
            free=[k for k in [lane,*pattern,0,5] if busy[k]+75<ms]
            if not free:continue
            lane=free[0];n=key_note(ms,lane)
            # Sustained energy plus a gap after this attack supports a conservative hold.
            next_ms=round(float(t[indices[j+1]])*1000) if j+1<len(indices) else round(duration*1000)-500
            if j%[6,6,7,8][level]==3 and next_ms-ms>180 and f[ix,5]>.30:
                length=round(min([1000,850,700,600][level],max(300,beat*1000)))
                n['type']=2;n['end']=min(ms+length,round(duration*1000)-500);busy[lane]=n['end']
            if level>=1 and j%[1000,16,7,5][level]==0 and bass>.8:
                other=5-lane if 1<=lane<=4 else (3 if lane==0 else 2)
                if other!=lane and busy[other]+100<ms:key_note(ms,other)
    # Clamp each hold before the next attack in that key, preserving release time.
    for lane in range(6):
        ns=sorted((n for n in notes if n.get('lane')==lane),key=lambda n:n['start'])
        for a,b in zip(ns,ns[1:]):
            if a['type']==2 and a['end']>b['start']-90:a['end']=b['start']-90
            if a['type']==2 and a['end']-a['start']<160:a['type']=1;a['end']=a['start']
    # Continuous field motion: stock-supported linear segment flag, rational coordinates,
    # bounded speed, and generous width. This is a rule, not an audio-trained lane model.
    width=[.5,.42,.36,1/3][level];speed=[.12,.15,.19,.24][level]
    segment=beat*[8,8,4,4][level];start=2.0;center=.5;chain='field'
    while start<duration-.5:
        end=min(duration-.5,start+segment)
        lo=int(np.searchsorted(t,start));hi=max(lo+1,int(np.searchsorted(t,end)))
        chroma=f[lo:hi,12:24].mean(axis=0);p=int(np.argmax(chroma))
        target=.25+.5*(p/11);delta=max(-speed*(end-start),min(speed*(end-start),target-center))
        target=max(width/2,min(1-width/2,center+delta))
        notes.append({'side':4,'type':5,'start':round(start*1000),'end':round(end*1000),
            'x0':[round(center*240),240],'x1':[round(target*240),240],
            'w0':[round(width*240),240],'w1':[round(width*240),240],'flags':36,'chain':chain})
        center=target;start=end
    notes.sort(key=lambda n:(n['start'],n['type']!=5,n.get('lane',9)))
    groups={}
    for i,n in enumerate(notes):
        n['id']=i;n['group']=groups.setdefault(n['chain'],i) if 'chain' in n else i
        n.pop('lane',None);n.pop('chain',None)
    return {'bpm':bpm,'beats':4,'timing_data_hex':'','notes':notes,
        'authoring':'Learned attack timing scores; explicit lane, hold, flick and field rules.'}

def validate(chart,decoded,duration):
    assert len(chart['notes'])==len(decoded['notes'])
    counts={};lanes=[[] for _ in range(6)];fields=[]
    for a,b in zip(chart['notes'],decoded['notes']):
        for k in ['id','group','side','type','start','end']:assert a[k]==b[k],(k,a[k],b[k])
        for k in ['x0','x1','w0','w1']:assert a[k][:2]==list(b[k][:2])
        assert a['flags']==struct.unpack_from('<I',bytes.fromhex(b['extra']),36)[0]
        assert 1500<=b['start']<=b['end']<duration*1000
        assert 0<=b['group']<=b['id']
        counts[str(b['type'])]=counts.get(str(b['type']),0)+1
        if b['type'] in (1,2):
            assert 0<=b['key_start']==b['key_end']<=5
            lanes[b['key_start']].append(b)
        elif b['type']==5:
            for x,w in [('x0','w0'),('x1','w1')]:assert -.0001<=b[x][2]-b[w][2]/2 and b[x][2]+b[w][2]/2<=1.0001
            fields.append(b)
    for ns in lanes:
        for a,b in zip(ns,ns[1:]):assert b['start']-a['end']>=70,(a,b)
    for a,b in zip(fields,fields[1:]):
        assert a['end']==b['start'] and a['x1'][:2]==b['x0'][:2]
    assert counts.get('1',0)>20 and counts.get('5',0)>0
    return counts


