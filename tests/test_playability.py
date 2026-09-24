from __future__ import annotations

import copy

from infalsus_studio.playability import apply, audit


STRICT = {"tap_recovery_ms": 80, "near_ms": 80, "max_density": 8}


def note(time, lane, width=1, *, held=False, end=None):
    return {"type": 2 if held else 1, "side": 1, "start": time,
            "end": time if end is None else end, "x0": [lane, 4], "x1": [lane, 4],
            "w0": [width, 4], "w1": [width, 4]}


def test_repeated_chords_and_jacks_reassign_without_moving_times():
    notes = [note(0, 2), note(0, 3), note(40, 2), note(40, 3)]
    assert not audit(notes, 3, STRICT)["ok"]
    receipt = apply(notes, 3, STRICT)
    assert receipt["audit"]["ok"]
    assert [item["start"] for item in notes] == [0, 0, 40, 40]
    assert len({item["x0"][0] for item in notes[2:]}) == 2


def test_held_key_is_reserved_until_its_end():
    notes = [note(0, 2, held=True, end=500), note(100, 2)]
    assert not audit(notes, 3, STRICT)["ok"]
    receipt = apply(notes, 3, STRICT)
    assert receipt["audit"]["ok"]
    assert notes[1]["x0"] != [2, 4]


def test_clean_alternation_stays_byte_for_byte_unchanged():
    notes = [note(200 + step * 90, 1 + step % 2) for step in range(8)]
    original = copy.deepcopy(notes)
    receipt = apply(notes, 3, STRICT)
    assert receipt["status"] == "unchanged"
    assert notes == original


def test_wide_collision_is_narrowed_and_repair_is_idempotent():
    notes = [note(0, 1, 2), note(0, 2, 2)]
    assert any(item["kind"] == "wide_collision" for item in audit(notes, 3, STRICT)["violations"])
    first = apply(notes, 3, STRICT)
    assert first["audit"]["ok"]
    assert all(item["w0"] == [1, 4] for item in notes)
    original = copy.deepcopy(notes)
    assert apply(notes, 3, STRICT)["status"] == "unchanged"
    assert notes == original


def test_adjacent_simultaneous_targets_are_not_duplicate_stacks():
    notes = [note(0, 1), note(0, 2)]
    assert audit(notes, 3, STRICT)["ok"]


def test_density_thins_a_chord_layer_without_deleting_the_timestamp():
    notes = [note(0, lane) for lane in range(1, 5)]
    receipt = apply(notes, 3, {**STRICT, "max_density": 3})
    assert receipt["audit"]["ok"]
    assert notes and {item["start"] for item in notes} == {0}


def test_fbd_repeat_bottleneck_gets_wide_alternative_keys():
    notes=[note(t,2) for t in range(0,401,80)]
    result=apply(notes,3)
    assert result['audit']['ok']
    assert result['widened_targets']>0
    assert len(notes)==6 and any(n['w0']==[2,4] for n in notes)
    assert [n['start'] for n in notes]==list(range(0,401,80))
    assert apply(notes,3)['status']=='unchanged'


def test_same_repeat_in_ult_stays_narrow():
    notes=[note(t,2) for t in range(0,401,80)]
    assert apply(notes,2)['audit']['ok']
    assert all(n['w0']==[1,4] for n in notes)


def test_frame_spaced_triples_fail_even_below_one_second_limit():
    notes=[note(t,k) for t in (0,16,32,48,64) for k in (1,2,3)]
    assert any(v['kind']=='density' for v in audit(notes,3)['violations'])
    original_times={n['start'] for n in notes}
    result=apply(notes,3)
    assert result['audit']['ok']
    assert {n['start'] for n in notes}<=original_times
    assert len(notes)<15


def test_narrow_target_before_overlapping_wide_is_detected():
    notes=[note(0,2),note(1,1,2)]
    assert any(v['kind']=='wide_collision' for v in audit(notes,3)['violations'])
    assert apply(notes,3)['audit']['ok']
