"""Build the corpus-wide interaction/effects correction from revision 18."""
import argparse
import collections
import copy
import hashlib
import json
import struct
from pathlib import Path

from .chord_balance_v20 import apply as balance_chords, LIMITS as CHORD_LIMITS
from .contextual_fields_v17 import apply as shape_fields, V20_POLICY
from .cursor_motion_v17 import apply as limit_motion, peak
from .cursor_paths_v14 import audit as field_audit
from .effect_translation_v20 import apply as translate_effects, integrated_distance
from .finalize_motion_v17 import cover_flicks
from .final_shape_contract_v17 import repair as repair_final_shape
from .flick_contract_v17 import validate as validate_flicks
from .mouse_balance_v20 import apply as balance_mouse
from .native_curve_fit_v14 import edges
from .native_field_geometry import check as check_geometry
from .rewind_safety_v20 import repair_chart
from .readability_v20 import snap_micro_attacks, enforce_scroll_separation

B = Path(__file__).resolve().parent
OLD = B / 'revision18'
OUT = B / 'revision20'
RECORD = '<qiidff'
PLAYTEST_RATING_OVERRIDES = {('STARRED HEART', 3): 12}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf8')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def normalize_ids(notes):
    """Keep the native dense id/group contract after structural transforms."""
    group_ids = {}
    notes.sort(key=lambda n: (n['start'], n['end'], n['id']))
    for index, note in enumerate(notes):
        token = note['group']
        note['id'] = index
        note['group'] = group_ids.setdefault(token, index)


def field_metrics(notes):
    groups = collections.defaultdict(list)
    for note in notes:
        if note['type'] == 5:
            groups[note['group']].append(note)
    travel = seconds = 0.0
    widths = []
    for group in groups.values():
        for note in sorted(group, key=lambda n: n['start']):
            seconds += (note['end'] - note['start']) / 1000
            first, last = edges(note, 0), edges(note, 1)
            travel += abs(sum(last) / 2 - sum(first) / 2)
            widths.extend((first[1] - first[0], last[1] - last[0]))
    return dict(active_seconds=seconds, center_travel_per_second=travel / max(seconds, 1e-9),
                min_width=min(widths, default=0), max_width=max(widths, default=0),
                average_width=sum(widths) / max(1, len(widths)),
                peak_native_edge_speed=max((peak(n) for n in notes if n['type'] == 5), default=0))


def effect_metrics(chart, level):
    events = sorted((row[1], float(row[3])) for row in struct.iter_unpack(
        RECORD, bytes.fromhex(chart['timing_data_hex'])) if row[2] == 0)
    attacks = sorted(set(n['start'] for n in chart['notes'] if n['type'] in (1, 2, 4)))
    special = []
    for a, b in zip(attacks, attacks[1:]):
        active = next((value for time, value in reversed(events) if time <= (a + b) / 2), 1.0)
        if active < .5 and b - a <= 600:
            special.append(integrated_distance(events, a, b))
    threshold = (0, 0, .02, .015)[level]
    return dict(kind0_controls=len(events), negative_controls=sum(v < 0 for _, v in events),
                zero_controls=sum(v == 0 for _, v in events), special_attack_gaps=len(special),
                minimum_special_scroll_gap=min(special, default=None),
                special_scroll_gaps_below_review_floor=sum(abs(v) < threshold for v in special),
                review_floor=threshold)


def process(song, meta, duration):
    source = OLD / 'charts' / (meta['name'] + '.json')
    chart = read(source)
    before = copy.deepcopy(chart)
    before_fields = field_metrics(chart['notes'])
    effects = translate_effects(chart, meta, duration * 1000)
    chords = balance_chords(chart['notes'], meta['level'])
    mouse = balance_mouse(chart['notes'], meta)
    source_kind = Path(meta.get('source_chart', '')).suffix.lower()
    fields = dict(status='preserved', policy=None)
    motion = dict(status='preserved')
    final_shape = []
    if meta['level'] >= 2 or source_kind == '.json':
        fields = shape_fields(chart['notes'], meta['level'], meta, V20_POLICY)
        motion = limit_motion(chart['notes'], meta['level'])
        motion['flick_targets_repaired'] = cover_flicks(chart['notes'])
        final_shape = repair_final_shape(chart['notes'])
    micro_snap = snap_micro_attacks(chart['notes'])
    normalize_ids(chart['notes'])
    validate_flicks(chart['notes'])
    check_geometry(chart['notes'])
    assert not field_audit(chart['notes'])['errors']
    repaired, rewind = repair_chart(chart)
    chart = repaired
    scroll_readability = enforce_scroll_separation(chart, meta['level'])
    # The readability floor only raises nonnegative speed, but rerun the same
    # crossing proof so the final serialized timing is the audited state.
    chart, post_readability_rewind = repair_chart(chart)
    assert post_readability_rewind['changed_controls'] == 0
    readability = dict(micro_snap=micro_snap, scroll=scroll_readability,
                       post_scroll_crossing_audit=post_readability_rewind['after'])
    validate_flicks(chart['notes'])
    check_geometry(chart['notes'])
    assert not field_audit(chart['notes'])['errors']
    assert rewind['after']['ambiguous_pre_crossings'] == 0
    ids = [n['id'] for n in chart['notes']]
    assert ids == list(range(len(ids)))
    assert all(n['type'] in (1, 2, 4, 5) for n in chart['notes'])
    assert all(n['side'] in (1, 2, 3, 4) for n in chart['notes'])
    if meta['level'] < 3:
        assert not [n for n in chart['notes'] if n['type'] in (1, 2) and n['side'] == 1
                    and n['w0'][0] / n['w0'][1] > .251], 'fat keyboard note outside FBD'
    after_fields = field_metrics(chart['notes'])
    report = dict(title=song['title'], base_name=song['base_name'], name=meta['name'],
                  level=meta['level'], rating=meta['rating'], source_sha256=sha(source),
                  changed=chart != before, notes_before=len(before['notes']), notes_after=len(chart['notes']),
                  actions_before=sum(n['type'] in (1, 2, 4) for n in before['notes']),
                  actions_after=sum(n['type'] in (1, 2, 4) for n in chart['notes']),
                  fields_before=before_fields, fields_after=after_fields,
                  chords=chords, mouse=mouse, fields=fields, motion=motion,
                  final_shape_repairs=len(final_shape), effects=effects, rewind=rewind,
                  readability=readability,
                  effect_metrics=effect_metrics(chart, meta['level']))
    return chart, report




