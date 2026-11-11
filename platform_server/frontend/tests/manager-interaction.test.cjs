const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const read=name=>fs.readFileSync(path.join(__dirname,'../dist',name),'utf8');
const campus=read('campus.js'),plain=value=>JSON.parse(JSON.stringify(value));
const manager={id:'manager-suhyun',type:'manager',action:'manager_greet',name:'수현 매니저',x:92,y:92,w:16,h:16};
const dialogue='이곳에는 숨겨진 이스터에그가 많다던데...';

function surface(){
  const listeners=new Map();
  return {open:false,hidden:false,disabled:false,style:{},children:[],attributes:{},textContent:'',focused:false,
    addEventListener(type,fn){if(!listeners.has(type))listeners.set(type,[]);listeners.get(type).push(fn);},
    emit(type,event={}){for(const fn of listeners.get(type)||[]){fn(event);if(event.stopped)break;}},
    setAttribute(key,value){this.attributes[key]=String(value);},removeAttribute(key){delete this.attributes[key];},
    replaceChildren(...children){this.children=children;},append(...children){this.children.push(...children);},
    focus(){this.focused=true;},showModal(){this.open=true;},close(){this.open=false;this.emit('close');},
    querySelector(){return this.button||(this.button=surface());},
  };
}
function extract(start,end){
  const a=campus.indexOf(start),b=campus.indexOf(end,a);
  assert.ok(a>=0&&b>a,`missing production section: ${start}`);
  return campus.slice(a,b);
}
function fixture(){
  const document=surface(),window=surface(),elements=new Map(),commands=[],inputs=[],poking=[],events=[],errors=[];
  const element=id=>{if(!elements.has(id))elements.set(id,surface());return elements.get(id);};
  document.getElementById=element;document.createElement=surface;document.body={append(){}};
  window.matchMedia=()=>({matches:false});window.dispatchEvent=event=>events.push(event);
  const npc={id:manager.id,x:100,y:100,managerNpc:true,avatar:'male-1',nickname:manager.name,dir:'down'};
  window.CampusWorlds={campus:{npcs:[npc],objects:[manager]},rooftop:{npcs:[],objects:[]}};
  const player={id:'self',x:60,y:100,dir:'right',floor:'campus',moving:true};
  const presence={connected:true,selfId:'self',peers:new Map(),setInput:keys=>inputs.push([...keys]),
    setPoking:active=>poking.push(active),command(action,data){
      return new Promise((resolve,reject)=>commands.push({action,data,resolve,reject}));
    }};
  const context=vm.createContext({document,window,performance:{now:()=>100},
    CustomEvent:class{constructor(type,options){this.type=type;this.detail=options.detail;}},
    setTimeout:()=>1,clearTimeout(){},setInterval:()=>1,clearInterval(){},
    $:element,player,presence,keys:new Set(),suppressedKeys:new Set(),
    movementKeys:new Set(['w','a','s','d','arrowup','arrowdown','arrowleft','arrowright']),
    managerGreetingPending:false,logoutPending:false,achievements:null,minigames:null,directMessages:null,
    classroom:{blocking:false},floorTransition:{blocking:false},social:{isTyping:false},boothUI:{blocking:false},
    dialog:element('detailDialog'),boardDialog:element('boardDialog'),exitDialog:element('exitDialog'),
    transferDialog:element('transferDialog'),canvas:element('mapCanvas'),D:{rooms:[]},nearby:manager,
    toast:message=>errors.push(message),zoomBy(){},jump:{trigger(){}},
  });
  vm.runInContext(read('keyboard.js'),context);
  vm.runInContext(read('poke.js'),context);
  const gate=campus.split('\n').find(line=>line.includes('const isDialogOpen='));
  assert.ok(gate);vm.runInContext(gate.replace('const isDialogOpen=','globalThis.isDialogOpen='),context);
  context.poke=window.CampusPoke.create({getPlayer:()=>player,getPresence:()=>presence,
    getClassroom:()=>context.classroom,getInput:()=>[...context.keys],isBlocked:()=>context.isDialogOpen(false)});
  vm.runInContext(extract('  function showDetail(','  function showBoard('),context);
  vm.runInContext(extract("  document.addEventListener('keydown',e=>{","  window.addEventListener('blur'"),context);
  for(const marker of ["  $('closeDialog').onclick=", "  dialog.addEventListener('close',", "  $('interactionPrompt').querySelector('button').onclick="]){
    const line=campus.split('\n').find(line=>line.startsWith(marker));assert.ok(line);vm.runInContext(line,context);
  }
  const input=campus.split('\n').find(line=>line.includes('presence.setInput(!isDialogOpen()'));
  assert.ok(input);vm.runInContext(`globalThis.publishInput=()=>{${input}};`,context);
  return {context,document,window,element,player,presence,npc,commands,inputs,poking,events,errors,
    key(key,code){const event={key,code,repeat:false,isComposing:false,ctrlKey:false,altKey:false,metaKey:false,
      target:{closest:()=>null},preventDefault(){this.prevented=true;},stopImmediatePropagation(){this.stopped=true;}};
      document.emit('keydown',event);return event;},
  };
}
const settle=async()=>{await Promise.resolve();await Promise.resolve();};

test('a recovered connection refreshes achievements while preserving chat connection state',()=>{
  const connected=[],refreshes=[],context={social:{setConnected:value=>connected.push(value)},achievements:null};
  const callback=campus.match(/onConnectionChange\(value\)\{[^}]*\}/)?.[0];assert.ok(callback);
  const change=vm.runInNewContext('('+callback.replace('onConnectionChange','function')+')',context);
  change(true);assert.deepEqual(connected,[true],'initial connection tolerates achievement UI initialization');
  context.achievements={refresh:()=>refreshes.push('server read')};
  change(false);assert.equal(refreshes.length,0,'disconnection must not request unavailable state');
  change(true);assert.deepEqual(refreshes,['server read']);assert.deepEqual(connected,[true,false,true]);
});

for(const route of ['showDetail','E key','interaction button'])test(`${route} waits for server greeting and opens its exact response`,async()=>{
  const f=fixture();
  if(route==='showDetail')f.context.showDetail(manager);
  else if(route==='E key')assert.equal(f.key('e','KeyE').prevented,true);
  else f.element('interactionPrompt').querySelector('button').onclick();
  assert.equal(f.commands.length,1);
  assert.deepEqual(plain({action:f.commands[0].action,data:f.commands[0].data}),{action:'manager_greet',data:{objectId:manager.id}});
  assert.equal(f.element('detailDialog').open,false,'local map data cannot supply a greeting');
  f.context.showDetail(manager);assert.equal(f.commands.length,1,'pending greeting ignores repeated interaction');
  const achievements=[{event_uuid:'server-achievement',name:'수상한 친화력',achieved:true}];
  f.commands[0].resolve({dialogue,managerName:'서버가 확인한 수현 매니저',achievements});await settle();
  assert.equal(f.element('detailDialog').open,true);
  assert.equal(f.element('detailText').textContent,dialogue);
  assert.equal(f.element('detailTitle').textContent,'서버가 확인한 수현 매니저');
  assert.equal(f.element('detailEyebrow').textContent,'MANAGER');
  assert.equal(f.element('closeDialog').focused,true);
  assert.deepEqual(plain(f.events),[{type:'campus:achievements',detail:achievements}]);
});

test('pending greeting and open dialogue stop held poke, movement and new game input',async()=>{
  const f=fixture();f.context.keys.add('w');f.context.poke.setHeld(true);
  assert.equal(f.context.poke.held,true);
  f.context.showDetail(manager);
  assert.equal(f.player.moving,false);assert.equal(f.context.keys.size,0);assert.equal(f.context.poke.held,false);
  assert.deepEqual(f.poking,[true,false]);assert.deepEqual(f.inputs.at(-1),[]);
  for(const state of ['pending','dialogue']){
    if(state==='dialogue'){f.commands[0].resolve({dialogue,managerName:manager.name});await settle();}
    f.key('w','KeyW');f.key('z','KeyZ');assert.equal(f.context.keys.size,0);assert.equal(f.context.poke.held,false);
    f.context.keys.add('w');f.context.publishInput();assert.deepEqual(f.inputs.at(-1),[]);
    f.context.keys.clear();f.context.poke.setHeld(true);assert.equal(f.context.poke.held,false);
  }
  f.element('detailDialog').close();
  assert.equal(f.element('mapCanvas').focused,true);assert.equal(f.context.isDialogOpen(),false);
  f.key('w','KeyW');assert.equal(f.context.keys.has('w'),true);
});

for(const invalid of ['server rejection','missing dialogue','missing manager name'])test(`${invalid} shows an error and permits retry without local dialogue`,async()=>{
  const f=fixture();f.context.showDetail(manager);
  if(invalid==='server rejection')f.commands[0].reject(new Error('매니저 가까이에서 인사해 주세요.'));
  else f.commands[0].resolve(invalid==='missing dialogue'?{managerName:manager.name}:{dialogue});
  await settle();
  assert.equal(f.element('detailDialog').open,false);assert.equal(f.element('detailText').textContent,'');
  assert.equal(f.errors.length,1);assert.equal(f.context.isDialogOpen(),false);assert.equal(f.events.length,0);
  f.context.showDetail(manager);assert.equal(f.commands.length,2);
});

test('an existing blocking dialog prevents manager commands',()=>{
  const f=fixture();f.element('settingsDialog').open=true;
  f.context.showDetail(manager);f.key('e','KeyE');f.element('interactionPrompt').querySelector('button').onclick();
  assert.equal(f.commands.length,0);assert.equal(f.element('detailDialog').open,false);
});

test('server poke hit mutates the rendered manager actor and never requests a greeting',()=>{
  const f=fixture(),packet={type:'poke_hit',id:'hit-1',actorId:'self',targetId:manager.id,
    context:'world',floor:'campus',dir:'right',blocked:false,peperoBefore:0};
  const target=f.window.CampusPoke.targets('campus').find(actor=>actor.id===manager.id);
  assert.equal(target,f.npc,'renderer and poke must share one manager actor');
  f.context.poke.setHeld(true);assert.equal(f.commands.length,0);
  f.context.poke.receive({...packet,floor:'rooftop'});assert.equal(f.npc._campusPokeReaction,undefined);
  f.context.poke.receive(packet);
  assert.deepEqual(plain(f.npc._campusPokeReaction),{started:100,dir:'right'});
  const reaction=f.npc._campusPokeReaction;f.context.poke.receive(packet);assert.equal(f.npc._campusPokeReaction,reaction);
  const translated=[],rotated=[];
  f.window.CampusPoke.react({translate:(...args)=>translated.push(args),rotate:value=>rotated.push(value)},
    f.npc,150,{inverseVector:(x,y)=>({x,y})});
  assert.equal(translated.length,1);assert.equal(rotated.length,1);
  assert.equal(f.commands.length,0);assert.equal(f.element('detailDialog').open,false);
});
