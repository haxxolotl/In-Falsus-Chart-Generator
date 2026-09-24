from infalsus_studio.engine.guide_lines_v21 import parse_guides, bind_targets, align_root_motion
from infalsus_studio.engine.source_charts import parse_aff


def test_explicit_identity_only_and_noinput(tmp_path):
    path = tmp_path/'source.aff'
    path.write_text('''AudioOffset:-100
-
timing(0,120,4);
(0,1);
arc(0,1000,0,1,s,1,1,0,none,false)[arctap(250)];
timinggroup(noinput){arc(0,1000,0,1,s,1,1,0,none,true)[arctap(400)];};
arc(500,1000,0.5,0.5,s,1,1,0,none,true)[arctap(500),arctap(750)];
arc(1000,1200,-0.5,1.5,s,0,0,0,none,true);
''')
    raw, *_ = parse_guides(path)
    source = parse_aff(path)['notes']
    assert raw[0]['source_targets'] == []
    assert [b['event'] for b in raw[1]['source_targets']] == [3,4]
    for a in raw:
        for b in a['source_targets']:
            assert source[b['event']]['type'] == 'sky'
            assert source[b['event']]['start'] == b['time']
    note = dict(id=8,type=1,side=3,start=702,end=702,x0=[5,4],w0=[1,4],_source_event=3)
    arcs, receipt = bind_targets(raw,dict(time_scale=1,offset_seconds=.3),[note])
    assert len(arcs) == len(raw)
    bound = next(a for a in arcs if a.get('bindings'))
    assert bound['bindings'] == [dict(sourceTime=.7,time=.702,x=1.,event=3,note=8,side=3)]
    assert receipt['bound_targets'] == 1 and receipt['unbound_source_targets'] == 1
    assert arcs[-1]['x0'] == -.5 and arcs[-1]['x1'] == 1.5


def test_wide_center_flick_and_ambiguous_identity():
    raw = [dict(start=0,end=1000,x0=0,x1=1,y0=1,y1=1,easing='s',trace=True,noinput=False,
                source_targets=[dict(event=i,time=i*100) for i in range(3)])]
    common = dict(type=1,start=0,end=0,x0=[1,4],w0=[2,4])
    notes = [dict(common,id=1,side=1,_source_event=0),
             dict(common,id=2,type=4,side=4,start=100,x0=[1,5],w0=[1,5],_source_event=1),
             dict(common,id=3,side=3,start=200,_source_event=2),
             dict(common,id=4,side=2,start=200,_source_event=2)]
    result, receipt = bind_targets(raw,dict(time_scale=1,offset_seconds=0),notes)
    assert [round(b['x'],6) for b in result[0]['bindings']] == [.25,.3]
    assert receipt['unbound_source_targets'] == 1


def test_root_guides_follow_installed_scroll_without_flattening_local_groups():
    authored = [dict(id=0,scroll=[[1,128],[1.025,1]],hidden=[]),
                dict(id=3,scroll=[[0,-.125],[2,1]],hidden=[[3,True]])]
    native = [[1,4],[1.8,.12],[2,1]]
    result = align_root_motion(authored,native)
    assert result[0]['scroll'] == native
    assert result[1] == authored[1]
    native[0][1] = 9
    assert result[0]['scroll'][0][1] == 4
