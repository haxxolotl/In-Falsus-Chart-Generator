"""Execute the shipped per-note timing-integrity check in one native loop."""
import struct

def check(decoder,count):
    # Win64 wrapper: preserve nonvolatiles, reserve shadow space, call the
    # unmodified _S._Iab for every decoded 128-byte note, retain signed offsets.
    entry=0x60000100;target=decoder.base+0x52a600+decoder.rva_shift;out=0x100600000
    code=bytearray.fromhex('41544155415641574883ec284989cc4989d54d89c64531ff4d85ed7400')
    jz=len(code)-1;loop=len(code)
    code+=bytes.fromhex('4c89f848c1e007498d0c0431d248b8')+struct.pack('<Q',target)+bytes.fromhex('ffd0438904be49ffc74d39ef72')
    code+=struct.pack('b',loop-(len(code)+1));end=len(code);code[jz]=end-(jz+1)
    code+=bytes.fromhex('4883c428415f415e415d415cc3')
    decoder.uc.mem_write(entry,bytes(code));decoder.call(entry-decoder.base,0x100800000,count,out)
    offsets=struct.unpack('<'+'i'*count,bytes(decoder.uc.mem_read(out,count*4))) if count else []
    return dict(notes=count,invalid=sum(v!=0 for v in offsets),minimum_ms=min(offsets,default=0),maximum_ms=max(offsets,default=0))
