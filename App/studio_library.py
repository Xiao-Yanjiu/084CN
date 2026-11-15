"""Native audio library taxonomy and exact text joins."""
import json,re,hashlib,collections
from pathlib import Path
import studio_project as p
from studio_role_names import role_label
CHAR=['李箱','浮士德','堂吉诃德','良秀','默尔索','鸿璐','希斯克利夫','以实玛利','罗佳','辛克莱','奥提斯','格里高尔']
def strings(o):
 if isinstance(o,str):yield o
 elif isinstance(o,dict):
  for v in o.values():yield from strings(v)
 elif isinstance(o,list):
  for v in o:yield from strings(v)
def records(o):
 if isinstance(o,dict):
  yield o
  for v in o.values():yield from records(v)
 elif isinstance(o,list):
  for v in o:yield from records(v)
def build(log=print):
 static=p.load(p.DATA/'native-static.json',{});native=p.load(p.DATA/'live-native.json',{});g=p.game();cn=g/'Lang/LLC_zh-CN';kr=g/'Assets/Resources_moved/Localize/kr'
 title={}
 for f in [*cn.glob('StoryTheater*.json'),*cn.glob('SubChapter*.json')]:
  for r in p.load(f,{}).get('dataList',[]):title[str(r.get('id'))]=r.get('title') or r.get('name') or ''
 for r in p.load(cn/'StageChapterText.json',{}).get('dataList',[]):
  suffix=str(r.get('id','')).rsplit('_',1)[-1]
  if r.get('chaptertitle'):title['CHAPTER_'+suffix]=r['chaptertitle']
 def chapter(sid):
  if isinstance(sid,int) and 100<=sid<=110:return (['序章','第一章','第二章','第三章','第四章','第五章','第六章','第七章','第八章','第九章','第十章'][sid-100],sid)
  if isinstance(sid,int) and sid>=100 and sid<200:return ('第'+str(sid-100)+'章',sid)
  return ('支线 · '+(title.get('EventTitleText_'+str(sid)) or title.get('CHAPTER_'+str(sid)) or str(sid)),int(sid) if isinstance(sid,int) else 90000)
 scene_map={};provenance={};personas={};entity={};stage_to_chapter={}
 for name,obj in static.items():
  for r in obj.get('list',[]) if isinstance(obj,dict) else []:
   if 'characterId' in r and 'id' in r and isinstance(r['characterId'],int) and 1<=r['characterId']<=12:entity[str(r['id'])]=CHAR[r['characterId']-1]
  if name.startswith('storytheater-'):
   for group in obj.get('list',[]):
    sid=group.get('subChapterId')
    for row in group.get('storyList',[]):
     if sid is not None:
      if row.get('nodeId'):stage_to_chapter[row['nodeId']]=sid
      if row.get('storyId'):scene_map[row['storyId']]=('剧情',*chapter(sid));provenance[row['storyId']]=name
  if name.startswith('story-loading-cg'):
   for r in obj.get('list',[]):
    for scene in r.get('storyIds',[]):scene_map[scene]=('剧情',*chapter(r['id']));provenance[scene]=name
  if name.startswith('storytheater-personality'):
   for r in records(obj):
    if r.get('storyId'):personas[r['storyId']]=name
 # Native part/subchapter/stage graph, including dungeon encounters.
 for name,obj in static.items():
  if not name.startswith('part'):continue
  for group in obj.get('list',[]):
   for sub in group.get('subchapterList',[]):
    for node in sub.get('stageNodeList',[]):
     for field in ('nodeId','stageId'):
      if node.get(field):stage_to_chapter[node[field]]=sub['id']
 for name,obj in static.items():
  if not name.startswith('dungeonMap'):continue
  for dungeon in obj.get('list',[]):
   sid=stage_to_chapter.get(dungeon.get('id'))
   if sid is not None:
    for r in records(dungeon):
     if r.get('encounterId'):stage_to_chapter[r['encounterId']]=sid
 # Stage objects connect battle-triggered story segments to their actual parent chapter.
 allscenes={f.stem[3:] for f in (kr/'StoryData').glob('KR_*.json')}
 for name,obj in static.items():
  if not name.startswith(('storybattle','abbattle','stagenode')):continue
  for r in obj.get('list',[]):
   sid=stage_to_chapter.get(r.get('id'))
   refs=set(strings(r)) & allscenes
   parents={scene_map[s][1:] for s in refs if s in scene_map}
   if sid is not None:parent=chapter(sid)
   elif len(parents)==1:parent=next(iter(parents))
   else:continue
   for scene in refs:
    if scene not in scene_map:scene_map[scene]=('剧情',*parent);provenance[scene]=name
 for name,obj in static.items():
  if not name.startswith(('storybattle','abbattle')):continue
  refs=set(strings(obj)) & allscenes;parents={scene_map[x] for x in refs if x in scene_map}
  if len(parents)==1:
   for scene in refs:
    if scene not in scene_map:scene_map[scene]=next(iter(parents));provenance[scene]=name+'（同组章节一致）'
 # Scene configuration files are native chapter-specific groups. Only extend
 # a group when every already classified member agrees on one chapter.
 for name,obj in static.items():
  if not name.startswith('story-config'):continue
  refs=set(strings(obj)) & allscenes
  parents={scene_map[x] for x in refs if x in scene_map}
  if len(parents)==1:
   for scene in refs:
    if scene not in scene_map:scene_map[scene]=next(iter(parents));provenance[scene]=name+'（同组章节一致）'
 # Use literal native scene links only, not voice number prefixes.
 for _ in range(3):
  for scene,obj in native.get('effects',{}).items():
   if scene not in scene_map:continue
   for ref in set(strings(obj)) & allscenes:
    if ref not in scene_map:scene_map[ref]=scene_map[scene];provenance[ref]='场景链接 '+scene
 for scene in personas:scene_map[scene]=('其他','人格剧情',120000);provenance[scene]=personas[scene]
 # An event's unlock chapter is not its narrative chapter.
 specials=set()
 for name,obj in static.items():
  if name.startswith(('open-story-by-date','loading-cg-exception')):specials.update(set(strings(obj)) & allscenes)
 for scene in specials:
  if scene not in scene_map:scene_map[scene]=('剧情','活动与特殊剧情',90000);provenance[scene]='游戏特殊剧情入口'
 # Native narrative records without a published chapter link retain their scene identity.
 base=p.rows();items=[];consumed=set();unlinked=[];voice_links=p.load(p.DATA/'story-voice-links.json',{})
 for r in base:
  scene=r['scene'];tab,category,rank=scene_map.get(scene,('剧情','补充剧情片段',95000))
  if scene not in scene_map:unlinked.append(scene)
  r.update(tab=tab,chapter=category,rank=rank,sample_key=voice_links.get(r['key'],r['key']),display_key=voice_links.get(r['key'],r['key']),role_name=role_label(r['speaker']),source_type='剧情',classification_source=provenance.get(scene,'原生StoryData及场景语音字段；未提供章节关联'))
  items.append(r)
  if r['asset']:consumed.add((str(Path(r['asset'])).lower(),r['sample_key']))
 # Read zero-association voice text by exact ID. Never strip suffixes to guess a partial clip's text.
 textmap=collections.defaultdict(list);announcers={};personalities={}
 for f in cn.glob('Announcer*.json'):
  for r in p.load(f,{}).get('dataList',[]):
   if r.get('name'):announcers[str(r['id'])]=r['name']
 for f in cn.glob('Personalities*.json'):
  for r in p.load(f,{}).get('dataList',[]):personalities[str(r.get('id'))]=r.get('name','')
 for folder,cat in [('EGOVoiceDig','EGO'),('BattleAnnouncerDlg','播报'),('PersonalityVoiceDlg','人格与大厅')]:
  for f in (cn/folder).glob('*.json'):
   ident=f.stem.rsplit('_',1)[-1];actor=entity.get(ident)
   if cat=='EGO':
    if ident.isdigit() and 1<=int(ident)<=12:actor=CHAR[int(ident)-1]
    label=actor or '共用／协同 EGO'
   elif cat=='播报':label=announcers.get(ident,f.stem)
   else:label=(actor+' · ' if actor else '')+(personalities.get(ident) or f.stem)
   ko=p.load(kr/folder/('KR_'+f.name),{});ko={str(r['id']):r.get('dlg','') for r in ko.get('dataList',[]) if 'id' in r}
   for r in p.load(f,{}).get('dataList',[]):
    if not isinstance(r.get('id'),str):continue
    category=cat
    if cat=='人格与大厅' and (r['id'].startswith('battle') or r.get('desc') in ('进入战斗','进行攻击','人格出战')):category='战斗'
    textmap[r['id']].append(dict(text=r.get('dlg',''),speaker=folder+'::'+ident,role_name=label,category=category,transcript=ko.get(r['id'],''),text_file=str(f)))
 storybanks={Path(x['asset']).name for x in base if x['asset']}
 banks=p.load(p.DATA/'bank-index.json',{});leftovers=collections.Counter();missingtext=collections.Counter();duplicate_text=[]
 for asset,entry in banks.items():
  bank=Path(asset).name;sample_counts=collections.Counter(entry['keys'])
  for sample_index,sample in enumerate(entry['keys']):
   if (str(Path(asset)).lower(),sample) in consumed:continue
   choices=textmap.get(sample,[]);unique={(x['text'],x['speaker']):x for x in choices}
   if len(unique)==1:
    m=next(iter(unique.values()));category=m['category'];label=m['role_name'];speaker=m['speaker'];text=m['text'];transcript=m['transcript'];evidence=m['text_file']
   else:
    if len(unique)>1:duplicate_text.append(sample)
    text='';transcript='';speaker='resource::'+Path(asset).stem;label=bank.replace('.assets.bank','');evidence='原生资源包；无唯一同ID零协台词'
    if 'Announcer' in bank:category='播报'
    elif 'EGO' in bank or 'Ego' in bank:category='EGO'
    elif bank.startswith('Voice_'):category='战斗与角色语音片段'
    elif 'BGM' in bank or 'Music' in bank:category='背景音乐'
    elif bank in storybanks:category='剧情附属音频'
    else:category='场景与效果音'
    missingtext[category]+=1
   if not text.strip():continue
   key=sample if re.fullmatch(r'[A-Za-z0-9_-]+',sample) else 'audio-'+hashlib.sha256(sample.encode()).hexdigest()[:20]
   # Include the bank identity: two distinct assets never overwrite each other.
   key+='--'+hashlib.sha256(bank.encode()).hexdigest()[:10]
   if sample_counts[sample]>1:key+='-'+str(sample_index)
   items.append(dict(key=key,display_key=sample,sample_key=sample,sample_index=sample_index,scene=Path(asset).stem,asset=asset,speaker=speaker,role_name=label,text=text,transcript=transcript,tab='其他',chapter=category,rank=120000,source_type=category,classification_source=evidence))
   leftovers[category]+=1
 # Persist library atomically as a separate catalog; the story database remains intact.
 items.sort(key=lambda r:(r['tab']!='剧情',r['rank'],r['chapter'],r['scene']))
 target=p.DATA/'audio-library.json';temp=target.with_suffix('.tmp');temp.write_text(json.dumps(items,ensure_ascii=False),encoding='utf8');temp.replace(target)
 report=dict(total=len(items),counts=dict(collections.Counter(r['tab']+' / '+r['chapter'] for r in items)),missing_chinese=dict(missingtext),supplementary_scenes=sorted(set(unlinked)),conflicting_text_ids=sorted(set(duplicate_text)),original_unclassified=5133,excluded_without_chinese=sum(missingtext.values()))
 (p.DATA/'audio-classification-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
 log('分类完成：'+str(len(items))+' 条本地音频与剧情台词')
 return report

def rows():return [r for r in p.load(p.DATA/'audio-library.json',[]) if isinstance(r.get('text'),str) and r['text'].strip()]
