"""Losslessly merge Ongeki raster field pieces and prove native buffer limits."""
import bisect,copy,json,pathlib,sys,shutil
from fractions import Fraction
from .chart_encoder import Encoder
from .chart_emulator import Decoder
from .arrangement_metrics import extended
from .generate_arranged_charts import verify
from .native_note_buffer import check,native_boundary_proof
from .prepare_audio import save,sha
import pathlib
B=pathlib.Path(__file__).parent
R=B/'revision10';O=B.parent/'outputs/source-chart-pack-v10'

def merge_fields(notes):
    flick_times=sorted(n['start'] for n in notes if n['type']==4)
    fields=[n for n in notes if n['type']==5 and n.get('_source_type')=='field']
    out=[];sources=[]
    for n0 in fields:
        n=copy.deepcopy(n0);a=out[-1] if out else None
        can_merge=(a and a['end']==n['start'] and a['x1']==n['x0'] and a['w1']==n['w0']
                   and a['group']==n['group'] and a.get('flags',0)==n.get('flags',0)
                   and bisect.bisect_left(flick_times,a['start'])==bisect.bisect_right(flick_times,n['end']))
        if can_merge:
            val=lambda n,k:Fraction(*n[k][:2])
            can_merge=all((val(a,k+'1')-val(a,k+'0'))*(n['end']-n['start'])==
                          (val(n,k+'1')-val(n,k+'0'))*(a['end']-a['start']) for k in ['x','w'])
        if can_merge:
            a.update(end=n['end'],x1=n['x1'],w1=n['w1'])
            a.setdefault('_source_events',[a.get('_source_event')]).extend(n.get('_source_events',[n.get('_source_event')]))
        else:out.append(n)
    result=[copy.deepcopy(n) for n in notes if not(n['type']==5 and n.get('_source_type')=='field')]+out
    result.sort(key=lambda n:(n['start'],n['id']))
    groups={}
    for i,n in enumerate(result):n['id']=i;n['group']=groups.setdefault(n['group'],len(groups))
    # Compare complete source and output geometry on the union of breakpoints.
    for old in fields:
        target=next(n for n in out if n['start']<=old['start'] and n['end']>=old['end'])
        for tm,suffix in [(old['start'],'0'),(old['end'],'1')]:
            u=Fraction(tm-target['start'],target['end']-target['start'])
            for k in ['x','w']:
                expected=Fraction(*old[k+suffix][:2]);actual=Fraction(*target[k+'0'][:2])+(Fraction(*target[k+'1'][:2])-Fraction(*target[k+'0'][:2]))*u
                assert expected==actual,'field geometry changed'
    clean=lambda n:{k:v for k,v in n.items() if k not in ['id','group']}
    assert [clean(n) for n in notes if n['type']!=5]==[clean(n) for n in result if n['type']!=5], 'attacks or keyboard holds changed'
    return result,len(fields)-len(out)

