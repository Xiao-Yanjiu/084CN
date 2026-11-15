from collections import Counter, defaultdict
from functools import lru_cache
import json
import re
from studio_core import GAME, NAMES

@lru_cache(maxsize=1)
def role_names():
    root=GAME/'Lang/LLC_zh-CN'
    def rows(path):
        if not path.is_file(): return []
        return json.loads(path.read_text(encoding='utf-8-sig')).get('dataList',[])
    def chinese(value):
        return isinstance(value,str) and re.search(r'[\u3400-\u9fff]',value) and not re.search(r'[\uac00-\ud7af]',value)
    names=dict(NAMES)
    names.update({'__dante__':'但丁','__narrator__':'旁白'})
    names.update({r['id']:r['name'] for r in rows(root/'ScenarioModelCodes-AutoCreated.json')
                  if isinstance(r.get('id'),str) and chinese(r.get('name'))})
    aliases=defaultdict(Counter)
    for path in (root/'StoryData').glob('*.json'):
        for r in rows(path):
            if r.get('model') and chinese(r.get('teller')):
                aliases[r['model']][r['teller']]+=1
    for role, labels in aliases.items():
        names.setdefault(role,labels.most_common(1)[0][0])
    names.update({'???':'身份未公开','//효과음':'效果音','더미':'占位角色','더미2':'广播或群演',
        '피묻은크로머':'克罗默（染血）','크로머목소리':'克罗默（声音）','여자시민':'友好的市民'})
    return names

def role_label(role):
    if role.startswith('E001X::'):
        return role_label(role.split('::',1)[1])+' · E001X独立音色'
    if role.startswith('teller::'):
        from studio_core import DATA
        try:return json.loads((DATA/'native-speaker-names.json').read_text(encoding='utf8')).get(role,role.split('::',1)[1])
        except (OSError,ValueError):return role.split('::',1)[1]
    if role.startswith('unknown::'):return '未具名角色 · '+role.split('::',1)[1]
    return role_names().get(role,role)

