"""SkySafeArea mesh uses x denominators for width numerators (build pinned)."""
import math, struct
from fractions import Fraction

def contain(notes):
    """Keep widths and edge easing, containing both rational endpoints exactly."""
    changed=0
    for n in notes:
        if n['type']!=5: continue
        for suffix in ('0','1'):
            x=Fraction(*n['x'+suffix][:2]); w=Fraction(*n['w'+suffix][:2])
            bounded=max(w/2,min(1-w/2,x))
            if bounded!=x:
                n['x'+suffix]=[bounded.numerator,bounded.denominator];changed+=1
    canonicalize(notes)
    return changed

def canonicalize(notes):
    changed=0
    for n in notes:
        if n['type']!=5: continue
        for suffix in ('0','1'):
            x,w=n['x'+suffix],n['w'+suffix]
            if x[1]==w[1]: continue
            denominator=math.lcm(x[1],w[1])
            a=x[0]*(denominator//x[1]);b=w[0]*(denominator//w[1])
            common=math.gcd(math.gcd(a,b),denominator)
            a//=common;b//=common;denominator//=common
            assert max(abs(a),abs(b),denominator)<2**31
            assert Fraction(a,denominator)==Fraction(*x[:2])
            assert Fraction(b,denominator)==Fraction(*w[:2])
            n['x'+suffix]=[a,denominator];n['w'+suffix]=[b,denominator]
            changed+=1
    return changed

def check(notes):
    fields=[n for n in notes if n['type']==5]
    assert all(n['x'+s][1]==n['w'+s][1] for n in fields for s in ('0','1')), 'render and judgment widths disagree'
    return dict(fields=len(fields),render_judgment_geometry_equal=True)

def native_widths(decoder,n):
    """Run the shipped mesh builder's real coordinate arithmetic, no Unity calls."""
    from unicorn.x86_const import UC_X86_REG_RBP,UC_X86_REG_RAX,UC_X86_REG_RBX,UC_X86_REG_RSI,UC_X86_REG_RDI,UC_X86_REG_R14,UC_X86_REG_XMM6,UC_X86_REG_XMM9
    uc=decoder.uc;frame=0x100003000;vector=0x100004000
    uc.mem_write(vector,bytes(32));uc.reg_write(UC_X86_REG_RAX,vector)
    uc.reg_write(UC_X86_REG_RBP,frame)
    for reg,val in [(UC_X86_REG_RSI,n['x0'][0]),(UC_X86_REG_RBX,n['x0'][1]),(UC_X86_REG_R14,n['x1'][0]),(UC_X86_REG_RDI,n['end']-n['start'])]:uc.reg_write(reg,val)
    for off,val in [(0x1d0,n['x1'][1]),(0x1d8,n['w0'][0]),(0x1e0,n['w1'][0])]:uc.mem_write(frame+off,struct.pack('<i',val))
    uc.mem_write(frame+0x200,struct.pack('<f',1.0))
    uc.emu_start(decoder.base+0x53c25b,decoder.base+0x53c372,count=100)
    return [2*struct.unpack('<f',uc.reg_read(reg).to_bytes(16,'little')[:4])[0] for reg in [UC_X86_REG_XMM6,UC_X86_REG_XMM9]]
