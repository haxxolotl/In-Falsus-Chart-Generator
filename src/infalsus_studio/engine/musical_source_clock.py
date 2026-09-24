"""A shared musical measure clock, separate from difficulty-specific scroll effects."""
import math, hashlib, json, re
from .source_formats import parse_source

def build(root, source):
    paths = source.get('chart_paths') or {re.search(r'(\d)\.aff$',p)[1]: p for p in source['paths'] if re.search(r'(\d)\.aff$',p)}
    path = paths.get('0', paths[min(paths, key=int)])
    file = root / 'sources/aff' / path
    chart = parse_source(file)
    raw = sorted(chart['timings'], key=lambda e: e['time'])
    events, ignored = [], []
    method = chart.get('timing_provenance', {}).get('method', 'Lowest source difficulty musical timing; independent of playable difficulty scroll changes')
    # Kokoro encodes explicit measure boundaries with a dummy 99-beat meter.
    # Their intervals supply the bar clock, while the beat unit remains inferred.
    if sum(e['meter'] == 99 for e in raw) > len(raw) * .7:
        for a, b in zip(raw, raw[1:]):
            delta = b['time'] - a['time']
            if delta >= 600:
                events.append(dict(time=a['time'], bpm=240000/delta, meter=4))
        events.append(dict(events[-1], time=raw[-1]['time']))
        method = 'Authored explicit measure boundaries; four quarter-note beat unit inferred from interval, not dummy 160 BPM / 99 meter'
    else:
        for i, e in enumerate(raw):
            span = raw[i+1]['time']-e['time'] if i+1 < len(raw) else math.inf
            if not 20 <= e['bpm'] <= 400 or e['meter'] < .5 or e['meter'] > 32 or span < 20:
                ignored.append(e); continue
            out = dict(e)
            if out['meter'] > 12:
                # Extended visual measures suppress bar lines. Restore repeated
                # musical measures, keeping the next explicit phase reset.
                out['meter'] = 4 if out['meter'] % 4 == 0 else 3 if out['meter'] % 3 == 0 else 4
                ignored.append(dict(e, replacement_meter=out['meter']))
            events.append(out)
        if not events:
            raise ValueError(('No defensible musical timing', source['title']))
        if events[0]['time'] > raw[0]['time'] and raw[0]['bpm'] > 0:
            first = events[0]; period = 60000/first['bpm']*first['meter']
            start = first['time']-math.ceil((first['time']-raw[0]['time'])/period)*period
            events.insert(0, dict(first, time=start))
    return dict(timings=events, source_chart=path,
                source_sha256=hashlib.sha256(file.read_bytes()).hexdigest(),
                method=method, ignored_scroll_timings=ignored,
                inferred_beat_unit='inferred' in method.lower())
