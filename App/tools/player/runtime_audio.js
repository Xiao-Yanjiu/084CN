let storySelectedVoice=null;
'use strict';
// All hooks live in process memory. No game files are written.
const hooks=[], callbacks=[], active=new Map();
let replacements={}, system=null, core=null, ready=false, lastSweep=0;
const nil=ptr(0), P=Process.pointerSize;
let api={},sweepTimer=null;
let narration=null;
function stopNarration(){if(narration){finishChinese(narration);narration=null;}}
function playNarration(file,key,voice){
 if(!core){send({type:'warning',message:'剧情音频系统尚未就绪'});return false;}
 let sound=nil,channel=nil;
 try{const s=out(),c=out();checked(api.create(core,Memory.allocUtf8String(file),0x80,nil,s),'load narration');sound=s.readPointer();
 checked(api.play(core,sound,nil,1,c),'play narration');channel=c.readPointer();checked(api.channelVolume(channel,key.startsWith('unvoiced-')&&voice&&/^D_/.test(voice)?1.35:1),'narration volume');checked(api.channelPause(channel,0),'start narration');
 narration={sound,channel,key,voice};send({type:'replaced',event:key});return true;
 }catch(e){if(!channel.isNull())api.channelStop(channel);if(!sound.isNull())api.soundRelease(sound);send({type:'fallback',message:String(e)});return false;}
}
function chinesePlaying(){
 const entries=[...active.values()];if(narration)entries.push(narration);
 for(const a of entries){if(a.channel.isNull())continue;if(a.path&&!a.path.startsWith('event:/Voice_Story/'))continue;
 const state=Memory.alloc(4);if(api.playing(a.channel,state)===0&&state.readS32())return true;}return false;
}
function fn(module,name,args){return new NativeFunction(module.getExportByName(name),'int',args);}
function out(){return Memory.alloc(P);}
function checked(result,what){if(result!==0)throw Error(what+': FMOD '+result);}
function pathOf(event){const d=out(),b=Memory.alloc(4096),n=Memory.alloc(4);checked(api.desc(event,d),'description');checked(api.path(d.readPointer(),b,4096,n),'event path');return b.readUtf8String();}
function finishChinese(a){if(!a.channel.isNull()){api.channelStop(a.channel);api.soundRelease(a.sound);a.channel=nil;a.sound=nil;}}
function dispose(key,restore){const a=active.get(key);if(!a)return;active.delete(key);try{finishChinese(a);if(restore)api.volume(a.event,a.volume);}catch(e){send({type:'warning',message:String(e)});}}
function install(){
 const studio=Process.findModuleByName('fmodstudio.dll'), fm=Process.findModuleByName('fmod.dll')||studio;if(!studio||!fm)return false;
 api={desc:fn(studio,'FMOD_Studio_EventInstance_GetDescription',['pointer','pointer']),path:fn(studio,'FMOD_Studio_EventDescription_GetPath',['pointer','pointer','int','pointer']),
 getCore:fn(studio,'FMOD_Studio_System_GetCoreSystem',['pointer','pointer']),flush:fn(studio,'FMOD_Studio_System_FlushCommands',['pointer']),
 group:fn(studio,'FMOD_Studio_EventInstance_GetChannelGroup',['pointer','pointer']),volume:fn(studio,'FMOD_Studio_EventInstance_SetVolume',['pointer','float']),getVolume:fn(studio,'FMOD_Studio_EventInstance_GetVolume',['pointer','pointer','pointer']),
 parent:fn(fm,'FMOD_ChannelGroup_GetParentGroup',['pointer','pointer']),create:fn(fm,'FMOD_System_CreateSound',['pointer','pointer','uint','pointer','pointer']),play:fn(fm,'FMOD_System_PlaySound',['pointer','pointer','pointer','int','pointer']),
 channelVolume:fn(fm,'FMOD_Channel_SetVolume',['pointer','float']),channelPause:fn(fm,'FMOD_Channel_SetPaused',['pointer','int']),channelStop:fn(fm,'FMOD_Channel_Stop',['pointer']),playing:fn(fm,'FMOD_Channel_IsPlaying',['pointer','pointer']),soundRelease:fn(fm,'FMOD_Sound_Release',['pointer']),state:fn(studio,'FMOD_Studio_EventInstance_GetPlaybackState',['pointer','pointer'])};
 const systemHook=Interceptor.attach(studio.getExportByName('FMOD_Studio_System_Update'),{onEnter(args){system=args[0];if(!core){const p=out();if(api.getCore(system,p)===0){core=p.readPointer();systemHook.detach();}}}});hooks.push(systemHook);
 sweepTimer=setInterval(()=>{if(typeof storyAudioTick==='function')storyAudioTick();for(const [key,a] of active){try{const p=Memory.alloc(4);if(!a.channel.isNull()&&(api.playing(a.channel,p)!==0||!p.readS32()))finishChinese(a);if(a.channel.isNull()){const state=Memory.alloc(4);const result=api.state(a.event,state);if(result!==0)dispose(key,false);else if(state.readS32()===2)dispose(key,true);}}catch(e){dispose(key,false);}}},100);
 hooks.push(Interceptor.attach(studio.getExportByName('FMOD_Studio_EventInstance_Start'),{
 onEnter(args){this.event=args[0];this.pending=null;if(!core||!system)return;let sound=nil;try{const path=pathOf(this.event),file=storySelectedVoice&&path.startsWith('event:/Voice_Story/')&&path.substring(path.lastIndexOf('/')+1)===storySelectedVoice.voice?storySelectedVoice.file:replacements[path];
// Clock events are muted only for the exact active Dante dialogue with a playable WAV.
if(narration&&narration.voice&&/^D_/.test(narration.voice)&&path==='event:/Voice/Default/'+narration.voice){
 const v=Memory.alloc(4);checked(api.getVolume(this.event,v,nil),'clock volume');
 active.set(this.event.toString(),{event:this.event,volume:v.readFloat(),sound:nil,channel:nil});checked(api.volume(this.event,0),'mute current Dante clock');return;}
if(!file)return;dispose(this.event.toString(),true);const s=out();checked(api.create(core,Memory.allocUtf8String(file),0,nil,s),'load Chinese WAV');sound=s.readPointer();const v=Memory.alloc(4);checked(api.getVolume(this.event,v,nil),'get event volume');this.pending={event:this.event,sound,volume:v.readFloat(),path};checked(api.volume(this.event,0),'mute matched original');}catch(e){if(!sound.isNull())api.soundRelease(sound);this.pending=null;send({type:'fallback',message:String(e)});}},
 onLeave(result){const a=this.pending;if(!a)return;let channel=nil;try{checked(result.toInt32(),'start original event');checked(api.flush(system),'flush event commands');const g=out(),parent=out();checked(api.group(a.event,g),'event group');checked(api.parent(g.readPointer(),parent),'voice parent group');if(parent.readPointer().isNull())throw Error('No voice parent group');const c=out();checked(api.play(core,a.sound,parent.readPointer(),1,c),'play Chinese');channel=c.readPointer();checked(api.channelVolume(channel,a.volume),'Chinese volume');checked(api.channelPause(channel,0),'start Chinese');a.channel=channel;active.set(a.event.toString(),a);send({type:'replaced',event:a.path});}catch(e){if(!channel.isNull())api.channelStop(channel);api.soundRelease(a.sound);api.volume(a.event,a.volume);send({type:'fallback',event:a.path,message:String(e)});}}
 }));
 hooks.push(Interceptor.attach(studio.getExportByName('FMOD_Studio_EventDescription_CreateInstance'),{onEnter(args){this.output=args[1];},onLeave(result){if(result.toInt32()===0)dispose(this.output.readPointer().toString(),false);}}));
 const setVolume=new NativeCallback((event,volume)=>{const a=active.get(event.toString());if(a){a.volume=volume;if(!a.channel.isNull())api.channelVolume(a.channel,volume);return api.volume(event,0);}return api.volume(event,volume);},'int',['pointer','float']);callbacks.push(setVolume);Interceptor.replace(studio.getExportByName('FMOD_Studio_EventInstance_SetVolume'),setVolume);
 hooks.push(Interceptor.attach(studio.getExportByName('FMOD_Studio_EventInstance_Stop'),{onEnter(args){dispose(args[0].toString(),true);}}));
 hooks.push(Interceptor.attach(studio.getExportByName('FMOD_Studio_EventInstance_SetPaused'),{onEnter(args){const a=active.get(args[0].toString());if(a&&!a.channel.isNull())api.channelPause(a.channel,args[1].toInt32());}}));
 // release is deferred by FMOD for one-shots; do not stop Chinese on release.
 ready=true;send({type:'ready',message:'FMOD runtime hooks ready'});return true;
}
let timer=setInterval(()=>{try{if(install())clearInterval(timer);}catch(e){clearInterval(timer);send({type:'fatal',message:String(e)});}},250);
rpc.exports={configure(mapping){replacements=mapping;return Object.keys(mapping).length;},status(){return {ready,active:active.size,mapped:Object.keys(replacements).length,core:!!core};},shutdown(){if(typeof storyAutoShutdown==='function')storyAutoShutdown();if(sweepTimer)clearInterval(sweepTimer);stopNarration();clearInterval(timer);for(const key of Array.from(active.keys()))dispose(key,true);Interceptor.detachAll();const m=Process.findModuleByName('fmodstudio.dll');if(m)Interceptor.revert(m.getExportByName('FMOD_Studio_EventInstance_SetVolume'));return true;}};
