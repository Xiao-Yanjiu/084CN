let storyAudioTick=null,storyAutoShutdown=null;
// Resolve metadata on every attach. No absolute method addresses or field offsets.
let storyIndex=new Map(),storyConflictVoices=new Set();
let storyRows=[],storyHooks=[],storyReady=false,storyIdentity='',storyGeneration=0;
function chooseStoryRow(rows,d){
 const prefix=[d.scene,d.model,d.teller];
 const exact=storyIndex.get(JSON.stringify([...prefix,d.id,d.content]));
 if(exact!==undefined)return exact;
 return storyIndex.get(JSON.stringify([...prefix,d.content]))||null;
}
function addStoryIdentity(key,row){
 const k=JSON.stringify(key);
 if(!storyIndex.has(k))storyIndex.set(k,row);
 else if(storyIndex.get(k)!==row)storyIndex.set(k,null);
}
function installStory(){
 const m=Process.getModuleByName('GameAssembly.dll');
 const call=(n,r,a)=>new NativeFunction(m.getExportByName(storyApiNames[n]||n),r,a,{scheduling:'exclusive'});
 const domain=call('il2cpp_domain_get','pointer',[]),attach=call('il2cpp_thread_attach','pointer',['pointer']);
 const assemblies=call('il2cpp_domain_get_assemblies','pointer',['pointer','pointer']),image=call('il2cpp_assembly_get_image','pointer',['pointer']),imageName=call('il2cpp_image_get_name','pointer',['pointer']);
 const klass=call('il2cpp_class_from_name','pointer',['pointer','pointer','pointer']),method=call('il2cpp_class_get_method_from_name','pointer',['pointer','pointer','int']);
 const field=call('il2cpp_class_get_field_from_name','pointer',['pointer','pointer']),offset=call('il2cpp_field_get_offset','int',['pointer']);
 const slen=call('il2cpp_string_length','int',['pointer']),schars=call('il2cpp_string_chars','pointer',['pointer']);
 function str(p){if(p.isNull())return '';const n=slen(p);if(n<0||n>32768)throw Error('invalid managed string');return schars(p).readUtf16String(n);}
 const d=domain();attach(d);const n=Memory.alloc(8),aa=assemblies(d,n);let im=null;
 for(let i=0;i<n.readU32();i++){const x=image(aa.add(i*8).readPointer());if(imageName(x).readUtf8String()==='Assembly-CSharp.dll')im=x;}
 if(!im)throw Error('Assembly-CSharp missing');
 function cls(name){const x=klass(im,Memory.allocUtf8String('StorySystem'),Memory.allocUtf8String(name));if(x.isNull())throw Error(name+' missing');return x;}
 function off(c,name){const f=field(c,Memory.allocUtf8String(name));if(f.isNull())throw Error('field missing '+name);return offset(f);}
 function addr(c,name,count){const x=method(c,Memory.allocUtf8String(name),count);if(x.isNull()||x.readPointer().isNull())throw Error('method missing '+name);return x.readPointer();}
 const manager=cls('StoryManager'),dialog=cls('Dialog'),scenario=cls('Scenario');
 const sid=off(manager,'_storyID'),scenarioId=off(scenario,'ID'),currentDialog=off(manager,'_dialog');let lastSnapshot='';
 // Hold only automatic progression, never a manual click/input method.
 const autoClass=cls('StoryAutoProgressManager'),autoAddress=addr(autoClass,'AutoScenario',0);
 const autoEnabled=new NativeFunction(addr(autoClass,'IsAuto',0),'bool',['pointer','pointer']);
 const setReady=new NativeFunction(addr(autoClass,'ReadyForNext',2),'void',['pointer','int','bool','pointer']);
 const restartWait=new NativeFunction(addr(autoClass,'StartWaitTime',1),'void',['pointer','float','pointer']);
 let autoScopes=new Map(),pendingAuto=null,resumeHook=null;
 function cancelAutoResume(){pendingAuto=null;if(resumeHook){resumeHook.detach();resumeHook=null;}}
 storyHooks.push(Interceptor.attach(autoAddress,{onEnter(a){this.tid=this.threadId;this.previous=autoScopes.get(this.tid);autoScopes.set(this.tid,{self:a[0]});},onLeave(){if(this.previous)autoScopes.set(this.tid,this.previous);else autoScopes.delete(this.tid);}}));
 storyHooks.push(Interceptor.attach(addr(autoClass,'IsReady',0),{onLeave(ret){
  const scope=autoScopes.get(this.threadId);
  if(scope&&ret.toInt32()&&chinesePlaying()){
   ret.replace(0);pendingAuto={...scope,generation:storyGeneration};send({type:'auto_wait_chinese'});
  }
 }}));
 storyAudioTick=()=>{
  if(!pendingAuto||resumeHook||chinesePlaying())return;
  resumeHook=Interceptor.attach(addr(manager,'Update',0),{onEnter(){
   const hook=resumeHook;resumeHook=null;hook.detach();const request=pendingAuto;pendingAuto=null;
   if(!request||request.generation!==storyGeneration||!autoEnabled(request.self,ptr(0)))return;
   // Only schedule Unity's existing wait coroutine. Never call AutoScenario from JS:
   // its later engine-driven continuation keeps all voice hooks on the normal path.
   // Current hash-validated build: CheckWaitTime and StartWaitTime use controller 9.
   setReady(request.self,9,0,ptr(0));restartWait(request.self,0.05,ptr(0));send({type:'auto_wait_restarted',generation:request.generation});
  }});
 };
 storyAutoShutdown=()=>{cancelAutoResume();storyAudioTick=null;};
 const fields={};for(const f of ['OriginalId','Model','Teller','Content','Voice'])fields[f]=off(dialog,f);
 function emit(scene,p,source){
  if(p.isNull())return;
  const started=Date.now();
  const model=str(p.add(fields.Model).readPointer()),teller=str(p.add(fields.Teller).readPointer());
  const currentVoice=str(p.add(fields.Voice).readPointer());
  if(model!=='단테'&&teller!=='단테'&&(model||teller)&&!storyConflictVoices.has(currentVoice)){
   storySelectedVoice=null;cancelAutoResume();const generation=++storyGeneration;storyIdentity='';if(narration)setImmediate(()=>{if(generation===storyGeneration)stopNarration();});return;
  }
  const d={scene,id:p.add(fields.OriginalId).readS32(),model,teller,content:str(p.add(fields.Content).readPointer()),voice:currentVoice,source};
  if(model!=='단테'&&teller!=='단테'&&(model||teller)){
   const selected=chooseStoryRow(null,d);
   storySelectedVoice=selected&&selected.voice?{voice:selected.voice,file:selected.file}:null;
   cancelAutoResume();const generation=++storyGeneration;storyIdentity='';if(narration)setImmediate(()=>{if(generation===storyGeneration)stopNarration();});return;
  }
  storySelectedVoice=null;
  const identity=JSON.stringify([d.scene,d.id,d.model,d.teller,d.content,p.toString()]);
  if(identity===storyIdentity)return;storyIdentity=identity;cancelAutoResume();const generation=++storyGeneration;
  // Copy managed data while it is valid, but do disk/FM0D work off the game callback.
  setImmediate(()=>{
   if(generation!==storyGeneration)return;
   stopNarration();const r=chooseStoryRow(null,d);
   send({type:'story_dialogue',...d,matched:r?r.key:null});
   if(r&&!r.voice){const ok=playNarration(r.file,r.key,d.voice);send({type:'story_latency',key:r.key,elapsed_ms:Date.now()-started,started:ok});}
  });
 }
 // Common audio stage takes the actual Dialog, including its native voice field.
 storyHooks.push(Interceptor.attach(addr(manager,'PlaySound',1),{onEnter(a){try{emit(str(a[0].add(sid).readPointer()),a[1],'PlaySound');}catch(e){send({type:'warning',message:'story: '+e});}}}));
 storyHooks.push(Interceptor.attach(addr(manager,'DisplayMarkerStoryDialog',10),{onEnter(a){try{emit(str(a[3].add(scenarioId).readPointer()),a[4],'MarkerDialog');}catch(e){send({type:'warning',message:'marker: '+e});}}}));
 for(const name of ['EndScenario','EndStory','OnDisable'])storyHooks.push(Interceptor.attach(addr(manager,name,0),{onEnter(){cancelAutoResume();storyIdentity='';const generation=++storyGeneration;setImmediate(()=>{if(generation===storyGeneration)stopNarration();});}}));
 // Observe the common current-dialog state, including attachment mid-line.
 // Read full strings only when the scene/dialog object changes.
 let snapshotHook=Interceptor.attach(addr(manager,'Update',0),{onEnter(a){try{
  const dp=a[0].add(currentDialog).readPointer(),sp=a[0].add(sid).readPointer();
  const stamp=a[0].toString()+':'+sp.toString()+':'+dp.toString();
  if(stamp===lastSnapshot||dp.isNull())return;lastSnapshot=stamp;
  snapshotHook.detach();emit(str(sp),dp,'CurrentDialog');
 }catch(e){send({type:'warning',message:'current dialog: '+e});}}});storyHooks.push(snapshotHook);
 storyReady=true;send({type:'story_ready',message:'Native scene/dialogue hook ready'});
}
rpc.exports.configurestory=function(rows){storyRows=rows;storyIndex=new Map();storyConflictVoices=new Set(rows.filter(r=>r.voice).map(r=>r.voice));for(const r of rows){for(const text of new Set([r.text,r.korean])){if(!text)continue;const prefix=[r.scene,r.model,r.teller];addStoryIdentity([...prefix,r.id,text],r);addStoryIdentity([...prefix,text],r);}}if(!storyReady){try{installStory();}catch(e){for(const h of storyHooks)h.detach();storyHooks=[];throw e;}}return {ready:storyReady,mapped:rows.length};};
