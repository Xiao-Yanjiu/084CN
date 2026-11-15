from pathlib import Path
import ctypes as c
import json
import re
import sqlite3
import struct
import wave
import uuid

ROOT = Path(__file__).resolve().parent
GAME = Path('.')
BUNDLE = ROOT / 'tools/GPT-SoVITS-v2pro-20250604'
DATA = ROOT / 'Config'
DATA.mkdir(exist_ok=True)
NAMES = dict(zip('이상 파우스트 돈키호테 료슈 뫼르소 홍루 히스클리프 이스마엘 로쟈 싱클레어 오티스 그레고르'.split(),
                 '李箱 浮士德 堂吉诃德 良秀 默尔索 鸿璐 希斯克利夫 伊实玛利 罗佳 辛克莱 奥提斯 格里高尔'.split()))

def db():
    con = sqlite3.connect(str(DATA / 'studio.db'), timeout=20)
    con.row_factory = sqlite3.Row
    con.executescript("""
    CREATE TABLE IF NOT EXISTS lines (
      key TEXT PRIMARY KEY, scene TEXT, speaker TEXT, text TEXT, asset TEXT,
      transcript TEXT DEFAULT '', checked INTEGER DEFAULT 0, approved TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS profiles (
      id INTEGER PRIMARY KEY, speaker TEXT, name TEXT, reference TEXT DEFAULT '',
      gpt TEXT DEFAULT '', sovits TEXT DEFAULT '', UNIQUE(speaker,name));
    CREATE TABLE IF NOT EXISTS native_sources (key TEXT PRIMARY KEY, scene TEXT NOT NULL, line_id INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS github_audio (key TEXT PRIMARY KEY, entry TEXT NOT NULL, filename TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS github_profile_ref (profile INTEGER, engine TEXT, key TEXT NOT NULL, PRIMARY KEY(profile,engine));
    CREATE TABLE IF NOT EXISTS stale_candidates (path TEXT PRIMARY KEY);
    CREATE TABLE IF NOT EXISTS native_redrafted (key TEXT PRIMARY KEY);
    CREATE TABLE IF NOT EXISTS native_changed (key TEXT PRIMARY KEY, original TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS source_remaps (key TEXT PRIMARY KEY, original TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS source_audit (key TEXT PRIMARY KEY, heard TEXT, score REAL, status TEXT);
    CREATE TABLE IF NOT EXISTS cosy_weights (profile INTEGER PRIMARY KEY, path TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS candidate_quality (path TEXT PRIMARY KEY, heard TEXT, score REAL);
    CREATE TABLE IF NOT EXISTS candidate_engines (path TEXT PRIMARY KEY, engine TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS candidate_scripts (path TEXT PRIMARY KEY, text TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS translation_references (key TEXT PRIMARY KEY, text TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS dub_scripts (key TEXT PRIMARY KEY, text TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS line_settings (key TEXT PRIMARY KEY, use_line INTEGER, speed REAL, note TEXT, seed INTEGER, ref_key TEXT);
    CREATE TABLE IF NOT EXISTS training_sources (profile INTEGER, key TEXT, PRIMARY KEY(profile,key));
    CREATE TABLE IF NOT EXISTS candidates (
      path TEXT PRIMARY KEY, key TEXT, profile INTEGER, direction TEXT, speed REAL, reference TEXT);
    CREATE TABLE IF NOT EXISTS unmatched (asset TEXT, name TEXT, PRIMARY KEY(asset,name));
    """)
    return con

def soundbank(path):
    raw = Path(path).read_bytes()
    pos = raw.index(b'FSB5')
    version, count, headers, names, audio = struct.unpack_from('<5I', raw, pos + 4)
    head = 60 if version == 1 else 64
    start = pos + head + headers
    if count > 100000 or names < count * 4 or start + names + audio > len(raw):
        raise ValueError('Unsupported FSB5 layout')
    offsets = struct.unpack_from('<%dI' % count, raw, start)
    keys = []
    for offset in offsets:
        if offset >= names:
            raise ValueError('Bad FSB5 name offset')
        end = raw.index(b'\0', start + offset, start + names)
        keys.append(raw[start + offset:end].decode('utf-8', errors='replace'))
    return keys, raw[pos:start + names + audio]

def scan(log):
    folder = GAME / 'Lang/LLC_zh-CN/StoryData'
    banks = GAME / 'StreamingAssets/Assets/Sound/FMODBuilds/Desktop'
    if not folder.is_dir() or not banks.is_dir():
        raise RuntimeError('找不到当前游戏的零协剧情或 FMOD 目录')
    scripts = {}
    for path in folder.rglob('*.json'):
        obj = json.loads(path.read_text(encoding='utf-8-sig'))
        if isinstance(obj, dict):
            scripts[path.stem] = obj.get('dataList') or []
    korean = {}
    for path in (GAME / 'Assets/Resources_moved/Localize/kr/StoryData').rglob('KR_*.json'):
        obj = json.loads(path.read_text(encoding='utf-8-sig'))
        if isinstance(obj, dict):
            korean[path.stem[3:]] = {item['id']: item for item in obj.get('dataList', [])
                                     if isinstance(item,dict) and 'id' in item}
    matches = []
    unmatched = []
    errors = []
    for index, asset in enumerate(sorted(banks.glob('*.assets.bank')), 1):
        scene = asset.name[:-12]
        try:
            keys, _ = soundbank(asset)
        except (ValueError, OSError, struct.error) as exc:
            if scene in scripts:
                errors.append((asset.name, str(exc)))
            continue
        # Treat names as candidates only. Training requires explicit source review.
        mapping = {}
        native = korean.get(scene, {})
        chinese = {item['id']: item for item in scripts.get(scene, [])
                   if isinstance(item, dict) and isinstance(item.get('id'), int)}
        expected = {f'{scene}-{idx + 1:02d}' for idx in native if isinstance(idx,int)}
        local_keys = {key for key in keys if key.startswith(scene+'-')}
        if local_keys and local_keys <= expected and len(set(keys)) == len(keys):
            mapping = {f'{scene}-{idx + 1:02d}': (item, chinese.get(idx,{})) for idx,item in native.items()
                       if isinstance(idx,int) and item.get('model') and item.get('content')}
        for key in keys:
            pair = mapping.get(key)
            if pair:
                ko, cn = pair
                translation=cn.get('content','') if cn.get('model')==ko['model'] else ''
                matches.append((key,scene,ko['model'],translation,str(asset),ko['content']))
            elif scene in korean or scene in scripts:
                unmatched.append((str(asset), key))
        if index % 100 == 0:
            log(f'已扫描 {index} 个资源包，发现 {len(matches)} 条可核对台词')
    with db() as con:
        con.execute('DELETE FROM unmatched')
        con.executemany('INSERT OR IGNORE INTO unmatched VALUES (?,?)', unmatched)
        con.executemany('''INSERT INTO lines(key,scene,speaker,text,asset,transcript) VALUES(?,?,?,?,?,?)
            ON CONFLICT(key) DO UPDATE SET transcript=CASE
            WHEN checked=1 AND speaker=excluded.speaker AND asset=excluded.asset THEN transcript
            WHEN excluded.transcript<>'' THEN excluded.transcript
            WHEN speaker=excluded.speaker AND asset=excluded.asset THEN transcript ELSE '' END,
            checked=CASE WHEN speaker=excluded.speaker
            AND asset=excluded.asset THEN checked ELSE 0 END,
            approved=CASE WHEN text=excluded.text AND speaker=excluded.speaker
            AND asset=excluded.asset THEN approved ELSE '' END, scene=excluded.scene,
            speaker=excluded.speaker,text=excluded.text,asset=excluded.asset
            WHERE NOT EXISTS(SELECT 1 FROM source_remaps WHERE key=lines.key) AND NOT EXISTS(SELECT 1 FROM native_sources WHERE key=lines.key)''', matches)
        con.execute('DELETE FROM unmatched WHERE EXISTS (SELECT 1 FROM lines WHERE lines.key=unmatched.name AND lines.asset=unmatched.asset)')
        con.executemany('INSERT OR IGNORE INTO profiles(speaker,name) VALUES (?,?)',
                        [(speaker, '基础音色') for speaker in {r[2] for r in matches}])
    (DATA / 'scan-errors.json').write_text(json.dumps(errors, ensure_ascii=False, indent=2), encoding='utf-8')
    return f'{len(matches)} 条候选，{len(unmatched)} 条场景音频未配对，{len(errors)} 个场景包无法解析'

_native_path_cache={}
_native_path_mtime=None

def wav_path(key):
    global _native_path_cache,_native_path_mtime
    index=DATA/'game-audio-export/native-paths.json'
    try:
        stamp=index.stat().st_mtime_ns
        if stamp!=_native_path_mtime:
            _native_path_cache=json.loads(index.read_text(encoding='utf-8'));_native_path_mtime=stamp
        if key in _native_path_cache:return Path(_native_path_cache[key])
    except (OSError,ValueError):pass
    if not re.fullmatch(r'[A-Za-z0-9_-]+', key):
        raise ValueError('Invalid sample key')
    return DATA / 'original' / (key + '.wav')

def extract(asset, selected):
    todo = {key for key in selected if not wav_path(key).exists()}
    if not todo:
        return
    keys, fsb = soundbank(asset)
    if not todo <= set(keys):
        raise ValueError('音频索引已变化，请重新扫描')
    wav_path(next(iter(todo))).parent.mkdir(exist_ok=True)
    import subprocess
    scratch = DATA / ('decode-' + uuid.uuid4().hex + '.fsb')
    try:
        scratch.write_bytes(fsb)
        decoder = ROOT / 'tools/vgmstream/vgmstream-cli.exe'
        for key in todo:
            target = wav_path(key)
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_name(target.stem + '.' + uuid.uuid4().hex + '.wav')
            try:
                subprocess.run(
                    [str(decoder), '-i', '-s', str(keys.index(key) + 1),
                     '-o', str(temp), str(scratch)],
                    check=True, capture_output=True,
                    creationflags=subprocess.CREATE_NO_WINDOW)
                with wave.open(str(temp), 'rb') as audio:
                    if audio.getnframes() == 0:
                        raise RuntimeError('Empty decoded source: ' + key)
                temp.replace(target)
            finally:
                temp.unlink(missing_ok=True)
    finally:
        scratch.unlink(missing_ok=True)


def collect(speaker, log, transcribe=False):
    with db() as con:
        rows = con.execute('SELECT * FROM lines WHERE speaker=? ORDER BY scene,key', (speaker,)).fetchall()
    groups = {}
    for row in rows:
        groups.setdefault(row['asset'], []).append(row['key'])
    failed = []
    for i, (asset, keys) in enumerate(groups.items(), 1):
        try:
            extract(asset, keys)
        except (ValueError, RuntimeError, OSError) as exc:
            failed.append(f'{Path(asset).name}: {exc}')
        if i % 20 == 0:
            log(f'已提取 {i}/{len(groups)} 个场景')
    if transcribe:
        from faster_whisper import WhisperModel
        model_path = ROOT / 'tools/faster-whisper-small'
        if not (model_path / 'model.bin').exists():
            raise RuntimeError('韩语转写模型尚未部署，原声已保存')
        model = WhisperModel(str(model_path), device='cpu', compute_type='int8', local_files_only=True)
        for index, row in enumerate(rows, 1):
            if not row['transcript'] and wav_path(row['key']).exists():
                segments, _ = model.transcribe(str(wav_path(row['key'])), language='ko', beam_size=3,
                                               condition_on_previous_text=False)
                text = ' '.join(s.text.strip() for s in segments).strip()
                with db() as con:
                    con.execute('UPDATE lines SET transcript=? WHERE key=?', (text, row['key']))
            if index % 10 == 0:
                log(f'韩语转写 {index}/{len(rows)}')
    if failed:
        (DATA/'extract-failures.txt').write_text('\n'.join(failed),encoding='utf-8')
    count=sum(wav_path(r['key']).exists() for r in rows)
    return f'已收集 {count}/{len(rows)} 条候选原声，{len(failed)} 个包有错误'




def align_scene(scene, log):
    """Recover scene-specific voice mappings with local Korean ASR and script text."""
    from difflib import SequenceMatcher
    from faster_whisper import WhisperModel
    if not re.fullmatch(r'[A-Za-z0-9_-]+',scene):
        raise ValueError('请输入有效场景 ID')
    cn = GAME/'Lang/LLC_zh-CN/StoryData'/(scene+'.json')
    ko = GAME/'Assets/Resources_moved/Localize/kr/StoryData'/('KR_'+scene+'.json')
    if not ko.is_file():
        raise ValueError('这个场景缺少原版韩语剧本')
    cn = {r['id']:r for r in json.loads(cn.read_text(encoding='utf-8-sig'))['dataList'] if 'id' in r} if cn.is_file() else {}
    ko = json.loads(ko.read_text(encoding='utf-8-sig'))['dataList']
    native={r['id']:r for r in ko if 'id' in r}
    def norm(text):
        return re.sub(r'[^가-힣0-9a-z]', '', re.sub(r'<[^>]+>','',text).lower())
    texts = [(r['id'], r['content'], norm(r['content'])) for r in ko
             if r.get('content') and r.get('model') and 'id' in r]
    texts = [t for t in texts if len(t[2])>=7]
    with db() as con:
        known = {r[0] for r in con.execute('SELECT key FROM lines')}
        assets = con.execute('SELECT * FROM unmatched WHERE asset LIKE ?', ('%\\'+scene+'.assets.bank',)).fetchall()
    if not assets:
        raise ValueError('这个场景没有待配对音频')
    assets = [r for r in assets if r['name'] not in known and re.fullmatch(r'[A-Za-z0-9_-]+',r['name'])]
    if not assets:
        raise ValueError('此场景剩余音频已被其他场景引用或没有有效语音键')
    extract(assets[0]['asset'],[r['name'] for r in assets])
    model = WhisperModel(str(ROOT/'tools/faster-whisper-small'),device='cpu',compute_type='int8',local_files_only=True)
    proposals = []
    for i,a in enumerate(assets,1):
        log(f'韩语对齐 {scene} · {i}/{len(assets)}')
        key = a['name']
        segments,_ = model.transcribe(str(wav_path(key)),language='ko',beam_size=2,condition_on_previous_text=False)
        heard = norm(''.join(s.text for s in segments))
        if len(heard)<7: continue
        scores=sorted(((SequenceMatcher(None,heard,t[2]).ratio(),t) for t in texts),reverse=True)
        if not scores: continue
        score,(idx,original,_) = scores[0]
        if score>=0.91 and (len(scores)==1 or score-scores[1][0]>=0.12):
            proposals.append((idx,key,original,a['asset']))
    result = 0
    with db() as con:
        for idx,key,original,asset in proposals:
            if sum(p[0]==idx for p in proposals)!=1: continue
            item=native[idx]
            ref=cn.get(idx,{})
            text=ref.get('content','') if ref.get('model')==item['model'] else ''
            row=con.execute('INSERT OR IGNORE INTO lines(key,scene,speaker,text,asset,transcript) VALUES (?,?,?,?,?,?)',
                            (key,scene,item['model'],text,asset,original))
            if row.rowcount:
                result+=1
                con.execute('INSERT OR IGNORE INTO profiles(speaker,name) VALUES (?,?)',(item['model'],'基础音色'))
                con.execute('DELETE FROM unmatched WHERE asset=? AND name=?',(asset,key))
    return f'{scene} 新增 {result} 条待审核的韩中音频对应'









