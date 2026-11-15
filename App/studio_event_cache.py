import json
from pathlib import Path

def inputs(game):
 game=Path(game)
 bank=game/'StreamingAssets/Assets/Sound/FMODBuilds/Desktop/Master.strings.bank'
 dll=game/'Plugins/x86_64/fmodstudio.dll'
 if not bank.is_file():bank=next(game.rglob('Master.strings.bank'))
 if not dll.is_file():dll=next(game.rglob('fmodstudio.dll'))
 return bank,dll

def signature(game):
 return [dict(path=str(p.resolve()),size=p.stat().st_size,mtime=p.stat().st_mtime_ns) for p in inputs(game)]

def valid(data,game):
 try:
  events=json.loads((data/'events.json').read_text(encoding='utf8'))
  return bool(events) and isinstance(events,list) and all(isinstance(e,str) for e in events) and any(e.startswith('event:/') for e in events) and json.loads((data/'events-signature.json').read_text(encoding='utf8'))==signature(game)
 except (OSError,ValueError,StopIteration):return False
