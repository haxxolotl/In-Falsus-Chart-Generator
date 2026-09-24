"""Integrate source-difficulty fidelity and authored input expression once per chart."""
import argparse
import collections
import copy
import hashlib
import json
from pathlib import Path

from . import source_difficulty_v21 as mapping
from . import keyboard_expression_v21 as expression
from .cursor_paths_v14 import audit as field_audit
from .flick_contract_v17 import validate as validate_flicks
from .native_field_geometry import check as check_geometry
from .readability_v20 import enforce_scroll_separation
from .rewind_safety_v20 import repair_chart
from .source_formats import parse_source
from .lane_hints_v14 import apply as lane_hints

B = Path(__file__).resolve().parent
OLD = B / 'revision20'
OUT = B / 'revision21'
SPECIAL = 'custom_473e29b521e70.spc'
read, save = mapping.read, mapping.save


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def process(song, meta, charts, selection, roles=False, duration=None):
    before = charts[meta['name']]
    if meta['name'] == SPECIAL:
        return copy.deepcopy(before), copy.deepcopy(meta), dict(status='protected_current_live_override', changed=False)
    chart, meta = mapping.prepare_mapped_chart(song, charts, meta['level'], selection)
    meta['title'] = song['title']
    if duration is None:
        duration = next(s['duration_seconds'] for s in read(OLD/'song-manifest.json') if s['base_name']==song['base_name'])
    meta['audio_end_ms'] = int(duration*1000)-1
    source = parse_source(mapping.resolve_source(meta['source_chart'])) if meta.get('source_chart') else None
    # Source selection applies to every chart. Full-fidelity restoration applies
    # to every highest tier; retain accepted easier adaptations at other tiers
    # unless the selected physical source itself needs replacement.
    eligible = source and (meta['level'] == 3 or selection['status']=='correction_required')
    role_report = dict(status='not_requested')
    if roles and eligible:
        from .source_roles_v21 import apply
        role_report = apply(chart, meta, selection, source=source)
    floor = mapping.restore_floor_events(chart, meta, ease_highest=selection['ease_highest'], source=source) if eligible else None
    transformed = expression.apply(chart['notes'], meta, source=source)
    mapping.normalize(chart['notes'])
    hints = lane_hints(chart, meta['level'])
    if meta['level'] < 3:
        assert not [n for n in chart['notes'] if n['type'] in (1,2) and n['side']==1
                    and n['w0'][0]/n['w0'][1]>.251], 'Wide key targets outside FBD'
    # Deemo positions are choreography, not discrete physical floor keys. Its
    # expression transform may use side/flick inputs but must keep every attack.
    if floor:
        if source.get('source_format') == 'Deemo DS':
            actual = {n.get('_source_event'): n for n in chart['notes'] if n.get('_source_type') == 'floor'}
            for expected in floor['expected_floor']:
                note = actual[expected['event']]
                assert note['type'] in (1, 4) and note['start'] == note['end'] == expected['time']
        else:
            mapping.assert_source_floor(chart, floor)
    if roles and eligible:
        from .source_roles_v21 import assert_roles
        assert_roles(chart, role_report)
    check_geometry(chart['notes'])
    validate_flicks(chart['notes'])
    assert not field_audit(chart['notes'])['errors']
    assert all(0<=n['start']<=n['end']<=meta['audio_end_ms'] for n in chart['notes']), 'Notes outside local audio'
    chart, rewind = repair_chart(chart)
    readability = enforce_scroll_separation(chart, meta['level'])
    chart, final_rewind = repair_chart(chart)
    assert final_rewind['after']['ambiguous_pre_crossings'] == 0
    metrics = expression.interaction_metrics(chart['notes'])
    rating = expression.rating_evidence(chart['notes'], meta['level'])
    prior_rating = meta['rating']
    if transformed['status'] == 'changed':
        meta['rating'] = rating['recommended_rating']
    elif floor and floor['previously_missing'] > 0:
        # Recovery never makes an unchanged workload easier. Keep established
        # labels unless the recovered material needs a higher official analogue.
        meta['rating'] = max(meta['rating'], rating['recommended_rating'])
    meta.setdefault('rating_evidence', {})['v21'] = rating
    meta.setdefault('conversion', {})['v21'] = dict(mapping=selection, source_roles=role_report,
        floor={k:v for k,v in (floor or {}).items() if k != 'expected_floor'},
        expression=transformed, rewind=rewind, readability=readability, lane_hints=hints)
    meta['metrics'].update({k:v for k,v in metrics.items() if k != 'attacks'})
    meta['metrics']['strikes'] = metrics['attacks']
    report = dict(status='passed', changed=chart != before, mapping=selection['status'],
        floor=floor, source_roles=role_report, expression=transformed,
        prior_rating=prior_rating, rating=meta['rating'], notes_before=len(before['notes']),
        notes_after=len(chart['notes']), attacks=metrics['attacks'],
        rewind=rewind, readability=readability, final_rewind=final_rewind['after'])
    return chart, meta, report




