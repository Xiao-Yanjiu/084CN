import ctypes as c,json
from pathlib import Path
import sys
r=Path(__file__).resolve().parents[2];sys.path.insert(0,str(r))
import studio_project
g=studio_project.game()
from studio_event_cache import inputs,signature
path,dll=inputs(g); fingerprint=signature(g);print('Loading FMOD libraries',flush=True);f=c.CDLL(str(dll));corelib=f
def call(name,types,*args):
 fn=getattr(f,name);fn.argtypes=types;fn.restype=c.c_int
 result=fn(*args)
 if result:raise RuntimeError((name,result))
p=c.c_void_p;system=p();call('FMOD_Studio_System_Create',[c.POINTER(p),c.c_uint],c.byref(system),0x00020200)
core=p();call('FMOD_Studio_System_GetCoreSystem',[p,c.POINTER(p)],system,c.byref(core))
setoutput=corelib.FMOD_System_SetOutput;setoutput.argtypes=[p,c.c_int];setoutput.restype=c.c_int
result=setoutput(core,2)
if result:raise RuntimeError(('FMOD_System_SetOutput NOSOUND',result))
print('Initializing FMOD without audio device',flush=True)
call('FMOD_Studio_System_Initialize',[p,c.c_int,c.c_uint,c.c_uint,p],system,32,4,0,None)
print('Reading string bank',flush=True)
bank=p()
call('FMOD_Studio_System_LoadBankFile',[p,c.c_char_p,c.c_uint,c.POINTER(p)],system,str(path).encode(),0,c.byref(bank))
count=c.c_int();call('FMOD_Studio_Bank_GetStringCount',[p,c.POINTER(c.c_int)],bank,c.byref(count))
print('Event count',count.value,flush=True)
paths=[]
for i in range(count.value):
 guid=c.create_string_buffer(16);buf=c.create_string_buffer(4096);size=c.c_int()
 call('FMOD_Studio_Bank_GetStringInfo',[p,c.c_int,p,p,c.c_int,c.POINTER(c.c_int)],bank,i,guid,buf,4096,c.byref(size))
 paths.append(buf.value.decode())
(r/'Config/player').mkdir(parents=True,exist_ok=True)
target=r/'Config/player/events.json'
temp=target.with_suffix('.tmp');temp.write_text(json.dumps(paths,ensure_ascii=False,indent=2),encoding='utf8');temp.replace(target)
(r/'Config/player/events-signature.json').write_text(json.dumps(fingerprint),encoding='utf8')
print(len(paths),paths[:5]);call('FMOD_Studio_System_Release',[p],system)
