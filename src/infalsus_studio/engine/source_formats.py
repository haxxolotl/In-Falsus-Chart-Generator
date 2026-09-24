"""Canonical authored events from AFF and Deemo DS; no audio-generated notes."""
import json,math,pathlib
from .source_charts import parse_aff

def parse_source(path):
 if path.suffix=='.aff':return parse_aff(path)
 if path.suffix=='.sus':
  from .sekai_source import parse_sus
  return parse_sus(path)
 if path.suffix=='.svg':
  from .sekai_svg_source import parse
  return parse(path)
 data=json.loads(path.read_text(encoding='utf8'))
 if data.get('source_format')=='SDVX.in Ongeki visual guide':
  return dict(data,notes=data['notes']+data['fields'])
 timing=json.loads(path.with_suffix('.timing.json').read_text())
 playable={n['$id']:n for n in data['notes'] if n.get('pos',0)<=2}
 links=[[playable[n['$ref']] for n in link['notes'] if n['$ref'] in playable] for link in data['links']]
 slide_ids={n['$id'] for link in links for n in link};notes=[]
 def x(n):return max(0,min(1,(n.get('pos',0)+2)/4))
 for n in playable.values():
  if n['$id'] in slide_ids:continue
  t=n['_time']*1000;notes.append(dict(type='floor',start=t,end=t,lane=min(4,1+int(x(n)*4)),source_id=n['$id'],source_x=x(n)))
 for link in links:
  link.sort(key=lambda n:n['_time'])
  if len(link)==1:
   n=link[0];notes.append(dict(type='floor',start=n['_time']*1000,end=n['_time']*1000,lane=min(4,1+int(x(n)*4)),source_id=n['$id'],source_x=x(n)))
  for a,b in zip(link,link[1:]):
   notes.append(dict(type='arc',start=a['_time']*1000,end=b['_time']*1000,x0=x(a),x1=x(b),y0=.5,y1=.5,easing='s',color=int(x(link[0])>=.5),trace=False,source_ids=[a['$id'],b['$id']]))
 return dict(notes=notes,timings=timing['timings'],audio_offset=0,ignored={'sound_only_events':len(data['notes'])-len(playable)},source_format='Deemo DS',timing_provenance=timing)
