import copy
import tempfile
from pathlib import Path
import pytest
from infalsus_studio.keyboard_contract import apply
from infalsus_studio import references, installer


def note(t,lane,width=1):
    return dict(type=1,side=1,start=t,end=t,x0=[lane,4],x1=[lane,4],w0=[width,4],w1=[width,4])


def test_preserves_authored_alternation():
    notes=[note(2000+i*70,1+i%2) for i in range(16)];original=copy.deepcopy(notes)
    apply(notes,3)
    assert notes==original


def test_consecutive_holds_can_share_an_exact_release_time():
    first=note(1000,2);first.update(type=2,end=1250)
    second=note(1250,2);second.update(type=2,end=1500)
    assert apply([first,second],3)['physical_assignment_checked']


def test_narrows_lower_tiers_and_removes_stacked_wide_targets():
    notes=[note(2000,1,2),note(2000,1)]
    apply(notes,3)
    assert sorted(n['x0'][0] for n in notes)==[1,2]
    assert all(n['w0']==[1,4] for n in notes)
    notes=[note(2000,1,2)]
    apply(notes,2)
    assert notes[0]['w0']==[1,4]


def test_named_source_order(tmp_path):
    source=tmp_path/'Song';source.mkdir()
    for name in ('easy','normal','hard','master'):
        (source/(name+'.aff')).write_text('AudioOffset:0\n-\ntiming(0,120,4);')
    result=references.find_references('Song','',source,tmp_path/'job',None)
    assert [Path(p).stem for p in result['paths']]==['easy','normal','hard','master']


def test_game_lock_blocks_second_installer(tmp_path):
    @installer._exclusive_game
    def nested(pack,game):
        with pytest.raises(installer.InstallError,match='Another Studio'):
            nested(pack,game)
        return 'ok'
    assert nested(tmp_path,tmp_path)=='ok'
