"""Read-only emulation of this build's pure chart decoding routines."""
import pathlib,sys,struct,json,hashlib
BASE=pathlib.Path(__file__).parent

import pefile
from unicorn import Uc,UC_ARCH_X86,UC_MODE_64,UcError
from unicorn.x86_const import *

GAME=None

EXPECTED='4b593ee87237dab978bdbb7dfd65b73967096e06ce4f33e4a42e68fbe0735807'
KEY=(0xa5c39e7d4b2816f0).to_bytes(8,'little')

class Decoder:
    def __init__(self,game_root=GAME):
        binary=pathlib.Path(game_root)/'GameAssembly.dll'
        digest=hashlib.sha256(binary.read_bytes()).hexdigest()
        shifts={EXPECTED:0, 'ab1d8fa7739078510fab5f8580095c90ea0f5e9236ac9b918e934cb9ae1b7d9c':-0x64b0}
        if digest not in shifts: raise ValueError('Unsupported In Falsus build; update Studio before generating charts.')
        self.rva_shift=shifts[digest]
        pe=pefile.PE(str(binary),fast_load=True)
        self.uc=Uc(UC_ARCH_X86,UC_MODE_64)
        self.base=pe.OPTIONAL_HEADER.ImageBase
        self.uc.mem_map(self.base,(pe.OPTIONAL_HEADER.SizeOfImage+4095)&~4095)
        self.uc.mem_write(self.base,pe.get_memory_mapped_image())
        self.uc.mem_map(0x70000000,0x200000)
        self.uc.mem_map(0x100000000,0x1000000)
        self.uc.mem_map(0x60000000,4096)
        self.uc.mem_write(0x60000000,b'\xcc')
    def call(self,rva,*args):
        rsp=0x701ff008
        self.uc.mem_write(rsp,struct.pack('<Q',0x60000000)+bytes(0x100))
        self.uc.reg_write(UC_X86_REG_RSP,rsp)
        for reg,v in zip([UC_X86_REG_RCX,UC_X86_REG_RDX,UC_X86_REG_R8,UC_X86_REG_R9],args):self.uc.reg_write(reg,v)
        for i,v in enumerate(args[4:]):self.uc.mem_write(rsp+0x28+8*i,struct.pack('<Q',v))
        try:self.uc.emu_start(self.base+rva,0x60000000,timeout=30_000_000,count=100_000_000)
        except UcError as e:
            raise RuntimeError(f'{e}; RIP={self.uc.reg_read(UC_X86_REG_RIP):x}') from e
        if self.uc.reg_read(UC_X86_REG_RIP)!=0x60000000:raise RuntimeError('Emulation budget exhausted')
        return self.uc.reg_read(UC_X86_REG_RAX)
    def decode(self,encoded,name):
        data=bytes(x^KEY[i%8] for i,x in enumerate(encoded))
        magic,ver,header,ns,ts,n,nt,bpm,beats=struct.unpack_from('<4sHHHHIIff',data)
        assert (magic,ver,header,ns,ts)==(b'ICP1',1,28,80,32)
        assert len(data)==28+n*80+nt*32
        string_ptr=0x100000000;state_ptr=string_ptr+0x1000;input_ptr=string_ptr+0x10000;output_ptr=string_ptr+0x800000
        logical_name=name if name.endswith('.spc') else name+'.spc'
        string=logical_name.encode('utf-16-le')
        self.uc.mem_write(string_ptr,bytes(16)+struct.pack('<I',len(string)//2)+string+b'\0\0')
        self.uc.mem_write(state_ptr,bytes(64))
        self.call(0x53b7d0+self.rva_shift,state_ptr,string_ptr,n,ver,0)
        state=bytes(self.uc.mem_read(state_ptr,40))
        # Native decoder includes the 28-byte container header in its offsets.
        self.uc.mem_write(input_ptr,data)
        self.uc.mem_write(output_ptr,bytes(n*128))
        self.call(0x538e90+self.rva_shift,input_ptr,n,state_ptr,output_ptr,0)
        raw=bytes(self.uc.mem_read(output_ptr,n*128))
        notes=[]
        for i in range(n):
            o=i*128
            ident,group,side,typ,start,end=struct.unpack_from('<qqIIii',raw,o)
            coords=[struct.unpack_from('<iif',raw,o+32+j*12) for j in range(4)]
            notes.append({'id':ident,'group':group,'side':side,'type':typ,'start':start,'end':end,
                          'x0':coords[0],'x1':coords[1],'w0':coords[2],'w1':coords[3],
                          'key_start':struct.unpack_from('<i',raw,o+120)[0],
                          'key_end':struct.unpack_from('<i',raw,o+124)[0],
                          'extra':raw[o+80:o+128].hex()})
        return {'name':name,'bpm':bpm,'beats':beats,'notes':notes,'state_hex':state.hex(),
                'timing_data_hex':data[28+n*80:].hex()}

