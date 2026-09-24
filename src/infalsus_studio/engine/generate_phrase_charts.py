"""Music-salience selection followed by audio-conditioned stock phrase retrieval."""
import pathlib,sys,json,math,copy,struct,collections,argparse
import numpy as np,joblib
from scipy import signal,ndimage
from .learn_phrase_library import descriptor,chart_metrics,rating_features
from .chart_encoder import Encoder
from .chart_emulator import Decoder
from .prepare_charts import tempo
from .prepare_audio import guid,sha,save
import pathlib
BASE=pathlib.Path(__file__).parent
ROOT=BASE/'revision2';OUT=BASE.parent/'outputs/arcaea-custom-pack-v2'
VERSION='phrase-v2.0'

def musical_events(t,f):
    # Distinct attacks can be foreground in different frequency regions.
    # Merge detections of one attack; do not impose a notes/second quota.
    smooth_chroma=ndimage.uniform_filter1d(f[:,12:24],size=9,axis=0)
    change=np.abs(smooth_chroma-np.roll(smooth_chroma,9,axis=0)).sum(axis=1)
    strength=np.maximum(f[:,0],np.max(f[:,1:5],axis=1)*.78)
    strength*=np.sqrt(np.minimum(f[:,5],1.6))
    importance=strength*(1+.45*np.minimum(change,1))
    candidates=[]
    for band in range(5):
        peaks,props=signal.find_peaks(f[:,band],distance=4,prominence=.12 if band==0 else .23,height=.18 if band==0 else .38)
        for ix,prom in zip(peaks,props['prominences']):
            if t[ix]<2.25 or t[ix]>t[-1]-.6 or f[ix,5]<.08:continue
            if importance[ix]<.22:continue
            candidates.append((int(ix),float(importance[ix]),band,float(prom)))
    kept=[];blocked=np.zeros(len(t),dtype=bool);radius=round(.048/float(t[1]-t[0]))
    for row in sorted(candidates,key=lambda r:r[1],reverse=True):
        ix=row[0]
        if not blocked[ix]:kept.append(row);blocked[max(0,ix-radius):ix+radius+1]=True
    kept.sort();return kept,importance

def salience_features(t,f,events):
    ids=np.array([r[0] for r in events]);times=t[ids];v=np.array([r[1:] for r in events]);
    return np.c_[f[ids],v,np.r_[1,np.diff(times)],np.r_[np.diff(times),1],
        ndimage.uniform_filter1d(f[:,5],size=35)[ids],
        ndimage.maximum_filter1d(f[:,0],size=17)[ids]]

def choose_events(rows,t,level):
    if level==3:return rows
    if not rows:return []
    # Preserve the musical foreground first. ULT retains most FBD events.
    fraction=[.20,.48,.82][level];count=max(1,round(len(rows)*fraction))
    chosen=[];gap=[.18,.11,.068][level]
    for r in sorted(rows,key=lambda r:r[1],reverse=True):
        if all(abs(float(t[r[0]])-float(t[q[0]]))>=gap for q in chosen):chosen.append(r)
        if len(chosen)>=count:break
    return sorted(chosen)

def finalize(notes,duration):
    notes.sort(key=lambda n:(n['start'],n['type']!=5,n.get('side',0)))
    # Preserve notes and intended lanes; end holds before subsequent attacks on that key.
    used=collections.defaultdict(list)
    for n in notes:
        if n['type'] not in (1,2):continue
        lo=round(n['x0'][0]/n['x0'][1]*4);width=max(1,round(n['w0'][0]/n['w0'][1]*4))
        for lane in range(lo,lo+width):used[lane].append(n)
    for lane,ns in used.items():
        for a,b in zip(ns,ns[1:]):
            if a['start']==b['start']:continue
            if a['end']>b['start']-40:a['end']=max(a['start'],b['start']-40)
            if a['type']==2 and a['end']-a['start']<100:a['type']=1;a['end']=a['start']
    # Merge exact duplicate keyboard moments after resampling a chord template.
    dedup=[];seen=set()
    for n in notes:
        k=(n['start'],n['side'],n['type'],tuple(n['x0']),tuple(n['w0']))
        if k in seen:continue
        seen.add(k);dedup.append(n)
    notes=dedup;groups={}
    for i,n in enumerate(notes):
        n['id']=i;n['group']=groups.setdefault(n.pop('_group'),i) if '_group' in n else i
        n['start']=max(1800,min(round(duration*1000)-600,n['start']))
        n['end']=max(n['start'],min(round(duration*1000)-500,n['end']))
    return notes

def generate(row,bank,scaler,model):
    a=np.load(BASE/'arcaea-preparation/features'/(row['base_name']+'.npz'));t=a['t'];f=a['features'];bpm=tempo(t,f)
    events,importance=musical_events(t,f)
    salience_model=joblib.load(ROOT/'salience-model.joblib')
    probabilities=salience_model['model'].predict_proba(salience_features(t,f,events))[:,1]
    strongest=np.quantile([r[1] for r in events],.92)
    events=[r for r,p in zip(events,probabilities) if p>=salience_model['threshold'] or r[1]>=strongest]
    duration=row['duration_seconds'];width=min(6,max(2,8*60/bpm))
    phrase_starts=np.arange(2.0,duration-.6,width);descriptors=np.stack([descriptor(t,f,s,min(duration,s+width)) for s in phrase_starts]);z=scaler.transform(descriptors)
    outputs=[];source_receipt=[]
    for level in range(4):
        library=[p for p in bank if p['level']==level];bx=scaler.transform(np.stack([p['descriptor'] for p in library]));notes=[];trace=[];selected_ms=[];prior=None
        for pi,start in enumerate(phrase_starts):
            end=min(duration-.5,start+width);part=[r for r in events if start<=t[r[0]]<end];chosen=choose_events(part,t,level)
            if not chosen:continue
            at=np.array([float(t[r[0]]) for r in chosen]);sal=np.array([r[1] for r in chosen]);selected_ms.extend(np.round(at*1000).astype(int).tolist())
            target_density=len(chosen)/(end-start)
            distance=np.mean((bx-z[pi])**2,axis=1)
            density_penalty=np.array([abs(math.log((p['density']+.5)/(target_density+.5))) for p in library])
            scores=distance+.7*density_penalty
            # Continue a matching native phrase when its musical context remains close.
            if prior is not None:
                for k,p in enumerate(library):
                    if p['song']==prior['song'] and abs(p['start']-(prior['start']+prior['duration']))<.02:scores[k]-=.10
            template=library[int(np.argmin(scores))];prior=template
            source=template['notes'];moments=sorted(set(n['start'] for n in source if n['type'] in (1,2,4)))
            if not moments:continue
            groups=[[n for n in source if n['type'] in (1,2,4) and n['start']==m] for m in moments]
            indices=np.rint(np.linspace(0,len(moments)-1,len(at))).astype(int)
            # Map gesture boundaries through the same chronological audio anchors.
            knots=np.r_[0,moments,1];values=np.r_[start,np.interp(np.linspace(0,1,len(moments)),np.linspace(0,1,len(at)),at),end]
            def warp(v):return round(float(np.interp(v,knots,values))*1000)
            mapped=[]
            for j,(second,mi) in enumerate(zip(at,indices)):
                ms=round(second*1000);accent=sal[j]>=np.quantile(sal,.95) and level==3
                moment=groups[mi]
                # At ordinary moments use at most two keyboard keys; wide chords need an accent.
                key_budget=4 if accent else 2;key_count=0
                for original in moment:
                    n=copy.deepcopy(original);n['start']=ms;n['end']=ms if n['type'] in (1,4) else max(ms+100,warp(n['end']))
                    for k in ['x0','x1','w0','w1']:n[k]=list(n[k][:2])
                    if n['type'] in (1,2):
                        width_keys=max(1,round(n['w0'][0]/n['w0'][1]*4))
                        if key_count>=key_budget:continue
                        width_keys=min(width_keys,key_budget-key_count);key_count+=width_keys
                        if n['side']==1:
                            lane=max(1,min(5-width_keys,round(n['x0'][0]/n['x0'][1]*4)))
                            n['x0']=n['x1']=[lane,4];n['w0']=n['w1']=[width_keys,4]
                        else:n['x0']=n['x1']=[0 if n['side']==2 else 5,4];n['w0']=n['w1']=[1,4]
                    if n['type']==2:n['_group']=f'{pi}:key:{original["group"]}:{j}'
                    n.pop('id',None);n.pop('group',None);mapped.append(n)
            for original in source:
                if original['type']!=5:continue
                n=copy.deepcopy(original);n['start']=warp(n['start']);n['end']=warp(n['end'])
                if n['end']-n['start']<15:continue
                for k in ['x0','x1','w0','w1']:n[k]=list(n[k][:2])
                n['_group']=f'{pi}:field:{n.pop("group")}';n.pop('id',None);mapped.append(n)
            notes.extend(mapped);trace.append({'target_start':float(start),'target_end':float(end),'musical_events':len(part),'selected_events':len(at),
                'source_song':template['song'],'source_chart':template['chart'],'source_start':template['start'],'source_middle_keys':template['middle_keys']})
        notes=finalize(notes,duration);m=chart_metrics(notes,duration);estimate=float(model.predict([rating_features(m)])[0]);rating=max(1,min(15,round(estimate)))
        chart={'name':f'{row["base_name"]}{level}.spc','bpm':bpm,'beats':4,'timing_data_hex':'','notes':notes}
        actual={n['start'] for n in notes if n['type'] in (1,2,4)};coverage=sum(ms in actual for ms in selected_ms)/max(1,len(selected_ms))
        assert coverage==1,(row['title'],level,coverage)
        outputs.append(chart);source_receipt.append({'level':level,'rating':rating,'rating_estimate':estimate,'metrics':m,
            'selected_musical_events':len(selected_ms),'selected_event_coverage':coverage,'full_fbd_events':len(events),'phrases':trace})
    # Ratings are calculated per chart. Ensure extra retained content cannot lower a tier's displayed rating.
    for i in range(1,4):source_receipt[i]['rating']=max(source_receipt[i]['rating'],source_receipt[i-1]['rating'])
    return outputs,source_receipt

def verify(chart,decoded,duration):
    assert len(chart['notes'])==len(decoded['notes'])
    for a,b in zip(chart['notes'],decoded['notes']):
        for k in ['id','group','side','type','start','end']:assert a[k]==b[k]
        for k in ['x0','x1','w0','w1']:assert a[k][:2]==list(b[k][:2])
        assert a['flags']==struct.unpack_from('<I',bytes.fromhex(b['extra']),36)[0]
        assert 1800<=b['start']<=b['end']<duration*1000
        if b['type'] in (1,2):assert 0<=b['key_start']<=b['key_end']<=5
    for lane in range(6):
        ns=[n for n in decoded['notes'] if n['type'] in (1,2) and n['key_start']<=lane<=n['key_end']]
        for a,b in zip(ns,ns[1:]):assert b['start']>=a['end'],('key overlap',lane,a['start'],a['end'],b['start'])
    return True

