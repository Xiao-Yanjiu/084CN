"""External FMOD player. All files stay under this project; attachment is memory-only."""
import sys,os,json,time,threading,subprocess,wave,collections
from pathlib import Path
ROOT=Path(__file__).resolve().parent
DATA=ROOT/'Config/player';DATA.mkdir(exist_ok=True)
class RuntimePlayer:
 def __init__(self,notify=lambda text:None):
  self.notify=notify;self.session=None;self.script=None;self.lock=threading.RLock();self.count=0
 @property
 def connected(self):return self.session is not None
 def message(self,message,data=None):
  payload=message.get('payload',message)
  with (DATA/'runtime-events.jsonl').open('a',encoding='utf8') as f:f.write(json.dumps(dict(time=time.time(),message=payload),ensure_ascii=False)+'\n')
  kind=payload.get('type')
  if kind=='replaced':self.notify('中文播放：'+payload['event'])
  elif kind=='story_dialogue' and not payload.get('matched'):self.notify('本句未匹配中文：'+payload.get('scene','')+' · '+payload.get('content','')[:32])
  elif kind in ('fatal','fallback','warning'):self.notify('保留原声：'+payload.get('message',''))
 def detached(self,*args):
  self.session=None;self.script=None;self.notify('游戏连接已断开，使用原声')
 def connect(self):
  with self.lock:
   if self.connected:return self.count
   sys.path.insert(0,str(ROOT/'tools/runtime-player'))
   temp=DATA/'tmp';temp.mkdir(exist_ok=True);os.environ['TMP']=str(temp);os.environ['TEMP']=str(temp)
   import frida
   device=frida.get_local_device();matches=[p for p in device.enumerate_processes() if p.name.lower()=='limbuscompany.exe']
   if len(matches)!=1:raise RuntimeError('请先打开一个《边狱巴士》游戏窗口，再连接游戏。')
   # Event names are read from the installed game's current string bank.
   from studio_event_cache import valid
   import studio_project
   if not valid(DATA,studio_project.game()):
    self.notify('正在更新游戏语音事件表')
    try:result=subprocess.run([sys.executable,str(ROOT/'tools/player/export_events.py')],cwd=str(ROOT),capture_output=True,timeout=45,creationflags=0x08000000)
    except subprocess.TimeoutExpired:raise RuntimeError('游戏事件表更新超时，已停止导出，可重新连接。')
    if result.returncode:raise RuntimeError('读取游戏事件表失败：'+result.stderr.decode(errors='replace')[-500:])
    if not valid(DATA,studio_project.game()):raise RuntimeError('游戏事件表校验失败，请重新连接。')
   mapping,report=build_mapping();(DATA/'runtime-map-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
   session=device.attach(matches[0].pid)
   try:
    source=(ROOT/'tools/player/runtime_audio.js').read_text(encoding='utf-8-sig')
    from studio_story_player import story_bootstrap,build_story_mapping
    story_rows=[];story_count=0;story_available=False
    try:
     source+='\n'+story_bootstrap();story_available=True;story_rows,story_report=build_story_mapping()
     (DATA/'story-map-report.json').write_text(json.dumps(story_report,ensure_ascii=False,indent=2),encoding='utf8')
    except Exception as e:self.notify('但丁/旁白暂未接入：'+str(e))
    script=session.create_script(source);script.on('message',self.message);script.load();script.exports_sync.configure(mapping)
    for _ in range(80):
     state=script.exports_sync.status()
     if state['ready']:break
     time.sleep(.1)
    if story_available:
     try:
      script.exports_sync.configurestory(story_rows);story_count=len(story_rows)
     except Exception as e:self.notify('但丁/旁白接入失败：'+str(e))
    if not state['ready']:raise RuntimeError('未找到兼容的 FMOD 接口，已取消接入；原声保持不变。')
   except Exception:
    try:script.exports_sync.shutdown()
    except Exception:pass
    session.detach();raise
   self.session=session;self.script=script;self.count=len(mapping)+story_count;session.on('detached',self.detached)
   self.notify(f'已连接游戏 · {self.count} 条可替换中文');return self.count
 def refresh(self):
  with self.lock:
   if not self.script:return
   mapping,report=build_mapping();self.count=self.script.exports_sync.configure(mapping)
   from studio_story_player import build_story_mapping
   rows,_=build_story_mapping()
   try:
    self.script.exports_sync.configurestory(rows);self.count+=len(rows)
   except Exception as e:self.notify('剧情映射刷新失败：'+str(e))
 def disconnect(self):
  with self.lock:
   script,session=self.script,self.session;self.script=None;self.session=None
   try:
    if script:script.exports_sync.shutdown()
   finally:
    if session:session.detach()
   self.notify('已断开游戏，恢复原声')
def build_mapping():
 import studio_library as library
 events=set(json.loads((DATA/'events.json').read_text(encoding='utf8')))
 rows=library.rows();by=collections.defaultdict(list)
 sample_events=collections.defaultdict(list)
 for event in events:sample_events[event.rsplit('/',1)[-1]].append(event)
 story_events=collections.defaultdict(list)
 for event in events:
  if event.startswith('event:/Voice_Story/'):
   story_events[event.rsplit('/',1)[-1]].append(event)
 for row in rows:
  if not row.get('asset'):continue
  sample=row.get('sample_key',row['key'])
  event='event:/Voice_Story/'+row['scene']+'/'+sample if row['tab']=='剧情' or row.get('source_type')=='剧情' else 'event:/Voice/'+sample
  if row['tab']=='剧情' or row.get('source_type')=='剧情':
   # Match the exact native voice key across every scene event that reuses it.
   for actual in story_events.get(sample,[]):by[actual].append(row)
  elif row.get('source_type')=='播报':
   actuals=[e for e in sample_events.get(sample,[]) if e.startswith('event:/BattleAnnouncer/')]
   if len(actuals)==1:by[actuals[0]].append(row)
  elif event in events:by[event].append(row)
 mapping={};ambiguous=[];invalid=[];missing=0
 for event,choices in by.items():
  keys={r['key'] for r in choices}
  if len(keys)!=1:ambiguous.append(dict(event=event,keys=sorted(keys)));continue
  path=ROOT.parent/'ChineseAudio'/(choices[0]['key']+'.wav')
  if not path.is_file():missing+=1;continue
  try:
   with wave.open(str(path),'rb') as f:
    if f.getnframes()==0 or f.getsampwidth()!=2:raise ValueError('empty or not PCM16')
  except Exception as e:invalid.append(dict(key=choices[0]['key'],error=str(e)));continue
  mapping[event]=str(path.resolve())
 return mapping,dict(mapped=len(mapping),exact_events=len(by),missing_audio=missing,ambiguous=ambiguous,invalid=invalid,unvoiced_not_supported=sum(not r.get('asset') for r in rows))
