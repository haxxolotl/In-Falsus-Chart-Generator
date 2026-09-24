"""Build render-only AFF guides, without reading or writing gameplay note objects.

Usage: python work/guide_lines_v21.py --revision work/revision21
Run --only custom_7a51800126e82.spc --output outputs/render-only-guides-v21/canary
before the full extraction. Input chart JSON supplies the installed scroll controls.
"""
import argparse, collections, hashlib, json, math, pathlib, re, struct

B = pathlib.Path(__file__).resolve().parent
GAME_HASH = 'ab1d8fa7739078510fab5f8580095c90ea0f5e9236ac9b918e934cb9ae1b7d9c'
PROTECTED = 'custom_473e29b521e70.spc'
TOKEN = re.compile(r'\s*(?:(timinggroup)\(([^)]*)\)\s*\{|(\})\s*;?|([a-z]*)\(([^)]*)\)(\s*\[[^\]]*\])?\s*;)')


def read(p):
    return json.loads(p.read_text(encoding='utf-8-sig'))


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def write(p, value):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(value, ensure_ascii=False, separators=(',', ':')), encoding='utf8')


def parse_guides(path):
    text = path.read_text(encoding='utf-8-sig')
    offset = float(re.search(r'AudioOffset:([^\r\n]+)', text)[1])
    body = re.split(r'\r?\n-\r?\n', text, maxsplit=1)[1]
    groups = [dict(attributes='', noinput=False, timings=[], visibility=[])]
    stack, arcs, scenes, ignored = [0], [], [], collections.Counter()
    pos = 0
    event_id = 0  # Same source-event order as source_charts.parse_aff.
    while body[pos:].strip():
        m = TOKEN.match(body, pos)
        if not m:
            raise ValueError(f'Unrecognized AFF syntax: {path}: {body[pos:pos+80]!r}')
        pos = m.end()
        group = groups[stack[-1]]
        if m[1]:
            groups.append(dict(attributes=m[2], noinput=group['noinput'] or 'noinput' in m[2],
                               timings=[], visibility=[]))
            stack.append(len(groups)-1)
        elif m[3]:
            assert len(stack) > 1
            stack.pop()
        else:
            kind, values = m[4], [v.strip() for v in m[5].split(',')]
            if kind == 'arc':
                trace = values[9] == 'true'
                taps = [float(v)+offset for v in re.findall(r'arctap\(([^)]+)\)', m[6] or '')]
                if not group['noinput'] and not trace:
                    event_id += 1
                ignored['arctaps_gameplay_unchanged'] += len(re.findall(r'arctap\(', m[6] or ''))
                if trace or group['noinput']:
                    arc = dict(start=float(values[0])+offset, end=float(values[1])+offset,
                               x0=float(values[2]), x1=float(values[3]), easing=values[4],
                               y0=float(values[5]), y1=float(values[6]), group=stack[-1],
                               noinput=group['noinput'], trace=trace,
                               source_targets=[] if group['noinput'] else [dict(event=event_id+i,time=t) for i,t in enumerate(taps)])
                    if arc['end'] < arc['start']:
                        raise ValueError(f'Reversed guide: {path}: {arc}')
                    assert arc['easing'] in ('s', 'b', 'si', 'so', 'sisi', 'siso', 'sosi', 'soso')
                    assert all(math.isfinite(arc[k]) for k in ('start', 'end', 'x0', 'x1', 'y0', 'y1'))
                    arcs.append(arc)
                if not group['noinput']:
                    event_id += len(taps)
            elif kind == 'timing':
                assert len(values) == 3
                time, bpm = float(values[0])+offset, float(values[1])
                assert math.isfinite(time) and math.isfinite(bpm)
                group['timings'].append((time, bpm))
            elif kind == 'scenecontrol' and len(values) == 4 and values[1] == 'hidegroup':
                time, hidden = float(values[0])+offset, int(values[3])
                assert math.isfinite(time) and hidden in (0, 1)
                group['visibility'].append((time, hidden))
            elif kind == 'scenecontrol' and len(values) == 4 and values[1] in ('enwidenlanes', 'enwidencamera'):
                time, duration, target = float(values[0])+offset, float(values[2]), int(values[3])
                assert math.isfinite(time) and math.isfinite(duration) and duration >= 0 and target in (0, 1)
                scenes.append((values[1], time, duration, target))
            elif kind in ('camera', 'scenecontrol'):
                ignored[kind+'_not_reproduced'] += 1
            elif group['noinput'] and kind in ('', 'hold'):
                # These are decorative note shapes, not guide-line bodies.
                ignored['noinput_'+('tap' if kind == '' else kind)+'_not_guide_geometry'] += 1
            elif kind not in ('', 'hold'):
                raise ValueError((path, kind))
            elif kind in ('', 'hold') and not group['noinput']:
                event_id += 1
    assert len(stack) == 1
    return arcs, groups, dict(ignored), offset, scenes


def source_group_motion(groups, alignment, duration):
    """Map each AFF group's signed BPM to render-only chart seconds."""
    scale, shift = alignment['time_scale'], alignment['offset_seconds']
    source_end = (duration-shift)*1000/scale
    weights = collections.Counter()
    root = sorted(groups[0]['timings'])
    for i, (time, bpm) in enumerate(root):
        end = root[i+1][0] if i+1 < len(root) else source_end
        if 20 <= bpm <= 600:
            weights[bpm] += max(0, min(end, source_end)-max(0, time))
    if not weights:
        raise ValueError('AFF source has no stable positive BPM for guide motion')
    base = weights.most_common(1)[0][0]

    def mapped(events):
        # Same-time source controls use the last authored value.
        by_time = {time*.001*scale+shift: value for time, value in events}
        return [[time, value] for time, value in sorted(by_time.items())]

    motion = []
    for index, group in enumerate(groups):
        timings = group['timings'] or root
        motion.append(dict(id=index,
                           scroll=mapped((time, bpm/base) for time, bpm in timings),
                           hidden=mapped(group['visibility'])))
    return base, motion


def align_root_motion(motion, native_scroll):
    """Move root guide art with the installed notes; retain local AFF groups."""
    assert sum(group['id'] == 0 for group in motion) == 1
    return [dict(group, scroll=[list(event) for event in native_scroll]) if group['id'] == 0
            else group for group in motion]


def map_lane_scene(scenes, alignment):
    scale, shift = alignment['time_scale'], alignment['offset_seconds']
    return {key: [[time*.001*scale+shift, span*.001*scale, target]
                  for kind,time,span,target in scenes if kind == source_kind]
            for key,source_kind in (('lanes','enwidenlanes'),('camera','enwidencamera'))}


def bind_targets(raw_arcs, alignment, notes):
    """Keep source art; bind only explicitly attached arctaps to final inputs.

    No nearest-time matching: dropped or ambiguous source identities remain art.
    Coordinates in bindings are native normalized sky-field positions.
    """
    scale, shift = alignment['time_scale'], alignment['offset_seconds']
    targets = collections.defaultdict(list)
    for note in notes:
        if note['type'] not in (1, 2, 4):
            continue
        ids = set(note.get('_source_events', []))
        if '_source_event' in note:
            ids.add(note['_source_event'])
        for identity in ids:
            targets[identity].append(note)
    result, counts = [], collections.Counter()
    for raw in raw_arcs:
        arc = {k:v for k,v in raw.items() if k != 'source_targets'}
        arc.update(start=raw['start']*.001*scale+shift, end=raw['end']*.001*scale+shift)
        bindings = []
        for source in raw.get('source_targets', []):
            candidates = targets[source['event']]
            if len(candidates) != 1:
                counts['unbound_source_targets'] += 1
                continue
            note = candidates[0]
            x = note['x0'][0]/note['x0'][1]
            width = note['w0'][0]/note['w0'][1]
            if note['side'] == 2:
                x = 0.0
            elif note['side'] == 3:
                x = 1.0
            elif note['side'] == 1:
                x = x + width/2 - .25
            else:
                x += width/2
            bindings.append(dict(sourceTime=source['time']*.001*scale+shift,
                time=note['start']/1000, x=x, event=source['event'], note=note['id'], side=note['side']))
        if bindings:
            # One time cannot point at two distinct translated inputs; preserve art
            # for that ambiguous moment rather than connecting to an arbitrary key.
            by_time = collections.defaultdict(list)
            for b in bindings:
                by_time[b['sourceTime']].append(b)
            bindings = [group[0] for group in by_time.values() if len({(b['time'],b['x'],b['side']) for b in group}) == 1]
            bindings.sort(key=lambda b:b['sourceTime'])
            if all(b['time'] < c['time'] for b,c in zip(bindings,bindings[1:])):
                arc['bindings'] = bindings
                counts['bound_targets'] += len(bindings)
                counts['bound_carriers'] += bool(bindings)
            else:
                counts['nonmonotonic_carriers_preserved_as_art'] += 1
        result.append(arc)
    result.sort(key=lambda a:(a['start'], a['end']))
    return result, dict(counts)


def easing(t, kind):
    if kind == 's': return t
    if kind == 'si': return math.sin(math.pi*t/2)
    if kind == 'so': return 1-math.cos(math.pi*t/2)
    if kind == 'b': return t*t*(3-2*t)
    raise ValueError(kind)


def position(arc, time):
    u = max(0, min(1, (time-arc['start'])/max(1e-9, arc['end']-arc['start'])))
    k = arc['easing']
    kx, ky = (k[:2], k[2:]) if len(k) == 4 else (k, 'b' if k == 'b' else 's')
    return (arc['x0']+(arc['x1']-arc['x0'])*easing(u, kx),
            arc['y0']+(arc['y1']-arc['y0'])*easing(u, ky))


def distance(time, scroll):
    previous, speed, total = 0, 1, 0
    for start, value in scroll:
        if start > time: break
        total += (start-previous)*speed
        previous, speed = start, value
    return total+(time-previous)*speed


def resolve_source(meta):
    rel = pathlib.Path(meta['source_chart'])
    work = B.parents[3]/'work'
    candidates = [work/'revision7/sources/aff'/rel, work/'revision10/sources/aff'/rel,
                  B/'revision7/sources/aff'/rel, B/'revision10/sources/aff'/rel, B/rel, rel]
    expected = meta.get('source_chart_sha256')
    existing = [p for p in candidates if p.is_file()]
    matching = [p for p in existing if not expected or sha(p) == expected]
    if not matching:
        raise ValueError(f'Source missing or hash mismatch: {rel}')
    return matching[0]


def ongeki_field_traces(chart):
    """Draw the visual guide's chosen solo field phrases without judging them."""
    arcs = []
    for note in chart['notes']:
        if note['type'] != 5 or note.get('_source_type') != 'field' or note.get('_ongeki_width_v27') != .9:
            continue
        x0 = note['x0'][0]/note['x0'][1] + note['w0'][0]/note['w0'][1]/2
        x1 = note['x1'][0]/note['x1'][1] + note['w1'][0]/note['w1'][1]/2
        arcs.append(dict(start=note['start']*.001, end=note['end']*.001,
                         x0=(x0-.25)*2, x1=(x1-.25)*2,
                         y0=.18, y1=.18, easing='s', group=0,
                         noinput=True, trace=True))
    return sorted(arcs, key=lambda arc:(arc['start'],arc['end']))


def build(revision, output, only=None):
    rows = read(revision/'all-charts.json')
    records, inventory, cache = [], [], {}
    for row in rows:
        for meta in row['charts']:
            if only and meta['name'] != only: continue
            item = dict(title=row['title'], name=meta['name'], payload_sha256=meta['sha256'])
            if meta['name'] == PROTECTED:
                inventory.append(dict(item, status='protected_bad_apple_override')); continue
            if not meta.get('source_chart'):
                inventory.append(dict(item, status='no_source_guides', reason='no AFF source attached')); continue
            if meta['source_chart'].lower().endswith('.json') and 'ongeki' in meta['source_chart'].lower():
                path = resolve_source(meta)
                assert read(path).get('source_format') == 'SDVX.in Ongeki visual guide'
                chart_path = revision/'charts'/(meta['name']+'.json')
                chart = read(chart_path)
                arcs = ongeki_field_traces(chart)
                if not arcs:
                    inventory.append(dict(item,status='source_has_no_guides',source=str(path),reason='no authored spotlight fields'));continue
                scroll = [[ms*.001,value] for _,ms,kind,value,_,_ in struct.iter_unpack(
                    '<qiidff',bytes.fromhex(chart['timing_data_hex'])) if kind==0]
                duration = max(n['end'] for n in chart['notes'])*.001
                records.append(dict(name=meta['name'],guid=meta['guid'],hash=meta['sha256'],
                                    title=row['title'],duration=duration,scroll=scroll,
                                    groups=[dict(id=0,scroll=scroll,hidden=[],twoSided=False)],arcs=arcs))
                inventory.append(dict(item,status='source_guides_staged',guides=len(arcs),
                                      noinput_guides=len(arcs),source=str(path),source_sha256=sha(path),
                                      art_kind='ongeki_visual_field_spotlights',
                                      chart_json_sha256=sha(chart_path)))
                continue
            if not meta['source_chart'].lower().endswith('.aff'):
                inventory.append(dict(item, status='no_source_guides', reason='source format has no AFF guide arcs')); continue
            path = resolve_source(meta)
            if path not in cache: cache[path] = parse_guides(path)
            raw_arcs, groups, exclusions, audio_offset, scenes = cache[path]
            if not raw_arcs and not scenes:
                inventory.append(dict(item, status='source_has_no_guides', source=str(path), exclusions=exclusions)); continue
            al = meta['alignment']
            assert al['status'] == 'aligned'
            scale, offset = al['time_scale'], al['offset_seconds']
            assert scale > 0 and math.isfinite(scale) and math.isfinite(offset)
            chart_path = revision/'charts'/(meta['name']+'.json')
            chart = read(chart_path)
            arcs, bindings = bind_targets(raw_arcs, al, chart['notes'])
            events = struct.iter_unpack('<qiidff', bytes.fromhex(chart['timing_data_hex']))
            # Native type 0 is piecewise constant scroll, independent of beat timing.
            scroll = [[ms*.001, value] for _, ms, kind, value, _, _ in events if kind == 0]
            assert scroll == sorted(scroll) and all(math.isfinite(v) for t,v in scroll)
            duration = max(max(n['end'] for n in chart['notes'])*.001,
                           al.get('local_duration', 0))
            base_bpm, motion = source_group_motion(groups, al, duration)
            motion = align_root_motion(motion, scroll)
            rec = dict(name=meta['name'], guid=meta['guid'], hash=meta['sha256'],
                       title=row['title'], duration=duration, scroll=scroll, groups=motion, arcs=arcs)
            if scenes:
                rec['laneScene'] = map_lane_scene(scenes, al)
            records.append(rec)
            error = max((abs(a[k]-(s[k]*.001*scale+offset)) for a,s in zip(
                sorted(arcs, key=lambda a:(a['group'],a['start'],a['end'])),
                sorted(raw_arcs, key=lambda a:(a['group'],a['start'],a['end']))) for k in ('start','end')), default=0)
            assert error < 1e-10
            inventory.append(dict(item, status='source_guides_staged', guides=len(arcs),
                                  noinput_guides=sum(a['noinput'] for a in arcs), source=str(path),
                                  source_sha256=sha(path), source_audio_offset_ms=audio_offset,
                                  source_stable_bpm=base_bpm, timing_groups=len(motion),
                                  source_timing_controls=sum(len(g['timings']) for g in groups),
                                  hidegroup_controls=sum(len(g['visibility']) for g in groups),
                                  lane_scene_controls=len(scenes),
                                  alignment=dict(scale=scale, offset_seconds=offset),
                                  maximum_alignment_error_seconds=error, exclusions=exclusions, target_bindings=bindings,
                                  local_group_attributes=[g['attributes'] for g in groups if g['attributes']],
                                  chart_json_sha256=sha(chart_path)))
    if only: assert len(inventory) == 1, f'Canary not found: {only}'
    data = dict(version=2, gameHash=GAME_HASH, charts=records)
    data_path = output/'payload/BepInEx/plugins/SourceGuides/guides.json'
    write(data_path, data)
    receipt = dict(status='staged_not_installed', revision=str(revision.resolve()),
                   metadata_sha256=sha(revision/'all-charts.json'),
                   data_sha256=sha(data_path), builder_sha256=sha(pathlib.Path(__file__)),
                   inventory=inventory, counts=dict(collections.Counter(i['status'] for i in inventory)),
                   charts=len(inventory), guides=sum(len(c['arcs']) for c in records),
                   gameplay_payloads_written=0, judged_notes_created=0, native_runtime_verified=False,
                   limitations=['AFF lane/camera widening is shown by side-rail guides; the native camera and input geometry are unchanged.',
                                'Ongeki spotlight traces are noninteractive source-derived art, not extra judged notes.',
                                'Source timing-group speed and hidegroup visibility apply to guide art only; other scene effects remain omitted.',
                                'No-input tap/hold sprites are counted but only arc bodies form guide geometry.',
                                'Playable native note sprites retain installed visual scroll; waterfall taps/holds and reversal of those sprites require a separate visual hook.',
                                'Source y is displayed above the native sky line at 0.35 of its width per source unit.'])
    write(output/'build-receipt.json', receipt)
    print(json.dumps({k:receipt[k] for k in ('status','charts','guides','counts')}, ensure_ascii=True))
    return data, receipt


def check():
    # Endpoints, combined easing, pause, seek, scroll transition and negative time.
    a = dict(start=10, end=12, x0=-.5, x1=1.5, y0=0, y1=1, easing='siso')
    assert position(a,10) == (-.5,0)
    assert abs(position(a,12)[0]-1.5) < 1e-12 and abs(position(a,12)[1]-1) < 1e-12
    assert abs(position(a,11)[0]-(-.5+math.sqrt(2))) < 1e-12
    controls = [[2, .5], [4, 1.5]]
    assert [distance(t, controls) for t in (-1,0,2,4,6)] == [-1,0,2,3,6]
    assert distance(3,controls) == distance(3,controls) == 2.5
    assert distance(2,controls) < distance(4,controls)


