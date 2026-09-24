"""Adapt source note roles; retain exact source attack timing after audio alignment."""
import pathlib,sys,json,copy,math,re,struct,hashlib,argparse,itertools
import numpy as np
from .source_formats import parse_source as parse_aff
from .refine_chart_music import frac,mouse_fix,value
from .arrangement_policy import finalize,rating,place,repair_short_cursor_segments
from .arrangement_metrics import extended
from .prepare_audio import save,sha,guid
from .chart_encoder import Encoder
from .chart_emulator import Decoder
from .generate_arranged_charts import verify
from .source_field_style import arc_width
import pathlib
B=pathlib.Path(__file__).parent
R=B/'revision7';O=B.parent/'outputs/source-chart-pack-v7'

def compatible_flicks(notes):
 mouse_fix(notes);repairs=[]
 for ms,group in itertools.groupby(sorted((n for n in notes if n['type']==4),key=lambda n:n['start']),key=lambda n:n['start']):
  group=list(group)
  if len({n['flags']&1024 for n in group})>1:
   carrier=next((n for n in notes if n['type']==5 and n['start']<=ms<=n['end']),None);direction=1024 if carrier and value(carrier,'x1')<value(carrier,'x0') else (group[0]['flags']&1024 or 4096)
   for n in group:n['flags']=(n['flags']&~0x1e00)|direction
   repairs.append(dict(time=ms,targets=len(group),reason='shared flick direction'))
  carriers=[n for n in notes if n['type']==5 and n['start']<=ms<=n['end']]
  left=max(0,min([value(n,'x0')-value(n,'w0')/2 for n in group]+[min(value(n,'x0'),value(n,'x1')) for n in carriers]))
  right=min(1,max([value(n,'x0')+value(n,'w0')/2 for n in group]+[max(value(n,'x0'),value(n,'x1')) for n in carriers]))
  if max(value(n,'x0')-value(n,'w0')/2 for n in group)<=min(value(n,'x0')+value(n,'w0')/2 for n in group) and all(value(n,'x0')-value(n,'w0')/2<=min(value(h,'x0'),value(h,'x1'))+.0005 and value(n,'x0')+value(n,'w0')/2>=max(value(h,'x0'),value(h,'x1'))-.0005 for n in group for h in carriers):continue
  for n in group:n.update(x0=frac((left+right)/2),x1=frac((left+right)/2),w0=frac(right-left),w1=frac(right-left))
  repairs.append(dict(time=ms,targets=len(group),reason='shared playable tracking zone'))
 return repairs

def deduplicate_flicks(notes):
 """Collapse physically identical flick targets created by two source hands."""
 seen={};removed=[]
 for n in list(notes):
  if n['type']!=4:continue
  key=(n['side'],n['start'],n['end'],n['flags'],tuple(n['x0'][:2]),tuple(n['x1'][:2]),tuple(n['w0'][:2]),tuple(n['w1'][:2]))
  if key not in seen:seen[key]=n;continue
  keep=seen[key];sources=set(keep.get('_source_events',[]))|set(n.get('_source_events',[]))
  if '_source_event' in keep:sources.add(keep['_source_event'])
  if '_source_event' in n:sources.add(n['_source_event'])
  if sources:keep['_source_events']=sorted(sources)
  removed.append(dict(time=n['start'],source_event=n.get('_source_event'),kept_source_event=keep.get('_source_event')));notes.remove(n)
 return removed

def easing(t,kind):
 if kind=='s':return t
 if kind=='si':return math.sin(math.pi*t/2)
 if kind=='so':return 1-math.cos(math.pi*t/2)
 if kind=='b':return 3*t*t-2*t*t*t
 raise ValueError(('unknown easing',kind))

def position(n,tm):
 a=n.get('arc_start',n['start']);b=n.get('arc_end',n['end']);u=np.clip((tm-a)/max(1,b-a),0,1);kind=n['easing'];kx=kind[:2] if len(kind)==4 else kind;ky=kind[2:] if len(kind)==4 else 'b' if kind=='b' else 's'
 return n['x0']+(n['x1']-n['x0'])*easing(u,kx),n['y0']+(n['y1']-n['y0'])*easing(u,ky)

def adaptive_arc_ticks(n,start,end,tolerance=.01,level=2):
 """Approximate one eased source arc without fixed high-frequency tessellation."""
 def point(tm):
  x,y=position(n,tm);x=float(np.clip(.1+.8*x,.04,.96));return x,arc_width(x,y,level)
 out=[start]
 def split(a,b,pa,pb,depth=0):
  if b-a<=2 or depth>=20:out.append(b);return
  m=(a+b)/2;pm=point(m);linear=((pa[0]+pb[0])/2,(pa[1]+pb[1])/2)
  if max(abs(pm[0]-linear[0]),abs(pm[1]-linear[1]))<=tolerance:out.append(b);return
  split(a,m,pa,pm,depth+1);split(m,b,pm,pb,depth+1)
 split(start,end,point(start),point(end))
 return out

def source_grid(chart,al,meta,duration):
 from .musical_source_clock import build
 clock=build(R,meta);events=clock['timings'];ignored=clock['ignored_scroll_timings']
 off=al['offset_seconds'];scale=al['time_scale'];out=[]
 for i,e in enumerate(events):
  tm=e['time']/1000*scale+off;bpm=e['bpm']/scale;period=60/bpm*e['meter']
  if tm<0:tm+=math.ceil(-tm/period)*period
  if tm>=duration:continue
  out.append(dict(start=tm,bpm=bpm,beats=e['meter']))
 assert out
 mapped_note_starts=[n['start']/1000*scale+off for n in chart['notes'] if n['start']/1000*scale+off>=0]
 if mapped_note_starts:out[0]['start']=min(out[0]['start'],min(mapped_note_starts))
 # The chart header supplies the base tempo and meter before the first authored
 # timing event.  Do not synthesize a duplicate event at zero: two identical
 # type-1 records before the first note drive LogicalNotePlayer._kb past its
 # timing span during chart initialization.
 bars=[]
 for i,e in enumerate(out):
  e['end']=out[i+1]['start'] if i+1<len(out) else duration;bars.extend(np.arange(e['start'],e['end']-.001,60/e['bpm']*e['beats']).tolist())
 raw=b''.join(struct.pack('<qiidff',i,round(e['start']*1000),1,e['bpm'],e['beats'],0) for i,e in enumerate(out))
 return dict(nominal_bpm=out[0]['bpm'],segments=out,bars=bars,ignored_scroll_timings=ignored,source=clock['method'],clock_provenance=clock),raw.hex()

def adapt(chart,al,level,thin=False,derived_level=None):
 source=chart['notes'];notes=[];aux=[];flicks=[];off=al['offset_seconds']*1000;scale=al['time_scale'];counts=dict(floor=0,hold=0,sky=0,arc=0,flick=0,field=0);changes=[];visual_guide=chart.get('source_format')=='SDVX.in Ongeki visual guide'
 def tm(x):return round(off+x*scale)
 def note(kind,start,end,x,w=.22,**extra):
  return dict(type=kind,side=4 if kind in (4,5) else 1,start=tm(start),end=tm(end),x0=frac(x),x1=frac(x),w0=frac(w),w1=frac(w),flags=0,**extra)
 # For three-difficulty source songs, ULT relaxes a subset of dense inner attacks;
 # FBD keeps the complete highest source chart. Whole holds and arcs are retained.
 taps=[(i,n) for i,n in enumerate(source) if n['type']=='floor'];remove=set()
 if thin:
  ordered=sorted(taps,key=lambda p:p[1]['start'])
  musical=[e for e in chart['timings'] if e['bpm']>0 and e['meter']>=1]
  for j,(i,n) in enumerate(ordered):
   e=next((e for e in reversed(musical) if e['time']<=n['start']),musical[0]);beat=60000/e['bpm'];phase=((n['start']-e['time'])/beat)%1
   # Relax weak sixteenth subdivisions consistently across returning figures;
   # retain eighth-note anchors, triplets, holds and complete cursor gestures.
   if 0<j<len(ordered)-1 and n['start']-ordered[j-1][1]['start']<beat*.3 and ordered[j+1][1]['start']-n['start']<beat*.3 and min(abs(phase-.25),abs(phase-.75))<.035:remove.add(i)
 if derived_level is not None:
  gap=[180,110,65,0][derived_level];chord=[1,2,3,6][derived_level];last=-1e9
  for t,group in itertools.groupby(sorted(taps,key=lambda p:p[1]['start']),key=lambda p:p[1]['start']):
   group=list(group)
   if t-last<gap:remove.update(i for i,_ in group);continue
   if len(group)>chord:
    keep={i for i,_ in (group if chord>=len(group) else [group[round(k*(len(group)-1)/max(1,chord-1))] for k in range(chord)])};remove.update(i for i,_ in group if i not in keep)
   last=t
 arcs=[]
 for i,n in enumerate(source):
  counts[n['type']]+=1
  if n['type'] in ('floor','hold'):
   if i in remove:continue
   out=note(1 if n['type']=='floor' else 2,n['start'],n['end'],0,_source_event=i,_source_type=n['type']);place(out,n['lane'],n.get('width',1));out['end']=max(out['start'],out['end']-1) if out['type']==2 else out['start'];notes.append(out)
   # Exact event charts lock authored holds.  The Ongeki raster guide can
   # merge overlapping colored rails into an ambiguous lane, so its holds may
   # be rerouted or shortened by the physical-key solver while their attacks
   # and visible sustained gestures remain source-derived.
   if out['type']==2 and not visual_guide:out['sustain_locked']='source-floor-'+str(i)
  elif n['type']=='field':
   out=note(5,n['start'],n['end'],.05+.9*n['x0'],.9*n['w0'],_source_event=i,_source_type='field');out.update(x1=frac(.05+.9*n['x1']),w1=frac(.9*n['w1']),_group='guide-field');notes.append(out)
  elif n['type']=='flick':
   out=note(4,n['start'],n['end'],float(np.clip(.1+.8*n['x'],.04,.96)),.26,_source_event=i,_source_type='flick');out['flags']=1024 if n['direction']<0 else 4096;notes.append(out)
  elif n['type']=='sky':
   x,y=position(n,n['start']);out=note(1,n['start'],n['start'],0,_source_event=i,_source_type='sky');place(out,0 if x<.5 else 5);notes.append(out)
  else:
   dist=abs(n['x1']-n['x0'])+abs(n['y1']-n['y0']);duration=n['end']-n['start']
   if 0<duration<=130 and dist>=.23:
    x,y=position(n,n['end']);out=note(4,n['end'],n['end'],float(np.clip(.1+.8*x,.04,.96)),.26,_source_event=i,_source_type='fast_arc');out['flags']=1024 if n['x1']<n['x0'] else 4096;notes.append(out);flicks.append(i)
   elif duration>0:arcs.append(dict(n,source_id=i))
 # One mouse follows a continuous source arc; concurrent second-hand arcs become
 # sustained side inputs. This avoids an impossible pair of independent cursors.
 boundaries=sorted({x for a in arcs for x in [a['start'],a['end']]});last_color=None;previous=None;field_count=0
 for a,b in zip(boundaries,boundaries[1:]):
  active=[n for n in arcs if n['start']<=a and n['end']>=b]
  if not active:previous=None;continue
  primary=next((n for n in active if n['color']==last_color),active[0]);last_color=primary['color']
  for n in active:
   if n is not primary:aux.append(dict(start=a,end=b,key=0 if n['color']==0 else 5,source_id=n['source_id']))
  ticks=adaptive_arc_ticks(primary,a,b,level=level)
  for u,v in zip(ticks,ticks[1:]):
   x0,y0=position(primary,u);x1,y1=position(primary,v);x0=float(np.clip(.1+.8*x0,.04,.96));x1=float(np.clip(.1+.8*x1,.04,.96));w0=arc_width(x0,y0,level);w1=arc_width(x1,y1,level)
   if previous and previous['end']==tm(u) and abs(value(previous,'x1')-x0)>.08:
    x0=value(previous,'x1');w0=arc_width(x0,y0,level);changes.append(dict(type='mouse_handoff',time=tm(u)))
   out=note(5,u,v,x0,w0,_source_event=primary['source_id'],_source_type='arc');out.update(x1=frac(x1),w1=frac(w1),_group='arc-'+str(primary['source_id']));notes.append(out);previous=out;field_count+=1
 for key in (0,5):
  merged=[]
  for x in sorted((x for x in aux if x['key']==key),key=lambda x:x['start']):
   if merged and x['start']<=merged[-1]['end']+1:merged[-1]['end']=max(merged[-1]['end'],x['end']);merged[-1]['sources'].append(x['source_id'])
   else:merged.append(dict(x,sources=[x['source_id']]))
  for x in merged:
   if x['end']-x['start']<100:continue
   out=note(2,x['start'],x['end'],0,_source_type='concurrent_arc',_source_events=sorted(set(x['sources'])));out['end']-=1;place(out,key)
   for h in notes:
    if h['type']==2 and h.get('sustain_locked') and value(h,'x0')*4==key:
     if h['start']==out['end']:out['end']-=1;changes.append(dict(type='one_ms_side_release',time=out['end']))
     if h['end']==out['start']:out['start']+=1;changes.append(dict(type='one_ms_side_release',time=out['start']))
   if any(n['type']==2 and n.get('sustain_locked') and value(n,'x0')*4==key and n['start']<out['end'] and n['end']>out['start'] for n in notes):changes.append(dict(type='concurrent_arc_collapse',time=out['start']));continue
   out['sustain_locked']='source-arc-'+str(out['start']);notes.append(out)
 # If a sky accent falls on a held side, retain its timestamp as a mouse flick
 # when tracking is active; otherwise the physical-key allocator finds a free key.
 holds=[n for n in notes if n['type']==2]
 for n in notes:
  if n.get('_source_type')!='sky':continue
  key=round(value(n,'x0')*4)
  if any(h['start']<=n['start']<=h['end'] and round(value(h,'x0')*4)==key for h in holds) and any(h['type']==5 and h['start']<=n['start']<=h['end'] for h in notes):
   n.update(type=4,side=4,x0=frac(.5),x1=frac(.5),w0=frac(.25),w1=frac(.25));changes.append(dict(type='sky_to_flick',time=n['start']))
   carrier=next(h for h in notes if h['type']==5 and h['start']<=n['start']<=h['end']);n['flags']=1024 if value(carrier,'x1')<value(carrier,'x0') else 4096
 notes=[n for n in notes if n['start']>=1800 and n['end']>=n['start']];mouse_fix(notes);notes,ergonomics=finalize(notes)
 if visual_guide:
  from .ongeki_field_width_v27 import correct as fit_visual_guide_widths
  changes.append(dict(type='visual_guide_width_fit',**fit_visual_guide_widths(notes)))
 return notes,dict(source_types=counts,relaxed_floor_attacks=len(remove),fast_arcs_as_flicks=len(flicks),field_segments=field_count,adaptations=changes,ergonomics=ergonomics)

