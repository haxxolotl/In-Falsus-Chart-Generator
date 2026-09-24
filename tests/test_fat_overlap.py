from infalsus_studio.engine.fat_overlap_v27 import inspect, repair


def note(ident, start, end, left, width, typ=1, source=None):
    return dict(id=ident, start=start, end=end, type=typ, side=1,
                x0=[left,4], x1=[left,4], w0=[width,4], w1=[width,4],
                _source_type=source)


def test_wide_target_moves_off_a_held_key_without_moving_time():
    notes=[note(0,100,500,4,1,typ=2,source='hold'),
           note(1,200,200,3,2,source='floor')]
    before=[(n['start'],n['end']) for n in notes]
    assert len(inspect(notes))==1
    assert len(repair(notes))==1
    assert not inspect(notes)
    assert [(n['start'],n['end']) for n in notes]==before
    assert notes[1]['x0']==[2,4] and notes[1]['w0']==[2,4]


def test_fast_alternation_does_not_recreate_a_wide_collision():
    notes=[note(0,100,500,4,1,typ=2,source='hold'),
           note(1,299,299,2,1,source='floor'),
           note(2,306,306,3,2,source='floor')]
    repair(notes)
    assert not inspect(notes)
    assert notes[2]['x0']==[3,4] and notes[2]['w0']==[1,4]
