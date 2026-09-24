"""Build-pinned chart writer using the installed decoder to advance its cipher state.

Only emulator memory is changed. The game executable and installation stay untouched.
"""
import math,struct,pathlib,json,sys
BASE=pathlib.Path(__file__).parent

from .chart_emulator import Decoder,KEY
from unicorn import UC_HOOK_CODE
from unicorn.x86_const import UC_X86_REG_RAX,UC_X86_REG_RDI,UC_X86_REG_R12,UC_X86_REG_R13,UC_X86_REG_RBP,UC_X86_REG_RSP

# Native keystream-return sites: logical field index, byte offset, byte width.
SITES={0x538f3e:(0,0,8),0x538fea:(1,8,8),0x539099:(2,16,4),
       0x5390fd:(3,20,4),0x539161:(4,24,4),0x5391c5:(5,28,4),
       0x5393c9:(6,32,4),0x539229:(7,36,4),0x539282:(8,40,4),0x5392e1:(9,44,4)}

class Encoder:
    def __init__(self, game_root=None):
        self.decoder=Decoder() if game_root is None else Decoder(game_root)
        self.active=None
        self.calls=0
        self.integrity_calls=0
        self.coordinate_rows=None
        for site,field in SITES.items():
            self.decoder.uc.hook_add(UC_HOOK_CODE,self._key_ready,user_data=field,
                begin=self.decoder.base+site+self.decoder.rva_shift,end=self.decoder.base+site+self.decoder.rva_shift)
        # The four raw coordinate GCDs carry a per-note checksum byte each.
        # Reducing them all to one keeps visible coordinates correct but makes
        # _S._Iab apply a hidden +/-750 ms judgment penalty at runtime.
        site=self.decoder.base+0x539864+self.decoder.rva_shift
        assert bytes(self.decoder.uc.mem_read(site,3))==b'\x44\x8b\xc8'
        self.decoder.uc.hook_add(UC_HOOK_CODE,self._integrity_ready,begin=site,end=site)

    def _integrity_ready(self,uc,address,size,user_data):
        if self.active is None:return
        rbp=uc.reg_read(UC_X86_REG_RBP);rsp=uc.reg_read(UC_X86_REG_RSP)
        row=struct.unpack('<I',uc.mem_read(rbp+0x120,4))[0]
        checksum=uc.reg_read(UC_X86_REG_RAX)&0xffffffff
        tags=[(checksum>>(8*j))&255 for j in range(4)]
        raw=[]
        for j,tag in enumerate(tags):
            num,den=self.coordinate_rows[row][2*j:2*j+2]
            raw.extend((num*(tag+1),den*(tag+1)))
        assert all(-(2**31)<=v<2**31 for v in raw),'checksum coordinate overflow'
        ptr=uc.reg_read(UC_X86_REG_R12)+28+uc.reg_read(UC_X86_REG_R13)+48
        uc.mem_write(ptr,struct.pack('<8i',*raw))
        for ptr,value in [(rsp+0x50,tags[0]),(rbp+0x50,tags[1]),(rbp+0x58,tags[2]),(rsp+0x48,tags[3]+1)]:
            uc.mem_write(ptr,struct.pack('<Q',value))
        self.integrity_calls+=1

    def _key_ready(self,uc,address,size,field):
        if self.active is None:return
        index,offset,width=field
        row=uc.reg_read(UC_X86_REG_RDI)
        key=uc.reg_read(UC_X86_REG_RAX)
        value=self.active[row][index]
        address=uc.reg_read(UC_X86_REG_R12)+28+uc.reg_read(UC_X86_REG_R13)+offset
        uc.mem_write(address,((value+key)&((1<<(width*8))-1)).to_bytes(width,'little'))
        self.calls+=1

    def encode(self,chart,name):
        notes=chart['notes']
        # SkySafeArea passes only x denominators to its mesh builder. Widths
        # must share those denominators even though judgment uses all four
        # independent rational values. Preserve exact geometry on both paths.
        from .native_field_geometry import canonicalize
        canonicalize(notes)
        timing=bytes.fromhex(chart.get('timing_data_hex',''))
        assert len(timing)%32==0
        data=bytearray(struct.pack('<4sHHHHIIff',b'ICP1',1,28,80,32,len(notes),len(timing)//32,
                                   chart.get('bpm',120),chart.get('beats',4)))
        rows=[];coordinate_rows=[]
        for i,note in enumerate(notes):
            assert note['type'] in (1,2,4,5) and note['side'] in (1,2,3,4)
            assert 0<=note['start']<=note['end']<=3600000
            coords=[];scales=[]
            for key in ('x0','x1','w0','w1'):
                num,den=map(int,note[key][:2]);assert den>0
                scale=math.gcd(num,den);scales.append(scale)
                coords.extend([num//scale,den//scale])
            rows.append([i,note.get('group',i),note['start'],note['end'],
                         note['side']|(note['type']<<8),note.get('flags',0),*scales])
            data.extend(bytes(48)+struct.pack('<8i',*coords))
            coordinate_rows.append(coords)
        data.extend(timing)
        encoded=bytes(v^KEY[i%8] for i,v in enumerate(data))
        self.active=rows;self.calls=0;self.coordinate_rows=coordinate_rows;self.integrity_calls=0
        try:
            decoded=self.decoder.decode(encoded,name)
            assert self.calls==len(notes)*10
            assert self.integrity_calls==len(notes)
            plaintext=bytes(self.decoder.uc.mem_read(0x100010000,len(data)))
        finally:self.active=None
        from .native_note_integrity import check as check_integrity
        assert check_integrity(self.decoder,len(notes))['invalid']==0,'native note checksum failed'
        for i,(wanted,actual) in enumerate(zip(notes,decoded['notes'])):
            for key in ('group','side','type','start','end'):
                expected=wanted.get(key,i)
                assert actual[key]==expected,(i,key,actual[key],expected)
            for key in ('x0','x1','w0','w1'):
                assert tuple(actual[key][:2])==tuple(wanted[key][:2]),(i,key,actual[key],wanted[key])
            assert struct.unpack_from('<I',bytes.fromhex(actual['extra']),36)[0]==wanted.get('flags',0)
        return bytes(v^KEY[i%8] for i,v in enumerate(plaintext))

