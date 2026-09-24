"""Translate authored source scroll gestures separately from the musical clock."""
import json, math, pathlib, re, struct, hashlib
B=pathlib.Path(__file__).resolve().parent
R=B/'revision14/scroll-effects'

def read(p): return json.loads(p.read_text(encoding='utf-8-sig'))
def save(p,x):
 p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,ensure_ascii=False,indent=2),encoding='utf8')

def source_events(path):
 text=path.read_text(encoding='utf8');offset=float(re.search(r'AudioOffset:([^\r\n]+)',text)[1])
 body=text.split('\n-\n',1)[-1];groups=[dict(attributes='',timings=[],scenes=[])];stack=[0]
 token=re.compile(r'timinggroup\(([^)]*)\)\s*\{|(\})|\b(timing|scenecontrol)\(([^)]*)\)')
 for m in token.finditer(body):
  if m[1] is not None:
   groups.append(dict(attributes=m[1],timings=[],scenes=[]));stack.append(len(groups)-1)
  elif m[2]:
   if len(stack)>1:stack.pop()
  else:
   v=m[4].split(',');g=groups[stack[-1]]
   if m[3]=='timing':g['timings'].append(dict(time=float(v[0])+offset,bpm=float(v[1]),meter=float(v[2])))
   else:g['scenes'].append(dict(time=float(v[0])+offset,kind=v[1],arguments=v[2:]))
 return groups

def plan(meta,duration_ms):
 path=B/'revision7/sources/aff'/meta.get('source_chart','')
 if path.suffix!='.aff' or not path.is_file():return [],dict(status='no_machine_readable_scroll_source')
 groups=source_events(path);level=int(meta['level']);al=meta['alignment'];scale=al['time_scale'];offset=al['offset_seconds']*1000
 clocks=meta['timing_map']['segments'];raw=[];scenes=[]
 def mapped(t):return round(t*scale+offset)
 def musical_bpm(t):return next((e['bpm']*scale for e in reversed(clocks) if e['start']*1000<=mapped(t)),clocks[0]['bpm']*scale)
 for gi,g in enumerate(groups):
  scenes.extend(dict(s,group=gi,local_ms=mapped(s['time'])) for s in g['scenes'])
  if 'noinput' in g['attributes']:continue
  ev=g['timings']
  for i,e in enumerate(ev):
   if i+1==len(ev):continue
   end=ev[i+1]['time'];span=end-e['time'];ratio=e['bpm']/musical_bpm(e['time'])
   # Initialization angle compensation is not a deliberate scroll gesture.
   if span<=0 or abs(ratio-1)<.04:continue
   if abs(ratio)>20:continue # teleport/hidden geometry cannot become a whole-field jump
   if mapped(e['time'])<0 or mapped(end)>=duration_ms-1500:continue
   raw.append(dict(source_start=e['time'],source_end=end,start=mapped(e['time']),end=mapped(end),ratio=ratio,group=gi))
 # Duplicated left/right timing groups describe one gesture, not stacked effects.
 uniq={ (e['start'],e['end'],round(e['ratio'],5)):e for e in raw };raw=sorted(uniq.values(),key=lambda e:(e['start'],e['group']))
 if level<2:return [],dict(status='reviewed_steady_scroll_for_lower_difficulty',source_gestures=len(raw),scenes=scenes)
 # Readability envelope: positive speed avoids global reversals of unrelated notes.
 # Preserve gesture times; soften negative local arc animation to a slowdown.
 lo,hi=(.72,1.18) if level==2 else (.48,1.35)
 points={};used=[];skipped=[]
 for e in raw:
  span=e['end']-e['start']
  if span<35:
   # A 1ms source jump works on selected layers. Globalizing it is unreadable.
   skipped.append(dict(e,reason='layer_specific_impulse_not_safe_as_global_scroll'));continue
  ratio=e['ratio'];speed=max(lo,min(hi,ratio if ratio>0 else lo))
  if abs(speed-1)<.04:continue
  # Prevent a source group with indefinite slow scroll from changing the whole song.
  if span>12000:skipped.append(dict(e,reason='persistent_local_layer_speed'));continue
  used.append(dict(e,speed=speed))
 for t in sorted({t for e in used for t in (e['start'],e['end'])}):
  active=[e['speed'] for e in used if e['start']<=t<e['end']]
  slow=[v for v in active if v<1]
  points[t]=min(slow) if slow else max(active,default=1.0)
 # Ease changes in 60ms steps. Restore exactly 1 after the final gesture.
 out=[];previous=1.0
 for t,speed in sorted(points.items()):
  if abs(speed-previous)<1e-6:continue
  previous_time=out[-1][0] if out else 0
  ramp=min(180,max(0,t-previous_time))
  for j in range(1,4):
   tt=round(t-ramp+ramp*j/3);v=previous+(speed-previous)*j/3
   if tt>=0:out.append((tt,round(v,6)))
  previous=speed
 if out and out[-1][1]!=1:out.append((min(round(duration_ms)-1500,out[-1][0]+300),1.0))
 merged={t:v for t,v in out};out=[]
 for t,v in sorted(merged.items()):
  if out and t-out[-1][0]<40:
   out[-1]=(t,v)
  else:out.append((t,v))
 # Global speed changes get a bounded slew, including consecutive layer events.
 for i in range(1,len(out)):
  t,v=out[i];pt,pv=out[i-1];limit=(t-pt)/1000*2.5
  out[i]=(t,round(min(pv+limit,max(pv-limit,v)),6))
 if out and out[-1][1]!=1:out.append((out[-1][0]+round(abs(1-out[-1][1])/2.5*1000)+40,1.0))
 return out,dict(status='translated' if out else 'reviewed_no_safe_global_gesture',source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),source_path=str(path),source_gestures=len(raw),translated=used,skipped=skipped,scenes=scenes,limits=[lo,hi],camera_and_layer_visibility='not_translated_to_global_camera',events=out)

def apply(chart,meta,duration_ms):
 events,receipt=plan(meta,duration_ms)
 if not events:return receipt
 old=[list(x) for x in struct.iter_unpack('<qiidff',bytes.fromhex(chart['timing_data_hex']))]
 musical=[x[1:] for x in old if x[2] in (1,2)]
 # Existing no-op type0 ending records retain the runtime end deadline.
 out=old+[[0,t,0,v,chart.get('beats',4),0.0] for t,v in events]
 out.sort(key=lambda e:(e[1],e[2]!=0))
 for i,e in enumerate(out):e[0]=i
 assert [x[1:] for x in out if x[2] in (1,2)]==musical
 assert len(out)<1024
 chart['timing_data_hex']=b''.join(struct.pack('<qiidff',*e) for e in out).hex()
 receipt['musical_clock_unchanged']=True;receipt['note_timestamps_unchanged']=True
 return receipt

