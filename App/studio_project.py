"""Chapter Studio service. No character weights; exact native source identities."""
import json,re,sys,os,hashlib,subprocess,threading,wave,time
from contextlib import contextmanager
from pathlib import Path
from studio_core import ROOT,DATA,GAME,db,soundbank
OUTPUT=ROOT.parent/'ChineseAudio'
CACHE=DATA/'source-cache'
LOCK=threading.Lock()
@contextmanager
def project_lock():
 import msvcrt
 with LOCK:
  with (DATA/'chapter-operation.lock').open('a+b') as f:
   if not f.tell():f.write(b'0');f.flush()
   f.seek(0)
   try:msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
   except OSError:raise RuntimeError('另一个 Studio 正在扫描、生成或删除，请等待其完成')
   try:yield
   finally:f.seek(0);msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)

MALE={'__dante__':'dante','__narrator__':'narrator'}
def load(path,default=None):
 try:return json.loads(Path(path).read_text(encoding='utf-8-sig'))
 except (OSError,ValueError):return default

def dialogue_id(row):
 return row.get('id',row.get('d'))

def game():return Path(load(DATA/'chapter-settings.json',{}).get('game',str(GAME)))
def set_game(path):
 p=Path(path)
 if (p/'LimbusCompany_Data').is_dir():p=p/'LimbusCompany_Data'
 if not (p/'Lang/LLC_zh-CN/StoryData').is_dir():raise ValueError('该目录没有零协剧情文本')
 (DATA/'chapter-settings.json').write_text(json.dumps({'game':str(p)},ensure_ascii=False),encoding='utf8')

def connection():
 c=db();c.executescript('''CREATE TABLE IF NOT EXISTS chapter_scenes(scene TEXT PRIMARY KEY, chapter TEXT, rank INTEGER);
 CREATE TABLE IF NOT EXISTS chapter_outputs(key TEXT PRIMARY KEY,path TEXT,fingerprint TEXT,status TEXT);
 CREATE TABLE IF NOT EXISTS chapter_selection(key TEXT PRIMARY KEY);
 CREATE TABLE IF NOT EXISTS chapter_available(key TEXT PRIMARY KEY);''');return c

def catalog():
 return Path.home()/'AppData/LocalLow/ProjectMoon/LimbusCompany/com.unity.addressables/catalog_S1.bin'

def refresh_native(log):
 sys.path.insert(0,str(ROOT/'tools/asset-reader'))
 import UnityPy
 UnityPy.config.FALLBACK_UNITY_VERSION='6000.3.12f1'
 cat=catalog();raw=cat.read_bytes()
 signature=hashlib.sha256(raw).hexdigest()
 target=DATA/'live-native.json';old=load(target,{})
 cache=Path.home()/'AppData/LocalLow/Unity/ProjectMoon_LimbusCompany'
 by={p.parent.name:p for p in cache.glob('*/*/__data')}
 relevant=sorted(set(b.decode() for b in re.findall(rb'[A-Za-z0-9_\-]+_[0-9a-f]{32}[.]bundle',raw) if b.startswith(b'static_') or b'storyeffect' in b.lower()))
 cache_signature=[(n,str(by.get(n[-39:-7],''))) for n in relevant]
 if old.get('schema')==3 and old.get('signature')==signature and old.get('cache_signature')==[list(x) for x in cache_signature]:return old
 effects={};chapters={};missing=[];static={}
 for n in relevant:
  p=by.get(n[-39:-7])
  if not p:missing.append(n);continue
  log('读取本地场景索引：'+n[:50])
  for o in UnityPy.load(str(p)).objects:
   if o.type.name!='TextAsset':continue
   d=o.read()
   try:j=json.loads(d.m_Script)
   except (ValueError,UnicodeError):continue
   if not isinstance(j,dict):continue
   if n.startswith('static_'):
    static[d.m_Name]=j
    if d.m_Name.startswith('storytheater-'):chapters[d.m_Name]=j
   elif isinstance(j.get('dataList'),list):
    if d.m_Name in effects and effects[d.m_Name]!=j:raise ValueError('原生场景索引存在冲突：'+d.m_Name)
    effects[d.m_Name]=j
 (DATA/'native-static.json').write_text(json.dumps(static,ensure_ascii=False),encoding='utf8')
 result=dict(schema=3,signature=signature,cache_signature=cache_signature,effects=effects,chapters=chapters,missing=missing)
 target.write_text(json.dumps(result,ensure_ascii=False),encoding='utf8');return result

def scan(log=print):
 with project_lock():
  native=refresh_native(log);g=game();cnroot=g/'Lang/LLC_zh-CN/StoryData';krroot=g/'Assets/Resources_moved/Localize/kr/StoryData'
  if not cnroot.is_dir() or not krroot.is_dir():raise ValueError('缺少本地中文或韩语剧情文本')
  chaptermap={};excluded=set()
  def story_ids(obj):
   if isinstance(obj,dict):
    if isinstance(obj.get('storyId'),str):yield obj['storyId']
    for v in obj.values():yield from story_ids(v)
   elif isinstance(obj,list):
    for v in obj:yield from story_ids(v)
  titles={x['id']:x.get('title','') for x in load(g/'Lang/LLC_zh-CN/StoryTheaterOther.json',{}).get('dataList',[]) if 'id' in x}
  names={}
  nums=['序章','第一章','第二章','第三章','第四章','第五章','第六章','第七章','第八章','第九章','第十章']
  for name,j in native['chapters'].items():
   if 'personality' in name or 'mirrorworld' in name:
    excluded.update(story_ids(j));continue
   if not name.startswith(('storytheater-main','storytheater-other')):continue
   for group in j.get('list',[]):
    sid=group.get('subChapterId',-1)
    label=nums[sid-100] if name.startswith('storytheater-main') and 100<=sid<=110 else '支线 · '+(titles.get('EventTitleText_'+str(sid)) or str(sid))
    for item in group.get('storyList',[]):
     if item.get('storyId'):chaptermap[item['storyId']]=(label,sid)
  banks={};issues=[];bankfolder=g/'StreamingAssets/Assets/Sound/FMODBuilds/Desktop'
  cached=load(DATA/'bank-index.json',{})
  updated={}
  for p in bankfolder.glob('*.assets.bank'):
   st=p.stat();sig=[st.st_size,st.st_mtime_ns];entry=cached.get(str(p),{})
   if entry.get('stat')!=sig:
    try:keys,_=soundbank(p);entry={'stat':sig,'keys':keys}
    except Exception as e:issues.append({'bank':str(p),'error':str(e)});continue
   updated[str(p)]=entry
   for k in entry['keys']:banks.setdefault(k,[]).append(str(p))
  (DATA/'bank-index.json').write_text(json.dumps(updated),encoding='utf8')
  rows={};sources={};voice_links={};coverage=[]
  with db() as c:unvoiced={r[0]:r[1] for r in c.execute("SELECT key,speaker FROM lines WHERE speaker IN ('__dante__','__narrator__')")}
  for kp in krroot.glob('KR_*.json'):
   scene=kp.stem[3:]
   if False:continue  # Personality stories are shown under the Other tab.
   ko=load(kp,{}).get('dataList',[])
   zhrows=load(cnroot/(scene+'.json'),{}).get('dataList',[])
   erows=native['effects'].get(scene,{}).get('dataList',[])
   # Positional identities only when both language arrays prove structural alignment.
   aligned=len(ko)==len(zhrows) and all(dialogue_id(a)==dialogue_id(b) and (a.get('model') or '')==(b.get('model') or '') for a,b in zip(ko,zhrows))
   if aligned:
    ko=[dict(x,id=-(1000000+i)) if dialogue_id(x) is None else x for i,x in enumerate(ko)]
    zhrows=[dict(x,id=-(1000000+i)) if dialogue_id(x) is None else x for i,x in enumerate(zhrows)]
    if len(erows)==len(ko):erows=[dict(x,id=-(1000000+i)) if dialogue_id(x) is None and dialogue_id(ko[i])==-(1000000+i) else x for i,x in enumerate(erows)]
   cn={dialogue_id(x):x for x in zhrows if dialogue_id(x) is not None}
   effects={dialogue_id(x):x for x in erows if dialogue_id(x) is not None}
   for item in ko:
    idx=dialogue_id(item);zh=cn.get(idx,{});voice=effects.get(idx,{}).get('voice','');model=item.get('model','')
    if not zh.get('content'):continue
    if item.get('teller')=='단테' or '단테' in model:unvoiced['unvoiced-'+scene+'-'+str(idx)]='__dante__'
    elif not model and not item.get('teller'):unvoiced['unvoiced-'+scene+'-'+str(idx)]='__narrator__'
    if item.get('teller')=='단테' or '단테' in model or (not model and not item.get('teller')):
     voice=''
    if voice:
     key=voice;assets=sorted(set(banks.get(key,[])))
     if len(assets)!=1:
      # Prefer the actual scene's bank only when its embedded sample name matches exactly.
      exact=[a for a in assets if Path(a).name==scene+'.assets.bank']
      if len(exact)!=1:issues.append({'scene':scene,'id':idx,'voice':key,'error':'音频资源缺失或重复'});continue
      assets=exact
     speaker=model or ('teller::'+item['teller'] if item.get('teller') else 'unknown::'+scene+'::'+str(idx));asset=assets[0]
     if not speaker:issues.append({'scene':scene,'id':idx,'error':'有语音但无原生角色'});continue
    else:
     # Keep only already identified native Dante/narrator rows, never infer from text style.
     key='unvoiced-'+scene+'-'+str(idx)
     prior=(unvoiced[key],) if key in unvoiced else None
     if not prior or prior[0] not in MALE:continue
     speaker=prior[0];asset=''
    if zh.get('teller') and speaker.startswith('teller::'):names[speaker]=zh['teller']
    value=(key,scene,speaker,zh['content'],asset,item.get('content',''))
    if key in rows and rows[key] is None:continue
    if key in rows and rows[key][2:4]!=value[2:4]:
     key=key+'--'+hashlib.sha256((scene+'|'+str(idx)+'|'+str(speaker)+'|'+zh['content']).encode()).hexdigest()[:12]
     value=(key,scene,speaker,zh['content'],asset,item.get('content',''))
    if key in rows and rows[key] is None:continue
    rows[key]=value;sources[key]=(key,scene,idx)
    if voice:voice_links[key]=voice
    coverage.append(dict(key=key,scene=scene,id=idx,voice=voice))
  (DATA/'story-voice-links.json').write_text(json.dumps(voice_links,ensure_ascii=False),encoding='utf8')
  (DATA/'story-occurrences.json').write_text(json.dumps(coverage,ensure_ascii=False),encoding='utf8')
  (DATA/'native-speaker-names.json').write_text(json.dumps(names,ensure_ascii=False),encoding='utf8')
  valid=[v for v in rows.values() if v]
  if not valid:raise ValueError('扫描结果为空，保留旧索引')
  with connection() as c:
   c.execute('DELETE FROM chapter_available');c.execute('DELETE FROM chapter_scenes')
   c.executemany('INSERT INTO chapter_scenes VALUES (?,?,?)',[(s,*v) for s,v in chaptermap.items()])
   c.executemany('''INSERT INTO lines(key,scene,speaker,text,asset,transcript) VALUES(?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET scene=excluded.scene,speaker=excluded.speaker,text=excluded.text,asset=excluded.asset,transcript=excluded.transcript''',valid)
   c.executemany('INSERT OR REPLACE INTO native_sources VALUES(?,?,?)',[sources[v[0]] for v in valid])
   c.executemany('INSERT INTO chapter_available VALUES(?)',[(v[0],) for v in valid])
  report={'lines':len(valid),'chapters':len(set(v[0] for v in chaptermap.values())),'unclassified':sum(v[1] not in chaptermap for v in valid),'issues':issues,'missing_bundles':native['missing'],'time':time.time()}
  (DATA/'chapter-scan-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
  return report

def rows():
 with connection() as c:return [dict(r) for r in c.execute("SELECT l.*,COALESCE(ch.chapter,'未归类') chapter,COALESCE(ch.rank,99999) rank FROM lines l JOIN chapter_available a USING(key) LEFT JOIN native_sources n ON n.key=l.key LEFT JOIN chapter_scenes ch ON ch.scene=l.scene ORDER BY rank,l.scene,n.line_id,l.key")]

def output(key):
 if not re.fullmatch(r'[A-Za-z0-9_-]+',key):raise ValueError('无效语音键')
 return OUTPUT/(key+'.wav')

def source(row):
 if row['speaker'] in MALE:raise ValueError('但丁及旁白使用所选男声，无对应人声原句')
 sample=row.get('sample_key',row['key'])
 p=Path(row['asset']);st=p.stat();sig=hashlib.sha256((str(p)+str(st.st_size)+str(st.st_mtime_ns)+row['key']+str(row.get('sample_index',''))).encode()).hexdigest()
 CACHE.mkdir(exist_ok=True);dest=CACHE/(sig+'.wav')
 if dest.exists():return dest
 keys,raw=soundbank(p)
 index=row.get('sample_index')
 if index is None:
  if keys.count(sample)!=1:raise ValueError('游戏资源内找不到唯一同名语音，禁止替换为其他角色')
  index=keys.index(sample)
 if not isinstance(index,int) or not 0<=index<len(keys) or keys[index]!=sample:raise ValueError('游戏音频资源已更新，请重新扫描后再试听或生成')
 scratch=CACHE/(sig+'.fsb');temp=CACHE/(sig+'.tmp.wav');scratch.write_bytes(raw)
 try:
  decoder=ROOT/'tools/vgmstream/vgmstream-cli.exe'
  subprocess.run([str(decoder),'-i','-s',str(index+1),'-o',str(temp),str(scratch)],check=True,capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW)
  with wave.open(str(temp),'rb') as w:
   if not w.getnframes():raise ValueError('解码原声为空')
  temp.replace(dest)
 finally:scratch.unlink(missing_ok=True);temp.unlink(missing_ok=True)
 # Bound on-demand cache to 512 MiB; the current file is pinned.
 files=sorted(CACHE.glob('*.wav'),key=lambda p:p.stat().st_mtime);total=sum(p.stat().st_size for p in files)
 for f in files:
  if total<=512*1024**2:break
  if f!=dest:total-=f.stat().st_size;f.unlink()
 return dest

def pending_generation(rows):
 # One directory listing, rather than per-row disk probes and worker tasks.
 existing={entry.name[:-4] for entry in os.scandir(OUTPUT) if entry.name.lower().endswith('.wav') and entry.is_file()} if OUTPUT.is_dir() else set()
 seen=set();pending=[]
 for row in rows:
  key=row['key']
  if key in seen:continue
  seen.add(key)
  if key not in existing:pending.append(row)
 return pending,len(seen)-len(pending)

def generate(row,log=print):
 from studio_generation import render,normalized,spoken_text,wave_check
 from studio_emotion_stage import get_worker
 with project_lock():
  out=output(row['key']);OUTPUT.mkdir(exist_ok=True)
  if out.exists():return '已存在，跳过'
  if not row['text'].strip():raise ValueError('没有精确对应的零协中文台词，不能编造文字生成')
  if not normalized(spoken_text(row['text'])):return '仅标点，保留原声'
  male=row['speaker'] in MALE
  ref=ROOT.parent/'Engines/voices'/(MALE[row['speaker']]+'.wav') if male else source(row)
  tmp=out.with_name(out.stem+'.pending.wav')
  def backend(text,path,seed):get_worker().run(dict(text=text,reference=str(ref),original='' if male else str(ref),output=str(path),seed=seed,natural_reading=male))
  try:
   report=render(row['text'],tmp,backend,None,log,redaction_pause=row.get('tab')=='剧情' or row.get('source_type')=='剧情')
   report.update(source_key=row['key'],speaker=row['speaker'],source_scene=row['scene'],speaker_reference=str(ref),mode='selected_male' if male else 'current_original',listening_accepted=False)
   if male:
    from studio_audio_finish import normalize_speech
   if row['speaker']=='__narrator__':
    from studio_audio_finish import adjust_narrator_tempo
    report['tempo']=adjust_narrator_tempo(tmp);wave_check(tmp)
   if male:
    report['loudness']=normalize_speech(tmp);wave_check(tmp)
   reportpath=out.with_suffix('.verification.json');reportpath.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
   tmp.replace(out)
   with connection() as c:c.execute('INSERT OR REPLACE INTO chapter_outputs VALUES (?,?,?,?)',(row['key'],str(out),hashlib.sha256(row['text'].encode()).hexdigest(),report['status']))
   return '已生成 · 待试听'
  except Exception as exc:
   failed=DATA/'generation-failures';failed.mkdir(exist_ok=True)
   audit=tmp.with_suffix('.verification.json');saved=failed/(out.stem+'.verification.json')
   if audit.exists():audit.replace(saved)
   else:saved.write_text(json.dumps({'text':row['text'],'error':str(exc)},ensure_ascii=False,indent=2),encoding='utf8')
   if tmp.exists():tmp.replace(failed/out.name)
   message=str(exc).replace(str(audit),str(saved))
   raise RuntimeError(message+'\n失败记录：'+str(saved)) from exc
  finally:tmp.unlink(missing_ok=True);tmp.with_suffix('.verification.json').unlink(missing_ok=True)

def delete(keys):
 with project_lock():
  for key in keys:
   p=output(key)
   if p.resolve().parent!=OUTPUT.resolve():raise ValueError('输出路径越界')
   p.unlink(missing_ok=True);p.with_suffix('.verification.json').unlink(missing_ok=True)
  with connection() as c:c.executemany('DELETE FROM chapter_outputs WHERE key=?',[(k,) for k in keys])

