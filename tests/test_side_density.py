from copy import deepcopy

from infalsus_studio.playability import audit as keyboard_audit
from infalsus_studio.side_density import apply as balance_sides


def tap(time, key, *, side=1, source_type='floor', identity=0):
    return dict(type=1,side=side,start=time,end=time,x0=[key,4],x1=[key,4],
                w0=[1,4],w1=[1,4],flags=0,id=identity,group=identity,
                _source_type=source_type,_source_event=identity)


def test_sky_side_burst_moves_to_available_center_without_shifting_music():
    notes=[tap(2000,1,identity=1),tap(2000,4,identity=2)]
    for index,time in enumerate(range(2110,3100,70),10):
        side=2 if index%2 else 3
        notes.append(tap(time,0 if side==2 else 5,side=side,source_type='sky',identity=index))
    before=deepcopy(notes)
    result=balance_sides(notes,3)
    assert result['moved']>0 and result['retained']>0 and not result['blocked']
    assert [(n['id'],n['start'],n['end'],n['type']) for n in notes] == [
        (n['id'],n['start'],n['end'],n['type']) for n in before]
    assert notes[:2]==before[:2]
    assert all(n['side']==1 and 1<=n['x0'][0]<=4 for n in notes if n['id'] in
               {move['id'] for move in result['moves']})
    assert keyboard_audit(notes,3)['ok']


def test_full_central_chord_retains_side_hit_and_reports_block():
    notes=[tap(2000,key,identity=key) for key in range(1,5)]
    notes.extend([tap(2000,0,side=2,source_type='sky',identity=10),
                  tap(2040,0,side=2,source_type='sky',identity=11)])
    before=deepcopy(notes)
    result=balance_sides(notes,3)
    assert result['moved']==0 and result['blocked']
    assert notes==before


def test_source_sky_position_guides_lane_choice():
    notes=[tap(2000,5,side=3,source_type='sky',identity=0),
           tap(2050,5,side=3,source_type='sky',identity=1)]
    source=[dict(type='sky',start=0,end=0,arc_start=0,arc_end=0,x0=.25,x1=.25,
                 y0=.5,y1=.5,easing='s') for _ in notes]
    result=balance_sides(notes,3,source)
    assert result['moved']==2 and notes[0]['x0']==[2,4]
    assert all(n['side']==1 for n in notes)
