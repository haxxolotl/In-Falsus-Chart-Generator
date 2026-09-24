import os
os.environ.setdefault('OMP_NUM_THREADS','4')
os.environ.setdefault('OPENBLAS_NUM_THREADS','4')
import pathlib,sys,json,math,time
BASE=pathlib.Path(__file__).parent
import numpy as np
import soundfile as sf
from scipy import signal,ndimage

SR=22050
HOP=256
FEATURE_NAMES=['flux','bass_flux','lowmid_flux','highmid_flux','treble_flux',
               'energy','bass_energy','lowmid_energy','highmid_energy','treble_energy',
               'centroid','flatness']+['chroma_'+str(i) for i in range(12)]

def normalize(v):
    return np.clip(v/(np.quantile(v,.95)+1e-9),0,4)

def extract(path):
    wave,sr=sf.read(str(path),dtype='float32',always_2d=True)
    frames=len(wave)
    assert np.isfinite(wave).all()
    mono=wave.mean(axis=1)
    del wave
    if sr!=SR:
        g=math.gcd(sr,SR);mono=signal.resample_poly(mono,SR//g,sr//g)
    freq,t,z=signal.stft(mono,fs=SR,nperseg=2048,noverlap=2048-HOP,boundary='zeros',padded=False)
    mag=np.abs(z).astype('float32');del z,mono
    compressed=np.log1p(1000*mag)
    delta=np.maximum(0,np.diff(compressed,axis=1,prepend=compressed[:,:1]))
    masks=[(freq>=a)&(freq<b) for a,b in [(30,180),(180,800),(800,3000),(3000,11026)]]
    flux=delta.mean(axis=0)
    features=[normalize(flux)]+[normalize(delta[m].mean(axis=0)) for m in masks]
    power=mag**2
    features += [normalize(np.sqrt(power.sum(axis=0)))]
    features += [normalize(np.sqrt(power[m].sum(axis=0))) for m in masks]
    features += [(freq[:,None]*mag).sum(axis=0)/(mag.sum(axis=0)+1e-9)/11025]
    features += [np.exp(np.mean(np.log(mag+1e-9),axis=0))/(mag.mean(axis=0)+1e-9)]
    midi=np.round(69+12*np.log2(np.maximum(freq,1)/440)).astype(int)
    chroma=np.array([power[(midi%12==i)&(freq>=65)&(freq<3000)].sum(axis=0) for i in range(12)])
    chroma/=chroma.sum(axis=0,keepdims=True)+1e-9
    features += list(chroma)
    f=np.array(features,dtype='float32').T
    peaks,_=signal.find_peaks(f[:,0],distance=round(.055*SR/HOP),prominence=.08,height=.1)
    return {'t':t.astype('float32'),'features':f,'peaks':t[peaks].astype('float32'),
            'sample_rate':sr,'frames':frames,'duration':frames/sr}


