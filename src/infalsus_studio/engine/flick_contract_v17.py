"""Require the two actual horizontal flick flags; zero is unjudgeable."""
import json,sys,hashlib
from pathlib import Path
LEFT,RIGHT,MASK=1024,4096,0x1e00
def repair(notes):
 changed=[]
 for n in notes:
  if n['type']!=4:continue
  f=n.get('flags',0);direction=f&MASK
  if direction==0:
   # The old converter used 1024 for left and zero for right.
   n['flags']=f|RIGHT;changed.append(dict(id=n['id'],time_ms=n['start'],old_flags=f,new_flags=n['flags']))
  elif direction not in (LEFT,RIGHT):raise ValueError(('unsupported/ambiguous flick direction',n))
 return changed
def validate(notes):
 assert all(n.get('flags',0)&MASK in (LEFT,RIGHT) for n in notes if n['type']==4),'Unjudgeable horizontal flick'
