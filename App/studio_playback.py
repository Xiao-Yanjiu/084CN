import math
import json
from pathlib import Path
import threading
import wave
import numpy as np
from scipy.signal import resample_poly

def output_devices():
    import sounddevice as sd
    hosts=sd.query_hostapis()
    return [(i, d['name'], hosts[d['hostapi']]['name']) for i,d in enumerate(sd.query_devices())
            if d['max_output_channels'] and hosts[d['hostapi']]['name'] in ('Windows WASAPI','MME')
            and 'Mapper' not in d['name']]


class Playback:
    rate = 48000
    tail = 3840

    def __init__(self):
        self.lock=threading.Lock()
        self.stream=None
        self.audio=None
        self.position=0
        self.device=None
        self.config=Path(__file__).resolve().parent/'Config'/'audio-output.json'
        try:
            self.selection=tuple(json.loads(self.config.read_text(encoding='utf-8')))
        except (ValueError,OSError,TypeError):
            self.selection=()

    def set_output(self,selection):
        self.close()
        self.selection=tuple(selection)
        self.config.write_text(json.dumps(self.selection,ensure_ascii=False),encoding='utf-8')

    def output(self):
        import sounddevice as sd
        for index,name,host in output_devices():
            if (name,host)==self.selection: return index
        # Follow the current system output; prefer its WASAPI endpoint.
        default=sd.default.device[1]
        if default>=0:
            name=sd.query_devices(default,'output')['name']
            for index,candidate,host in output_devices():
                if candidate==name and host=='Windows WASAPI':return index
            return default
        raise RuntimeError('没有可用的系统输出设备')

    def stop(self):
        with self.lock:
            if self.audio is None: return
            part=self.audio[self.position:self.position+self.tail].copy()
            if len(part):
                part*=np.linspace(1,0,len(part),dtype=np.float32)[:,None]
            self.audio=part
            self.position=0

    def _callback(self,out,frames,info,status):
        out.fill(0)
        with self.lock:
            if self.audio is None: return
            n=min(frames,len(self.audio)-self.position)
            out[:n]=self.audio[self.position:self.position+n]
            self.position+=n
            if self.position>=len(self.audio):
                self.audio=None

    def play(self,path):
        import sounddevice as sd
        device=self.output()
        if self.stream is not None and (device!=self.device or not self.stream.active):
            self.close()
        info=sd.query_devices(device,'output')
        self.rate=round(info['default_samplerate'])
        self.tail=round(self.rate*.08)
        self.device=device
        with wave.open(str(path),'rb') as wav:
            if wav.getsampwidth()!=2: raise ValueError('试听要求 PCM 16 位 WAV')
            channels,rate=wav.getnchannels(),wav.getframerate()
            pcm=np.frombuffer(wav.readframes(wav.getnframes()),dtype='<i2').reshape(-1,channels)
        if not len(pcm): raise ValueError('音频为空')
        pcm=pcm.astype(np.float32).mean(axis=1)/32768
        if rate!=self.rate:
            divisor=math.gcd(rate,self.rate)
            pcm=resample_poly(pcm,self.rate//divisor,rate//divisor)
        pcm*=min(1.,.90/max(float(np.max(np.abs(pcm))),1e-8))
        n=min(self.tail,len(pcm))
        pcm[-n:]*=np.linspace(1,0,n,dtype=np.float32)
        n=min(480,len(pcm))
        pcm[:n]*=np.linspace(0,1,n,dtype=np.float32)
        with self.lock:
            prefix=None
            if self.audio is not None:
                prefix=self.audio[self.position:self.position+self.tail].copy()
                if len(prefix): prefix*=np.linspace(1,0,len(prefix),dtype=np.float32)[:,None]
            self.audio=np.concatenate((prefix,pcm[:,None])) if prefix is not None else pcm[:,None]
            self.position=0
        if self.stream is None:
            self.stream=sd.OutputStream(device=device,samplerate=self.rate,channels=min(2,info['max_output_channels']),dtype='float32',
                                        blocksize=0,latency='high',callback=self._callback)
            try: self.stream.start()
            except Exception:
                self.stream.close()
                self.stream=None
                raise

    def close(self):
        self.stop()
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream=None


