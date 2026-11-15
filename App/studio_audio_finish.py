from pathlib import Path
import numpy as np,soundfile as sf

def normalize_speech(path,target_db=-16.0):
 path=Path(path);a,sr=sf.read(path,dtype='float32',always_2d=True)
 hop=max(1,int(sr*.02));mono=np.mean(a,axis=1)
 levels=np.array([np.sqrt(np.mean(mono[i:i+hop]**2)) for i in range(0,len(mono),hop)])
 active=levels[levels>max(1e-5,float(levels.max())*.08)]
 if not len(active):raise ValueError('旁白没有可听语音')
 rms=float(np.sqrt(np.mean(active**2)));peak=float(np.max(np.abs(a)))
 gain=min(10**(target_db/20)/max(rms,1e-8),.94/max(peak,1e-8))
 temp=path.with_suffix('.level.wav');sf.write(temp,a*gain,sr,subtype='PCM_16');temp.replace(path)
 return dict(target_db=target_db,method='one_constant_gain_no_compression',gain_db=float(20*np.log10(gain)),input_active_db=float(20*np.log10(rms)),output_active_db=float(20*np.log10(rms*gain)),peak_limited=bool(rms*gain<10**(target_db/20)*.99))


def adjust_narrator_tempo(path,factor=1.05):
 import subprocess
 path=Path(path)
 ffmpeg=Path(__file__).resolve().parent/'tools/ffmpeg.exe'
 temp=path.with_suffix('.tempo.wav')
 try:
  subprocess.run([str(ffmpeg),'-hide_banner','-loglevel','error','-y','-i',str(path),'-af',f'atempo={factor}','-c:a','pcm_s16le',str(temp)],check=True,creationflags=subprocess.CREATE_NO_WINDOW)
  temp.replace(path)
 finally:temp.unlink(missing_ok=True)
 return dict(method='pitch_preserving_atempo',factor=factor)
