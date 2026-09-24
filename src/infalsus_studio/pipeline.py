"""One job: acquire, calibrate, arrange/translate, validate, stage, install."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import threading
import time
import uuid

from .media import check_cancel, prepare


def data_root():
    return Path(os.environ.get("INFALSUS_STUDIO_HOME") or
        Path(os.environ.get("LOCALAPPDATA", Path.home())) / "InFalsusStudio")


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf8")


def guid(name):
    return hashlib.md5(name.encode()).hexdigest()


def _source_charts(row, refs, features, progress):
    """Keep real source clocks only when the held-out alignment gate passes."""
    from .engine.source_formats import parse_source
    from .engine.source_charts import align
    from .engine.convert_source_charts import adapt, source_grid
    from .engine.arrangement_metrics import extended
    from .engine.keyboard_expression_v21 import rating_evidence
    paths = [Path(p) for p in refs.get("paths", [])]
    if not paths: return None
    if len(paths)>4: paths=paths[:3]+[paths[-1]]
    parsed = [parse_source(p) for p in paths]
    # Discovery returns difficulty order. Do not reinterpret the most difficult
    # available chart as the first difficulty or recycle PRS for FBD.
    index_map = [0, 1, 2, 3] if len(paths) >= 4 else [0, 1, 2, 2] if len(paths) == 3 else [0, 0, len(paths)-1, len(paths)-1]
    alignment = align(parsed[-1], features["t"], features["features"])
    if alignment["status"] != "aligned" or alignment.get("candidate_advantage", 0) < .01:
        return dict(rejected=alignment)
    last = max(n["end"] for n in parsed[-1]["notes"]) * .001 * alignment['time_scale'] + alignment['offset_seconds']
    first = min(n["start"] for n in parsed[-1]["notes"]) * .001 * alignment['time_scale'] + alignment['offset_seconds']
    # A short game edit must not silently terminate a full-length music file.
    if last < row['duration_seconds'] - 15 or first > 15 or last > row['duration_seconds'] + .5:
        return dict(rejected=dict(alignment, reason='reference does not cover this audio edit'))
    clock_meta = dict(title=row['title'], chart_paths={str(i):str(p.resolve()) for i,p in enumerate(paths)})
    charts, metas = [], []
    for level, source_index in enumerate(index_map):
        notes, conversion = adapt(parsed[source_index], alignment, level,
            thin=len(paths)==3 and level==2, derived_level=level if len(paths)<3 else None)
        end_ms = int(row['duration_seconds']*1000)-1
        notes = [n for n in notes if n['start'] < end_ms]
        for n in notes: n['end'] = min(n['end'], end_ms)
        grid, timing = source_grid(parsed[source_index], alignment, clock_meta, row['duration_seconds'])
        chart = dict(name=f"{row['base_name']}{level}.spc", notes=notes,
            bpm=grid['nominal_bpm'], beats=grid['segments'][0]['beats'], timing_data_hex=timing)
        rating = rating_evidence(notes, level)['recommended_rating']
        metas.append(dict(name=chart['name'], level=level, rating=rating,
            metrics=extended(notes,row['duration_seconds']), musical_grid=grid,
            source_chart=str(paths[source_index].resolve()), source_difficulty=source_index,
            source_chart_sha256=hashlib.sha256(paths[source_index].read_bytes()).hexdigest(),
            alignment=alignment, conversion=conversion, source_count=len(paths)))
        charts.append(chart)
    return dict(charts=charts, metas=metas, alignment=alignment)


def _repair(row, charts, metas, job, scroll_effects):
    from .playability import apply as playable, audit as keyboard_audit, load_profile
    from .engine.lane_hints_v14 import apply as lane_hints
    from .engine import prepare_revision20 as v20, prepare_revision21 as v21
    from .engine.source_difficulty_v21 import normalize
    from .engine.keyboard_expression_v21 import apply as express, interaction_metrics
    from .engine.fix_v10_loading_buffers import merge_fields
    from .engine.cursor_paths_v14 import repair
    from .engine.cursor_continuity import regroup
    from .engine.native_field_geometry import canonicalize
    from .engine.native_note_buffer import check
    from .engine.finalize_motion_v17 import cover_flicks
    from .engine.final_shape_contract_v17 import repair as shape
    from .engine.local_beat_snap import snap_chart
    from .side_density import apply as balance_sides
    from .engine.flick_zone_v26 import repair as cover_final_flicks, inspect as inspect_final_flicks
    from .engine.fat_overlap_v27 import repair as clear_wide_overlaps, inspect as inspect_wide_overlaps
    outputs, reports = [], []
    playability_profile=load_profile(data_root()/'calibration/corpus')
    for chart, meta in zip(charts, metas):
        meta.update(name=chart['name'], guid=guid(chart['name']), title=row['title'])
        from .keyboard_contract import apply as keyboard_contract
        if meta.get('source_chart') and meta.get('source_count',0)>=3:
            from .engine.source_difficulty_v21 import restore_floor_events
            restore_floor_events(chart,meta,ease_highest=meta['source_count']==3 and meta['level']==2)
        keyboard_contract(chart['notes'],meta['level'])
        # These native contracts precede the newer expression pass in the
        # original corpus pipeline, so fresh songs must run them too.
        express(chart['notes'], meta)
        chart['notes'], _ = merge_fields(chart['notes'])
        repair(chart['notes'],meta['level'])
        regroup(chart['notes'])
        cover_flicks(chart['notes'])
        shape(chart['notes'])
        normalize(chart['notes']); canonicalize(chart['notes'])
        if not chart['timing_data_hex']:
            grid = meta.get('grid', {})
            phase = grid.get('phase', 0.)
            chart['timing_data_hex'] = struct.pack('<qiidff',0,round(phase*1000),1,chart['bpm'],4,0).hex()
        save(job/'original/charts'/(chart['name']+'.json'),chart)
        chart, report = v20.process(row,meta,row['duration_seconds'])
        if not scroll_effects:
            chart['timing_data_hex'] = b''.join(struct.pack('<qiidff',*r) for r in
                struct.iter_unpack('<qiidff',bytes.fromhex(chart['timing_data_hex'])) if r[2]!=0).hex()
        outputs.append(chart); reports.append(dict(v20=report))
    row['charts'] = metas
    chart_map = {c['name']:c for c in outputs}
    for i, (chart, meta) in enumerate(zip(outputs,metas)):
        selection = dict(status='source_matches' if meta.get('source_chart') else 'original_arrangement',
            expected_difficulty=meta.get('source_difficulty'), donor_level=meta['level'],
            distinct_sources=meta.get('source_count',0),level=meta['level'],
            ease_highest=meta.get('source_count')==3 and meta['level']==2)
        chart, meta, report = v21.process(row,meta,chart_map,selection,roles=True,duration=row['duration_seconds'])
        report['local_beat_snap'] = snap_chart(chart)
        source = None
        if meta.get('source_chart'):
            from .engine.keyboard_expression_v21 import source_path, load_source
            path = source_path(meta)
            if path:
                source = load_source(path)
        report['side_density'] = balance_sides(chart['notes'],meta['level'],source)
        report['playability'] = playable(chart['notes'],meta['level'],playability_profile)
        if not report['playability']['audit']['ok']:
            raise ValueError('Unresolved keyboard playability violations; pack was not installed.')
        report['keyboard_contract'] = keyboard_contract(chart['notes'],meta['level'])
        report['wide_overlap_final'] = clear_wide_overlaps(chart['notes'])
        assert not inspect_wide_overlaps(chart['notes']), 'Wide floor target covers another floor target'
        report['playability_final'] = keyboard_audit(chart['notes'],meta['level'],playability_profile)
        if not report['playability_final']['ok']:
            raise ValueError('Final wide-note placement exceeds the keyboard playability envelope.')
        report['flick_zone_final'] = len(cover_final_flicks(chart['notes']))
        assert not inspect_final_flicks(chart['notes'])[0], 'Flick contact outside final cursor zone'
        # The final repair is downstream of source recovery. Refresh EVO hints
        # for any reassigned keys without changing the musical or scroll clock.
        normalize(chart['notes'])
        report['lane_hints_final'] = lane_hints(chart,meta['level'])
        meta['metrics'].update(interaction_metrics(chart['notes']))
        if not chart['notes']: raise ValueError('Generated chart is empty.')
        reports[i]['v21'] = report
        reports[i]['native_capacity'] = check(chart['notes'],chart['timing_data_hex'])
        outputs[i], metas[i] = chart, meta
    return outputs, metas, reports


def run_job(options, progress=print, cancel=None):
    import numpy as np
    from . import installer
    from .calibration import ensure_models
    from .references import find_references
    from .engine.audio_features import extract
    from .engine.runtime import configure
    from .engine.chart_encoder import Encoder
    from .engine.decode_corpus import decode_audio
    game = Path(options.get('game_root') or installer.discover_game() or '')
    if not (game/'GameAssembly.dll').is_file():
        raise ValueError('Select your installed In Falsus folder first.')
    home = data_root(); home.mkdir(parents=True,exist_ok=True)
    if shutil.disk_usage(home).free < 3*1024**3:
        raise ValueError('At least 3 GB of free space is needed for media and temporary calibration files.')
    job = home/'jobs'/(time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6])
    job.mkdir(parents=True)
    result = dict(job_dir=str(job), installed=False, warnings=[], status='preparing')
    # Prevent shared module configuration and cache mutation across simultaneous
    # jobs in this process. Separate app processes have calibration/install locks.
    try:
        progress('Reading audio and measuring its volume…')
        audio = prepare(str(options['media']),job,options.get('normalize_audio',True),cancel)
        # Vorbis container serials can change between encodes. Identify the song
        # by decoded input audio so options/retries update it instead of duplicating it.
        base = 'custom_'+audio['input_sha256'][:12]
        row = dict(title=options.get('title') or audio['title'],artist=options.get('artist') or audio['artist'],
            base_name=base,audio_guid=guid(base+'.ogg'),duration_seconds=audio['duration_seconds'])
        if row['artist']=='Unknown artist': result['warnings'].append('No artist tag was found. Set Artist before sharing this pack.')
        progress('Preparing the local official-chart calibration…')
        models = ensure_models(game,home/'calibration',progress,cancel)
        check_cancel(cancel)
        gen,convert,v20,v21 = configure(models,job)
        progress('Analyzing musical phrases and timing…')
        features = extract(audio['wav'])
        feature_path = job/'features.npz'; np.savez_compressed(feature_path,**features)
        refs = dict(status='disabled',paths=[])
        if options.get('source_search',True):
            progress('Looking for matching source charts…')
            refs = find_references(row['title'],row['artist'],options.get('reference_dir'),job,progress,cancel)
            check_cancel(cancel)
        result['warnings'].extend(refs.get('warnings',[])); save(job/'references.json',refs)
        translated = _source_charts(row,refs,features,progress)
        if translated and 'charts' in translated:
            charts,metas = translated['charts'],translated['metas']
            progress('Aligned the source chart to this audio; translating four difficulties…')
        else:
            if translated:
                save(job/'rejected-alignment.json',translated)
                result['warnings'].append('The reference did not align confidently to this audio edit; used an audio-based arrangement instead.')
            elif options.get('source_search',True):
                result['warnings'].append('No usable reference was found; used an audio-based arrangement.')
            progress('Arranging four difficulties using the calibrated official patterns…')
            charts,metas = gen.generate(row,gen.Matcher(),None,features_path=feature_path)
        check_cancel(cancel)
        progress('Checking timing, cursor continuity, readable inputs, effects and native limits…')
        charts,metas,reports = _repair(row,charts,metas,job,options.get('scroll_effects',True))
        progress('Encoding and round-trip checking every chart against your installed game…')
        encoder = Encoder(game)
        for chart,meta in zip(charts,metas):
            check_cancel(cancel)
            raw = encoder.encode(chart,chart['name'])
            path = job/'encoded'/meta['guid']; path.parent.mkdir(exist_ok=True);path.write_bytes(raw)
            decoded = encoder.decoder.decode(raw,chart['name'])
            if len(decoded['notes'])!=len(chart['notes']) or decoded['timing_data_hex']!=chart['timing_data_hex']:
                raise ValueError('Native chart round trip did not match.')
            meta.update(payload=str(path),bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
            save(job/'charts'/(chart['name']+'.json'),chart)
        row['charts']=metas
        source_game = str(refs.get('source_game', '')).lower()
        row['category_slug'] = 'custom-arcaea' if source_game=='arcaea' else 'custom-ongeki' if 'ongeki' in source_game else 'custom-other'
        row['audio_bytes']=audio['ogg'].stat().st_size
        # Preview a sustained high-energy passage, excluding the lead-in/outro.
        t,f=features['t'],features['features']
        from scipy.ndimage import uniform_filter1d
        energy=uniform_filter1d(np.maximum(0,f[:,0]),max(1,round(15/(t[1]-t[0]))))
        valid=np.where((t>=max(10,row['duration_seconds']*.2))&(t<row['duration_seconds']-18))[0]
        peak=int(valid[np.argmax(energy[valid])]) if len(valid) else 0
        row['preview_start_seconds']=max(2.,float(t[peak])-5)
        row['preview_end_seconds']=min(row['duration_seconds'],row['preview_start_seconds']+20)
        encoded_audio=job/'encoded'/row['audio_guid'];encoded_audio.write_bytes(decode_audio(audio['ogg'].read_bytes()))
        save(job/'song.json',row);save(job/'validation.json',reports);save(job/'audio-metrics.json',audio['audio_metrics'])
        progress('Staging a backed-up install against the current song catalog…')
        pack=installer.prepare_install(job,game,row,metas,encoded_audio,Path(options['cover']) if options.get('cover') else None)
        from .extras import stage_extras
        result['warnings'].extend(stage_extras(pack,game,job,row,charts,options))
        result.update(pack_dir=str(pack),songs=[row['title']],status='ready')
        check_cancel(cancel)
        if options.get('install_after',True):
            progress('Installing the checked pack…')
            try:
                result['installation']=installer.install_pack(pack,game)
                result.update(installed=True,status='installed')
            except installer.InstallError as error:
                result['warnings'].append(f'Pack is ready but installation is blocked: {error}')
                result['status']='ready_install_blocked'
        progress('Complete.' if result['installed'] else 'Pack is ready; see the result for installation status.')
        save(job/'result.json',result)
        return result
    except Exception as error:
        cancelled=isinstance(error,InterruptedError) or (cancel is not None and cancel.is_set())
        result.update(status='cancelled' if cancelled else 'failed',error=str(error))
        save(job/'result.json',result)
        raise
