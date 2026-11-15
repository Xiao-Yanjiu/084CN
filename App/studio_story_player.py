"""Strict native-dialogue identities for Dante/narrator. No fuzzy text matching."""
import collections, json, wave, hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parent

def build_story_mapping():
 import studio_library as library
 import studio_project as p
 rows=library.rows()
 variants=collections.defaultdict(set)
 for r in rows:
  if r.get('asset') and r.get('source_type')=='剧情':variants[r.get('sample_key',r['key'])].add(r['text'])
 conflicts={voice for voice,texts in variants.items() if len(texts)>1}
 with p.connection() as c:
  identities={r['key']:(r['scene'],r['line_id']) for r in c.execute('select * from native_sources')}
 result=[]; rejected=[]; files={}
 for row in rows:
  if row.get('source_type')!='剧情':continue
  if row.get('asset') and row.get('sample_key',row['key']) not in conflicts and row.get('speaker') not in ('__dante__','__narrator__'):continue
  path=ROOT.parent/'ChineseAudio'/(row['key']+'.wav')
  if not path.is_file():continue
  identity=identities.get(row['key'])
  if not identity or identity[0]!=row['scene']:rejected.append(row['key']);continue
  scene,idx=identity
  if scene not in files:
   src=p.game()/'Assets/Resources_moved/Localize/kr/StoryData'/('KR_'+scene+'.json')
   files[scene]=p.load(src,{}).get('dataList',[])
  native=([files[scene][-idx-1000000]] if idx<=-1000000 and 0<=-idx-1000000<len(files[scene]) and p.dialogue_id(files[scene][-idx-1000000]) is None else []) if idx<=-1000000 else [x for x in files[scene] if p.dialogue_id(x)==idx]
  if len(native)!=1:rejected.append(row['key']);continue
  n=native[0];model=n.get('model') or '';teller=n.get('teller') or ''
  speaker='__dante__' if '단테' in (model,teller) else '__narrator__' if not model and not teller else ''
  voiced=bool(row.get('asset'))
  if not voiced and speaker!=row['speaker']:rejected.append(row['key']);continue
  try:
   with wave.open(str(path),'rb') as f:
    if f.getnframes()<1 or f.getsampwidth()!=2:raise ValueError()
  except Exception:rejected.append(row['key']);continue
  result.append(dict(scene=scene,id=idx,key=row['key'],speaker=speaker,model=model,teller=teller,text=row['text'],korean=n.get('content') or '',voice=row.get('sample_key',row['key']) if voiced else '',file=str(path.resolve())))
 # Expand exact native occurrences; UI classification never limits playback lookup.
 bykey={r['key']:r for r in result};seen={(r['scene'],r['id'],r['key']) for r in result}
 for occurrence in p.load(p.DATA/'story-occurrences.json',[]):
  base=bykey.get(occurrence['key']);identity=(occurrence['scene'],occurrence['id'],occurrence['key'])
  if not base or identity in seen:continue
  scene,idx=occurrence['scene'],occurrence['id']
  records=p.load(p.game()/'Assets/Resources_moved/Localize/kr/StoryData'/('KR_'+scene+'.json'),{}).get('dataList',[])
  matches=[n for n in records if p.dialogue_id(n)==idx]
  if len(matches)!=1:continue
  n=matches[0]
  if (n.get('model') or '')!=base['model'] or (n.get('teller') or '')!=base['teller']:continue
  result.append(dict(base,scene=scene,id=idx,korean=n.get('content') or ''));seen.add(identity)
 return result,dict(mapped=len(result),rejected=rejected)

def story_bootstrap():
 import studio_project as p
 mapping=json.loads((ROOT/'Config/player/il2cpp-api-map.json').read_text(encoding='utf-8-sig'))
 player=p.game().parent/'UnityPlayer.dll'
 if hashlib.sha256(player.read_bytes()).hexdigest()!=mapping['unity_sha256']:
  raise RuntimeError('游戏接口版本已变化：暂不接入但丁/旁白，需重新核对公共接口')
 return 'const storyApiNames='+json.dumps(mapping['symbols'])+';\n'+(ROOT/'tools/player/runtime_story.js').read_text(encoding='utf-8-sig')
