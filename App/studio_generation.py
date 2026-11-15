"""Content-first generation. ASR agreement is evidence, never listening acceptance."""
import json
import re
import uuid
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

VERSION = 'direct-generation-v2-no-asr'
FORMAT_TAG = re.compile(r'</?(?:color|size|b|i|u|s|voffset|sprite|font|align|br)(?:=[^<>]*|\s[^<>]*)?>', re.I)

# Explicitly approved readings; no general deletion of unknown characters.
APPROVED_READINGS={
    '#&#(!@&$を':'……',
    '#&*()@%&!)$&8%&*%#%#!':'……',
    '*抽泣* ':'',
    '为了解放■■而':'为了解放……而',
    '打起し':'打起精神',
}

def spoken_text(text, redaction_pause=False):
    # Only known markup is removed. Dante's <spoken dialogue> is not markup.
    text = re.sub(r'<ruby=[^>]*>.*?</ruby>', '', text or '', flags=re.I|re.S)
    text=FORMAT_TAG.sub('', text).replace('<', '').replace('>', '')
    for source,replacement in APPROVED_READINGS.items():text=text.replace(source,replacement)
    # User-approved numeric association names, including shortened references.
    for source,replacement in {'Öufi':'十一','Öu':'十一','하나':'一','Девять':'九','ДЕВЯТЬ':'九','し协会':'四协会'}.items():
        text=text.replace(source,replacement)
    # Non-dialogue production notes, confirmed from the full-library scan.
    if re.fullmatch(r'(?://효과연출[12]|SE // 와장창!|로보토미 백야 cg|\^0\^/)',text.strip()):return ''
    text=re.sub(r'\*(?:抽泣|哔哔声|吹口哨)\*','……',text)
    text=text.replace('♬','').replace('※','').replace('*','')
    text=re.sub(r'[#@%$^&][#@%$^&!*()]*','……',text) if '#' in text or '@' in text or '&%' in text else text
    text=re.sub(r'(?<=[A-F])\+','加',text)
    text=text.replace('^0^/','')
    text=text.replace('N/A','不适用').replace('//','……').replace('/','，')
    text=text.replace('砲','炮').replace('𫚉',' hong2 ').replace('鴶',' jia2 ')
    text=text.replace('^0^/','').replace('よりょしゅく','夜良秀苦')
    # Accent marks are unsupported by the vocabulary; retain base letters.
    text=re.sub(r'[À-ž]',lambda m:''.join(c for c in unicodedata.normalize('NFD',m.group()) if not unicodedata.combining(c)) if m.group()!='Ö' else m.group(),text)
    return re.sub(r'■+', '……', text) if redaction_pause else text

def normalized(text):
    from opencc import OpenCC
    return ''.join(re.findall(r'[\u3400-\u9fffA-Za-z0-9]', OpenCC('t2s').convert(text))).lower()

def segments(text, limit=90):
    # Keep short prefixes ("是，", "这样啊……") with following context.
    # Never discard punctuation, rewrite words, or split a single word prefix off.
    result=[]
    current=''
    for token in re.findall(r'.*?[。！？!?；;\n]+|.+$', text, re.S):
        if current and len(normalized(current)) >= 8 and normalized(token):
            result.append(current);current=''
        current += token
        if len(normalized(current)) > limit:
            # Split long clauses only at existing punctuation with enough context.
            pieces=re.findall(r'.*?[，,：:]+|.+$',current,re.S)
            chunk=''
            for piece in pieces:
                if len(normalized(chunk))>=30 and len(normalized(chunk+piece))>limit:
                    result.append(chunk);chunk=''
                chunk+=piece
            current=chunk
    if current:result.append(current)
    assert ''.join(result)==text, 'Internal error: segmentation lost source text'
    return result

def differences(expected, heard):
    want,actual=normalized(expected),normalized(heard)
    return [dict(kind=op,expected=want[a:b],heard=actual[c:d],start=a,end=b)
            for op,a,b,c,d in SequenceMatcher(None,want,actual,autojunk=False).get_opcodes() if op!='equal']

class ContentAudit:
    def __init__(self):
        self.whisper=None
        self.para=None
    def check(self,path,expected):
        from studio_core import ROOT,BUNDLE
        from faster_whisper import WhisperModel
        from funasr import AutoModel
        if self.whisper is None:
            self.whisper=WhisperModel(str(ROOT/'tools/faster-whisper-small'),device='cpu',compute_type='int8',cpu_threads=3,local_files_only=True)
        if self.para is None:
            self.para=AutoModel(model=str(BUNDLE/'tools/asr/models/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch'),device='cpu',ncpu=3,disable_update=True,disable_log=True,disable_pbar=True)
        # No expected-text prompt, no short-line bypass, no tone-stripping pass rule.
        parts,_=self.whisper.transcribe(str(path),language='zh',beam_size=5,condition_on_previous_text=False,vad_filter=False)
        heard=''.join(p.text for p in parts)
        other=''.join(p.get('text','') for p in self.para.generate(input=str(path),disable_pbar=True))
        diffs=[differences(expected,heard),differences(expected,other)]
        return dict(status='asr_agreement' if not any(diffs) else 'needs_listening',
                    whisper=heard,paraformer=other,differences=diffs,
                    listening_accepted=False)

_shared_audit=None
def get_content_audit():
    global _shared_audit
    if _shared_audit is None:_shared_audit=ContentAudit()
    return _shared_audit


def wave_check(path):
    import numpy as np
    import soundfile as sf
    data,rate=sf.read(path,dtype='float32',always_2d=True)
    if not len(data) or not np.isfinite(data).all():raise ValueError('合成结果为空或含无效采样')
    peak=float(np.max(np.abs(data)))
    if peak<1e-5:raise ValueError('合成结果没有可听信号')
    return data,rate

def render(text,output,backend,audit,log=print,redaction_pause=False):
    import numpy as np
    import soundfile as sf
    source_text=text
    text=spoken_text(text,redaction_pause=redaction_pause)
    if not normalized(text):raise ValueError('本句只有标点，保留原声，不生成中文')
    output=Path(output)
    work=output.parent/(output.stem+'.work')
    work.mkdir(parents=True,exist_ok=True)
    report=dict(version=VERSION,text=text,source_text=source_text,text_transforms=[{'kind':'redaction_pause','source':m.group(),'replacement':'……'} for m in re.finditer(r'■+',source_text)] if redaction_pause else [],segments=[],listening_accepted=False,
                emotion_status='punctuation_only; independent emotion conditioning unavailable',
                source_pronunciation_transfer=False)
    if text!=source_text:report['text_transforms'].append({'kind':'spoken_text_preparation','source':source_text,'replacement':text})
    report['text_transforms'].extend({'kind':'user_approved_reading','source':source,'replacement':replacement} for source,replacement in APPROVED_READINGS.items() if source in source_text)
    chunks=[];rate=None
    try:
        for i,part in enumerate(segments(text)):
            log(f'正在生成 · 第 {i+1} 段')
            attempts=[];best=None
            for attempt in range(2):
                path=work/f'{i}-{attempt}.wav'
                try:
                    backend(part,path,42+attempt*59)
                    data,sr=wave_check(path)
                    clipping_ratio=float(np.mean(np.abs(data)>=.999))
                    check=dict(status='generated_unverified',attempt=attempt+1,
                               near_full_scale_ratio=clipping_ratio,listening_accepted=False)
                    attempts.append(check)
                    best=(0,data,sr,check)
                    break
                except Exception as exc:
                    attempts.append(dict(attempt=attempt+1,error=str(exc)))
            report['segments'].append(dict(text=part,attempts=attempts,selected=best[3] if best else None))
            if best is None:raise RuntimeError(f'第 {i+1} 段生成失败：'+str(attempts[-1]))
            _,data,sr,check=best
            if rate is not None and sr!=rate:raise ValueError('段落采样率不一致')
            rate=sr
            if chunks:
                # Keep model pauses; add only enough silence to separate sentence boundaries.
                prev=chunks[-1]
                def silent_edge(a,reverse=False):
                    v=np.max(np.abs(a),axis=1)
                    if reverse:v=v[::-1]
                    active=np.flatnonzero(v>1e-4)
                    return int(active[0]) if len(active) else len(v)
                gap=max(0,int(rate*.20)-silent_edge(prev,True)-silent_edge(data))
                if gap:chunks.append(np.zeros((gap,data.shape[1]),dtype=np.float32))
            fade=min(round(sr*.005),len(data)//2)
            if fade:
                data=data.copy()
                data[:fade]*=np.linspace(0,1,fade)[:,None]
                data[-fade:]*=np.linspace(1,0,fade)[:,None]
            chunks.append(data)
        temp=output.with_suffix('.partial.wav')
        sf.write(temp,np.concatenate(chunks),rate,subtype='PCM_16')
        final_audio,_=wave_check(temp)
        report['audio_warning']='接近满幅采样较多，请试听是否失真' if float(np.mean(np.abs(final_audio)>=.999))>.001 else None
        report['status']='generated_unverified'
        report['transcription_check']='disabled_by_user'
        log('音频已生成 · 待试听')
        temp.replace(output)
        return report
    finally:
        output.with_suffix('.verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
        # Keep only the selected final candidate and its report, not duplicate retry audio.
        for p in work.glob('*.wav'):p.unlink()
        try:work.rmdir()
        except OSError:pass


def require_content(report,path):
    if report.get('status')=='generated_unverified':
        wave_check(path)
        return
    if report.get('status')=='asr_agreement':
        return
    checks=[report.get('final',report)]
    checks.extend(segment['selected'] for segment in report.get('segments',[]) if segment.get('selected'))
    for check in checks:
        diffs=check.get('differences',[])
        length_issue=lambda ds:any(d['kind'] in ('insert','delete') or len(d['expected'])!=len(d['heard']) for d in ds)
        # Conflicting recognizers require listening; agreement on missing content blocks processing.
        if diffs and all(length_issue(ds) for ds in diffs):
            raise RuntimeError('两份转写均出现疑似增字或漏字，已暂停后续处理。请核对报告：'+str(Path(path).with_suffix('.verification.json')))
    # Equal-length replacements are reviewable, not automatically accepted.
    if not all('differences' in check for check in checks):
        raise RuntimeError('文字检查未完成，不能继续生成')


def staged_character(row,profile,output,synth,log,variant='default',timbre_only=False):
    from studio_core import extract,wav_path
    if not profile['gpt'] or not profile['sovits']:
        raise ValueError('该角色需要先完成 GPT-SoVITS 训练，请在角色与训练页开始训练。')
    extract(row['asset'],[row['key']])
    def backend(text,path,seed):
        synth.generate(profile,text,wav_path(row['key']),row['transcript'],1.0,path,seed)
    report=render(row['text'],output,backend,synth.content_audit,log)
    if not timbre_only:require_content(report,output)
    return report
