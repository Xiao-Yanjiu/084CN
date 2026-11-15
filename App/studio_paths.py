"""Local game path discovery; no recursive drive scans."""
from pathlib import Path
import os,re
import studio_project as service

def validate(value):
 p=Path(value.strip().strip('"')) if value.strip() else None
 if p is None:return None,'请添加游戏路径'
 if p.name.lower()=='limbuscompany.exe':p=p.parent
 if (p/'LimbusCompany_Data').is_dir():p=p/'LimbusCompany_Data'
 required=[('游戏音频',p/'StreamingAssets/Assets/Sound/FMODBuilds/Desktop'),('零协中文',p/'Lang/LLC_zh-CN/StoryData'),('韩语剧情',p/'Assets/Resources_moved/Localize/kr/StoryData')]
 missing=[name for name,folder in required if not folder.is_dir()]
 if missing:return None,'路径不可用：缺少'+'、'.join(missing)
 if not any(required[0][1].glob('*.assets.bank')):return None,'路径不可用：没有游戏音频资源包'
 return p,'已找到游戏音频、零协中文和韩语剧情'

def discover():
 candidates=[service.game()];roots=[]
 try:
  import winreg
  for hive,key,field in [(winreg.HKEY_CURRENT_USER,r'Software\Valve\Steam','SteamPath'),(winreg.HKEY_LOCAL_MACHINE,r'SOFTWARE\WOW6432Node\Valve\Steam','InstallPath')]:
   try:
    with winreg.OpenKey(hive,key) as k:roots.append(Path(winreg.QueryValueEx(k,field)[0]))
   except OSError:pass
 except ImportError:pass
 for env in ('ProgramFiles(x86)','ProgramFiles'):
  if os.environ.get(env):roots.append(Path(os.environ[env])/'Steam')
 for letter in 'CDEFGHIJKLMNOPQRSTUVWXYZ':
  for suffix in ('Steam','SteamLibrary','steam'):
   folder=Path(letter+':/')/suffix
   if folder.is_dir():roots.append(folder)
 libraries=set(roots)
 for root in roots:
  for file in (root/'steamapps/libraryfolders.vdf',root/'config/libraryfolders.vdf'):
   try:text=file.read_text(encoding='utf8',errors='replace')
   except OSError:continue
   for match in re.findall(r'"path"\s*"([^"]+)"',text):libraries.add(Path(match.replace('\\\\','\\')))
 for root in libraries:
  candidates.append(root/'steamapps/common/Limbus Company')
  for manifest in (root/'steamapps').glob('appmanifest_*.acf'):
   try:text=manifest.read_text(encoding='utf8',errors='replace')
   except OSError:continue
   if re.search(r'"name"\s*"Limbus Company"',text,re.I):
    match=re.search(r'"installdir"\s*"([^"]+)"',text)
    if match:candidates.append(root/'steamapps/common'/match[1])
 for candidate in dict.fromkeys(candidates):
  found,_=validate(str(candidate))
  if found:return found
 return None
