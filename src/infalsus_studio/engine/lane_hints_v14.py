"""EVO lane activity uses timing type 3, not decoded note key-span annotations.

Stock alamode1 disables key 4 at t=0 and key 1 at 16714ms. Track reads
these records into six keyPressedIndicators and dims inactive keys to 0.3.
Raw bytes16/17 store physical key and enabled; _S._Gab expands that pair.
"""
import struct

def apply(chart,level):
 if level!=1:return dict(events=0,scope='EVO_only')
 events=[];coverage=0
 for key in range(1,5):
  intervals=[]
  for n in sorted(chart['notes'],key=lambda n:n['start']):
   if n['type'] not in (1,2) or n['side']!=1:continue
   lo=round(n['x0'][0]/n['x0'][1]*4);hi=lo+round(n['w0'][0]/n['w0'][1]*4)-1
   if not lo<=key<=hi:continue
   a=max(0,n['start']-750);b=n['end']+350
   if intervals and a<=intervals[-1][1]+1250:intervals[-1][1]=max(b,intervals[-1][1])
   else:intervals.append([a,b])
  initial=bool(intervals and intervals[0][0]==0)
  events.append((0,key,initial))
  for a,b in intervals:
   if a:events.append((a,key,True))
   events.append((b,key,False));coverage+=1
 raw=bytes.fromhex(chart['timing_data_hex']);rows=[bytearray(raw[i:i+32]) for i in range(0,len(raw),32) if struct.unpack_from('<i',raw,i+12)[0]!=3]
 deadline=max(max(n['end'] for n in chart['notes']),max(struct.unpack_from('<i',r,8)[0] for r in rows))
 for time,key,on in events:
  if time<=deadline:rows.append(bytearray(struct.pack('<qiiBB14x',0,time,3,key,on)))
 rows.sort(key=lambda r:(struct.unpack_from('<i',r,8)[0],struct.unpack_from('<i',r,12)[0]))
 for i,r in enumerate(rows):struct.pack_into('<q',r,0,i)
 assert len(rows)<1024
 chart['timing_data_hex']=b''.join(rows).hex()
 # Every keyboard event, including its full sustain, is in an enabled interval.
 return dict(events=sum(struct.unpack_from('<i',r,12)[0]==3 for r in rows),active_intervals=coverage,lead_in_ms=750,release_margin_ms=350,native_type=3)
