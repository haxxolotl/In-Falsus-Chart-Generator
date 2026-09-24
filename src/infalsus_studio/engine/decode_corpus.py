import pathlib,sys,json,time,hashlib,struct,collections
BASE=pathlib.Path(__file__).parent
import numpy as np
import soundfile as sf
from .chart_emulator import Decoder,GAME,KEY

MASK=(1<<64)-1
def decode_audio(raw):
    size=len(raw)
    pad=(-size)%8
    words=np.frombuffer(raw+bytes(pad),dtype='<u8').copy()
    block=np.arange(len(words),dtype=np.uint64)
    # The native FMOD callback gets the file length in bits 24..55 of its token.
    with np.errstate(over='ignore'):
        a=block*np.uint64(0xd6e8feb86659fd93)+np.uint64((size*0x9e3779b97f4a7c15)&MASK)
        b=(((a>>np.uint64(47))|(a<<np.uint64(17)))^a)*np.uint64(0xa24baed4963ee407)
        stream=((b>>np.uint64(23))|(b<<np.uint64(41)))^b
    words^=stream
    words^=np.uint64(0xa5c39e7d4b2816f0)
    return words.tobytes()[:size]

def ogg_crc(data):
    crc=0
    for b in data:
        crc^=b<<24
        for _ in range(8):crc=((crc<<1)^0x04c11db7 if crc&0x80000000 else crc<<1)&0xffffffff
    return crc


