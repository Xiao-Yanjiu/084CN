import sys,json,threading,traceback,time,os
from pathlib import Path
ROOT=Path(__file__).resolve().parent;sys.path.insert(0,str(ROOT));sys.stdout.reconfigure(encoding='utf8');sys.stdin.reconfigure(encoding='utf8')
wire=sys.stdout;sys.stdout=sys.stderr;lock=threading.Lock();cancel=threading.Event()
def send(obj):
 with lock:wire.write(json.dumps(obj,ensure_ascii=True)+'\n');wire.flush()
import studio_project as p,studio_library as library,studio_paths
from studio_playback import Playback
from studio_runtime_player import RuntimePlayer
player=Playback();runtime=RuntimePlayer(lambda text:send({'event':'runtime','text':text}))
def catalog():
 gamepath=p.game()
 if not studio_paths.validate(str(gamepath))[0]:
  gamepath=studio_paths.discover()
  if gamepath:p.set_game(str(gamepath))
 rows=library.rows();owners={}
 for obj in p.load(p.DATA/'native-static.json',{}).values():
  for item in obj.get('list',[]) if isinstance(obj,dict) else []:
   if isinstance(item,dict) and isinstance(item.get('characterId'),int) and 1<=item['characterId']<=12 and 'id' in item:owners[str(item['id'])]=library.CHAR[item['characterId']-1]
 for row in rows:
  actor=row['speaker'];label=row['role_name']
  if row['tab']=='其他' and row['chapter'] in ('人格与大厅','战斗'):
   owner=owners.get(actor.rsplit('::',1)[-1]);first=label.split(' · ')[0];owner=owner or (first if first in library.CHAR else label);actor='人物::'+owner;label=owner
  row['_actor']=actor;row['_actor_label']=label
 valid_keys={row['key'] for row in rows}
 with p.connection() as c:
  stored=[r[0] for r in c.execute('SELECT key FROM chapter_selection')]
  invalid=[(key,) for key in stored if key not in valid_keys]
  if invalid:c.executemany('DELETE FROM chapter_selection WHERE key=?',invalid)
  selected=[key for key in stored if key in valid_keys]
 return {'rows':rows,'selected':selected,'outputs':[x.stem for x in p.OUTPUT.glob('*.wav')],'game':str(gamepath) if gamepath else '','theme':p.load(p.DATA/'ui-theme.json',{}).get('mode','light')}
def dispatch(job):
 ident=job.get('id');cmd=job['cmd']
 try:
  if cmd=='catalog':result=catalog()
  elif cmd=='scan':
   if job.get('path'):p.set_game(job['path'])
   p.scan(lambda text:send({'event':'status','text':text}));library.build(lambda text:send({'event':'status','text':text}));result=catalog()
  elif cmd=='discover':
   path=studio_paths.discover();result={'path':str(path) if path else ''}
  elif cmd=='validate':
   path,text=studio_paths.validate(job['path']);result={'valid':bool(path),'text':text}
  elif cmd=='set_game':p.set_game(job['path']);result={'path':str(p.game())}
  elif cmd=='select':
   valid_keys={row['key'] for row in library.rows()}
   with p.connection() as c:
    c.execute('DELETE FROM chapter_selection');c.executemany('INSERT OR IGNORE INTO chapter_selection VALUES(?)',[(k,) for k in job['keys'] if k in valid_keys])
   result=True
  elif cmd=='theme':(p.DATA/'ui-theme.json').write_text(json.dumps({'mode':job['mode']}),encoding='utf8');result=True
  elif cmd=='play':
   row=next(r for r in library.rows() if r['key']==job['key']);player.play(p.output(row['key']) if job.get('chinese') else p.source(row));result=True
  elif cmd=='stop':player.stop();result=True
  elif cmd=='delete':player.stop();p.delete(job['keys']);runtime.refresh();result=True
  elif cmd=='connect':result={'count':runtime.connect(),'connected':runtime.connected}
  elif cmd=='disconnect':runtime.disconnect();result={'connected':False}
  elif cmd=='cancel':cancel.set();result=True
  elif cmd=='generate':
   cancel.clear();wanted=set(job['keys']);rows=[r for r in library.rows() if r['key'] in wanted];pending,skipped=p.pending_generation(rows);start=time.monotonic();failures=[];done=0;success=0;last_refresh=start
   send({'event':'progress','total':len(pending),'current':0,'done':0,'success':0,'skipped':skipped,'elapsed':0})
   for i,row in enumerate(pending):
    if cancel.is_set():break
    send({'event':'progress','total':len(pending),'current':i+1,'done':done,'success':success,'elapsed':time.monotonic()-start,'key':row['key']})
    try:p.generate(row,lambda text:send({'event':'status','text':text}));success+=1;send({'event':'generated','key':row['key']})
    except Exception as exc:failures.append({'key':row['key'],'error':str(exc)});send({'event':'rowerror','key':row['key'],'text':str(exc)})
    done+=1;send({'event':'progress','total':len(pending),'current':i+1,'done':done,'success':success,'elapsed':time.monotonic()-start})
    if runtime.connected and time.monotonic()-last_refresh>=30:runtime.refresh();last_refresh=time.monotonic()
   if runtime.connected:runtime.refresh()
   (p.DATA/'chapter-generation-errors.json').write_text(json.dumps(failures,ensure_ascii=False),encoding='utf8');result={'done':done,'failed':failures,'success':success,'skipped':skipped}
  else:raise ValueError('Unknown command '+cmd)
  send({'id':ident,'ok':True,'result':result})
 except Exception as exc:traceback.print_exc();send({'id':ident,'ok':False,'error':str(exc)})
for line in sys.stdin:
 try:
  job=json.loads(line)
  if job['cmd'] in ('cancel','stop'):dispatch(job)
  else:threading.Thread(target=dispatch,args=(job,),daemon=True).start()
 except Exception:traceback.print_exc()
runtime.disconnect();player.close()
