"""Offline IndexTTS emotion stage; Chinese wording and speaker audio are separate inputs."""
import json,os,subprocess,sys,time,uuid,atexit,re
from pathlib import Path
ROOT=Path(__file__).resolve().parent

def deployment_issue():
 runtime=ROOT.parent/'Engines/IndexTTS2/.venv'
 model=ROOT.parent/'Engines/IndexTTS2/checkpoints_2'
 if not (runtime/'Lib/site-packages/torch/__init__.py').is_file() or not (model/'gpt.pth').is_file():
  return '情绪引擎部署未完成（PyTorch 或模型权重缺失），请先使用「试听训练音色」。'
 return None

def apply(text,reference,output,audit,log=print,original=None):
 from studio_generation import render,require_content
 runtime=ROOT.parent/'Engines/IndexTTS2/.venv/python.exe'
 def backend(part,path,seed):
  get_worker().run(dict(text=part,reference=str(reference),output=str(path),original=str(original) if original else '',seed=seed))
 report=render(text,output,backend,audit,log)
 report['emotion_status']='IndexTTS2 separate emotion conditioning; listening not accepted'
 report['emotion_source']='original_audio_emotion_encoder' if original else 'chinese_text_emotion_model'
 report['speaker_reference']=str(reference)
 report['original_emotion_reference']=str(original) if original else None
 report['source_pronunciation_transfer']='not intended; requires listening validation'
 output.with_suffix('.verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
 require_content(report,output)
 return report

class Worker:
 def __init__(self):
  issue=deployment_issue()
  if issue:raise RuntimeError(issue)
  import psutil
  for existing in psutil.process_iter(['pid','cmdline']):
   args=existing.info.get('cmdline') or []
   if '--serve' not in args or not any(str(ROOT/'studio_emotion_stage.py').lower()==arg.lower() for arg in args):continue
   folder=Path(args[args.index('--serve')+1])
   if not (folder/'ready').exists():continue
   class SharedProcess:
    def poll(self):
     return None if existing.is_running() else 1
    def terminate(self):
     pass
   self.folder=folder
   self.process=SharedProcess()
   self.stream=None
   return
  self.folder=ROOT/'Config/emotion-worker'/uuid.uuid4().hex
  self.folder.mkdir(parents=True)
  self.stream=(ROOT/'Config/index-emotion.log').open('ab')
  env=dict(os.environ,HF_HOME=str(ROOT.parent/'Engines/hf-cache'),HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',MODELSCOPE_CACHE=str(ROOT.parent/'Engines/modelscope-cache'),TORCH_HOME=str(ROOT.parent/'Engines/torch-cache'),NLTK_DATA=str(ROOT.parent/'Engines/nltk-data'),MPLCONFIGDIR=str(ROOT.parent/'Engines/cache/matplotlib'),XDG_CACHE_HOME=str(ROOT.parent/'Engines/cache'),PYTHONIOENCODING='utf8',TEMP=str(ROOT.parent/'Engines/cache/tmp'),TMP=str(ROOT.parent/'Engines/cache/tmp'))
  runtime=ROOT.parent/'Engines/IndexTTS2/.venv/python.exe'
  (ROOT.parent/'Engines/cache/tmp').mkdir(parents=True,exist_ok=True)
  env['PATH']=str(runtime.parent)+os.pathsep+str(runtime.parent/'Scripts')+os.pathsep+str(ROOT/'tools')+os.pathsep+env.get('PATH','')
  self.process=subprocess.Popen([str(runtime),'-I','-u',str(Path(__file__)), '--serve',str(self.folder)],cwd=ROOT.parent/'Engines/IndexTTS2',env=env,stdin=subprocess.DEVNULL,stdout=self.stream,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
  atexit.register(self.close)
 def close(self):
  if self.process.poll() is None:self.process.terminate()
  if self.stream is not None:self.stream.close()
 def run(self,job):
  request=self.folder/(uuid.uuid4().hex+'.job.json')
  temp=request.with_suffix('.tmp')
  temp.write_text(json.dumps(job,ensure_ascii=False),encoding='utf8');temp.replace(request)
  result=request.with_suffix('.result.json')
  deadline=time.monotonic()+600
  while not result.exists():
   if self.process.poll() is not None:
    self.close()
    lines=(ROOT/'Config/index-emotion.log').read_text(encoding='utf8',errors='replace').splitlines()[-25:]
    cause=next((line for line in reversed(lines) if 'Error:' in line or 'Exception:' in line),'请查看 App/Config/index-emotion.log')
    if 'memory' in cause.lower():cause='模型加载时内存分配失败：'+cause
    raise RuntimeError('情绪引擎退出（代码 '+str(self.process.poll())+'）：'+cause)
   if time.monotonic()>deadline:
    self.close();raise TimeoutError('独立情绪推理超时，已停止引擎')
   time.sleep(.2)
  reply=json.loads(result.read_text(encoding='utf8'))
  if not reply['ok']:raise RuntimeError(reply['error'])

_worker=None
def get_worker():
 global _worker
 if _worker is None or _worker.process.poll() is not None:_worker=Worker()
 return _worker

def release_idle_workers():
 import psutil
 global _worker
 targets=[]
 for proc in psutil.process_iter(['pid','cmdline']):
  args=proc.info.get('cmdline') or []
  if '--serve' not in args or not any(str(ROOT/'studio_emotion_stage.py').lower()==arg.lower() for arg in args):continue
  folder=Path(args[args.index('--serve')+1])
  if any(not p.with_suffix('.result.json').exists() for p in folder.glob('*.job.json')):
   raise RuntimeError('男声情绪引擎仍在生成，请等待当前任务完成后切换角色。')
  targets.append(proc)
 for proc in targets:
  try:proc.terminate()
  except psutil.NoSuchProcess:pass
 psutil.wait_procs(targets,timeout=10)
 _worker=None


def main():
 sys.path.insert(0,str(ROOT.parent/'Engines/IndexTTS2'))
 import ctypes
 if os.name=='nt':
  crt=ctypes.CDLL('ucrtbase.dll');crt.setlocale.argtypes=[ctypes.c_int,ctypes.c_char_p];crt.setlocale.restype=ctypes.c_char_p;crt.setlocale(0,b'.UTF8')
 torch_lib=Path(sys.prefix)/'Lib/site-packages/torch/lib'
 dll_handle=None
 if os.name=='nt' and torch_lib.is_dir():
  os.environ['PATH']=str(torch_lib)+os.pathsep+os.environ.get('PATH','')
  dll_handle=os.add_dll_directory(str(torch_lib))
 import torch
 import wetext.wetext as wetext_runtime
 def local_wetext(repo):
  folder=ROOT.parent/'Engines/wetext-model'
  if repo!='pengzhendong/wetext' or not (folder/'zh/tn/tagger.fst').is_file():raise FileNotFoundError('缺少本地文本规范化模型')
  return str(folder)
 wetext_runtime.snapshot_download=local_wetext
 from indextts.infer_v2 import IndexTTS2
 model=ROOT.parent/'Engines/IndexTTS2/checkpoints_2'
 tts=IndexTTS2(cfg_path=str(model/'config.yaml'),model_dir=str(model),use_fp16=True,use_cuda_kernel=False,use_deepspeed=False)
 import faulthandler
 faulthandler.cancel_dump_traceback_later()
 normalizer=tts.normalizer.normalize
 def guarded_normalize(text):
  result=normalizer(text)
  # Punctuation representation may change, but ordinary Chinese words may not.
  if not re.search(r'[0-9A-Za-z]',text):
   chars=lambda s:''.join(re.findall(r'[\u3400-\u9fff]',s))
   if chars(result)!=chars(text):raise ValueError('模型文本规范化改变了中文文字，已拦截：'+text+' → '+result)
  return result
 tts.normalizer.normalize=guarded_normalize
 def infer(job):
  tokens=tts.tokenizer.tokenize(job['text'])
  ids=tts.tokenizer.convert_tokens_to_ids(tokens)
  if tts.tokenizer.unk_token_id in ids:raise ValueError('台词包含模型无法编码的文字，不能跳字生成')
  torch.manual_seed(job['seed'])
  opts=dict(spk_audio_prompt=job['reference'],text=job['text'],output_path=job['output'],emo_alpha=.5,use_random=False)
  if job.get('natural_reading'):
   opts['emo_alpha']=1.0  # Reference style only; no Qwen emotion classification.
  elif job.get('neutral'):
   opts['emo_vector']=[0,0,0,0,0,0,0,1];opts['emo_alpha']=1.0
  elif job.get('emotion_vector') is not None:
   opts['emo_vector']=job['emotion_vector'];opts['emo_alpha']=.7
  elif job['original']:opts['emo_audio_prompt']=job['original']
  else:opts['use_emo_text']=True
  with torch.inference_mode():
   tts.infer(**opts)
 if sys.argv[1]!='--serve':
  infer(json.loads(Path(sys.argv[1]).read_text(encoding='utf8')));return
 folder=Path(sys.argv[2])
 (folder/'ready').write_text('ready')
 while True:
  jobs=list(folder.glob('*.job.json'))
  for path in jobs:
   result=path.with_suffix('.result.json')
   if result.exists():continue
   try:infer(json.loads(path.read_text(encoding='utf8')));reply=dict(ok=True)
   except Exception as exc:
    import traceback
    traceback.print_exc();reply=dict(ok=False,error=str(exc))
   import gc
   gc.collect()
   if torch.cuda.is_available():torch.cuda.empty_cache()
   tmp=result.with_suffix('.tmp');tmp.write_text(json.dumps(reply,ensure_ascii=False),encoding='utf8');tmp.replace(result)
  time.sleep(.2)

if __name__=='__main__':
 sys.stdout.reconfigure(encoding='utf8');sys.stderr.reconfigure(encoding='utf8')
 import faulthandler
 faulthandler.enable();faulthandler.dump_traceback_later(60,repeat=True)
 print('IndexTTS worker starting',flush=True)
 main()
