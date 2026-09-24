"""Prevent FBD wide targets from visually covering another floor target."""


def lanes(note):
    left=round(note['x0'][0]/note['x0'][1]*4)
    width=round(note['w0'][0]/note['w0'][1]*4)
    return set(range(left,left+width))


def floor(notes):
    return [n for n in notes if n['side']==1 and n['type'] in (1,2)]


def active_together(a,b):
    return a['start']<=b['end'] and b['start']<=a['end']


def inspect(notes):
    rows=floor(notes)
    return [(wide,other) for wide in rows if len(lanes(wide))>1
            for other in rows if other is not wide and active_together(wide,other)
            and lanes(wide)&lanes(other)]


def _set_lanes(note,left,width):
    note.update(x0=[left,4],x1=[left,4],w0=[width,4],w1=[width,4])
    if note.get('_source_type') is None and '_physical_key' in note:
        note['_physical_key']=left


def _move_generated_tap(tap,rows):
    if tap['type']!=1 or tap.get('_source_type') is not None or len(lanes(tap))!=1:
        return False
    used=set().union(*(lanes(n) for n in rows if n is not tap and active_together(n,tap)))
    options=[key for key in (1,2,3,4) if key not in used]
    if not options:return False
    old=next(iter(lanes(tap)))
    choice=min(options,key=lambda key:(abs(key-old),key))
    _set_lanes(tap,choice,1)
    return True


def repair(notes):
    rows=floor(notes)
    changes=[]
    for wide in sorted((n for n in rows if len(lanes(n))>1),key=lambda n:(n['start'],n['id'])):
        blockers=[n for n in rows if n is not wide and active_together(n,wide)
                  and lanes(n)&lanes(wide)]
        if not blockers:continue
        # A generated tap at a wide hold's exact release can change lane
        # without changing the sustained source path.
        if wide['type']==2:
            for tap in blockers:
                if _move_generated_tap(tap,rows):
                    changes.append(dict(id=tap['id'],kind='move_generated_tap'))
            blockers=[n for n in rows if n is not wide and active_together(n,wide)
                      and lanes(n)&lanes(wide)]
            if not blockers:continue
        old=lanes(wide);old_center=sum(old)/len(old)
        used=set().union(*(lanes(n) for n in rows if n is not wide and (
            active_together(n,wide) or
            (len(lanes(n))==1 and abs(n['start']-wide['start'])<=18))))
        width=len(old)
        options=[(w,left) for w in range(width,0,-1) for left in range(1,6-w)
                 if not (set(range(left,left+w))&used)]
        if not options:
            raise ValueError(f'No readable floor placement for wide target {wide["id"]}')
        w,left=min(options,key=lambda item:(width-item[0],
            abs((item[1]+(item[0]-1)/2)-old_center),abs(item[1]-min(old))))
        _set_lanes(wide,left,w)
        changes.append(dict(id=wide['id'],kind='move_or_narrow_wide',
                            before=sorted(old),after=sorted(lanes(wide))))
    assert not inspect(notes), 'Wide note still covers a floor target'
    return changes
