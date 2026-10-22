const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');

function app({authenticated = true, secure = false, floors, restore = false, session} = {}) {
  const elements = new Map(), sockets = [], timers = new Map(), intervals = new Map(), listeners = new Map();
  let clock = 0, nextTimer = 0, redirected = null, expired = 0;
  const chats = [], chatErrors = [], connectionChanges = [], restores = [], controlErrors = [], jumps = [], pokes = [];
  const events = [];
  function domElement() {
    return {
      hidden:true,textContent:'',dataset:{},children:[],attributes:{},
      setAttribute(name,value){this.attributes[name]=String(value);},
      getAttribute(name){return this.attributes[name]??null;},
      replaceChildren(...items){this.children=[...items];},
      append(...items){this.children.push(...items);},
    };
  }
  function element(id) {
    if (id === 'campusPresenceConfig' && !authenticated) return null;
    if (!elements.has(id)) elements.set(id, {
      ...domElement(),
      addEventListener(name, callback) {listeners.set(id + ':' + name, callback);},
    });
    return elements.get(id);
  }
  if (authenticated) element('campusPresenceConfig').textContent = JSON.stringify({socketPath: '/ws/campus/', loginUrl: '/accounts/login/'});
  class Socket {
    static OPEN = 1; static CLOSED = 3;
    constructor(url) {this.url = url; this.readyState = 1; this.sent = []; sockets.push(this);}
    send(text) {this.sent.push(JSON.parse(text));}
    close(code = 1000) {this.readyState = 3; this.onclose?.({code});}
    receive(data) {this.onmessage({data: JSON.stringify(data)});}
  }
  const protocol = secure ? 'https:' : 'http:';
  const context = vm.createContext({URL, Event, WebSocket: Socket, performance: {now: () => clock},
    window: {location: {protocol, href: protocol + '//127.0.0.1:8000/', assign(url) {redirected = url;}},
      addEventListener(name, callback) {listeners.set('window:' + name, callback);}, dispatchEvent(event) {events.push(event.type);}, CampusSession: session},
    document: {getElementById: element, createElement: () => domElement(),
      addEventListener(name, callback) {listeners.set('document:' + name, callback);}},
    setTimeout(callback, delay) {const id = ++nextTimer; timers.set(id, {callback, delay}); return id;},
    clearTimeout(id) {timers.delete(id);},
    setInterval(callback, delay) {const id = ++nextTimer; intervals.set(id, {callback, delay}); return id;},
    clearInterval(id) {intervals.delete(id);},
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../dist/presence.js'), 'utf8'), context);
  const player = {x: 1262.125, y: 437.875, dir: 'down', color: '#dfa34d', moving: false};
  const presence = context.window.CampusPresence.create({player, width: 1632, height: 820, floors, onAuthExpired() {expired++;},
    onRestore(position) {restores.push(position);if(restore)Object.assign(player,position);},
    onChat(message) {chats.push(message);}, onChatError(message) {chatErrors.push(message);}, onConnectionChange(value) {connectionChanges.push(value);},
    onControlError(message) {controlErrors.push(message);}, onJump(message) {jumps.push(message);}, onPoke(message) {pokes.push(message);}});
  const peer = (changes = {}) => ({id: 'peer', nickname: '조원 이름', avatar: 'female-2', x: 1200, y: 437,
    dir: 'down', color: '#4e9b91', moving: false, ...changes});
  function welcome(socket = sockets.at(-1)) {
    socket.receive({type: 'welcome', selfId: 'self', players: [peer({id: 'self', nickname: '내 이름'}), peer()]});
  }
  return {presence, player, sockets, timers, intervals, listeners, element, peer, welcome, events, chats, chatErrors, connectionChanges, restores, controlErrors, jumps, pokes,
    tick(t, dt = .016) {clock = t; presence.tick(t, dt);},
    get redirected() {return redirected;}, get expired() {return expired;}};
}

const test = require('node:test');
const floors={campus:{width:2448,height:1230,objects:[]},rooftop:{width:2448,height:1800}};
const plain=value=>JSON.parse(JSON.stringify(value));
test('standalone creates no socket; authenticated connections negotiate server control and WSS',()=>{
 const offline=app({authenticated:false});offline.tick(200);assert.equal(offline.sockets.length,0);
 for(const secure of [false,true]){const f=app({secure});assert.equal(f.sockets[0].url,`${secure?'wss':'ws'}://127.0.0.1:8000/ws/campus/?control=server&moves=compact`);f.presence.close();}
});
test('only allowed keyboard inputs are sent; client coordinates never become movement commands',()=>{
 const f=app({floors});f.welcome();f.presence.setInput(['w','d','d','teleport']);
 assert.deepEqual(plain(f.sockets[0].sent.at(-1)),{type:'input',keys:['d','w']});
 f.player.x=2400;f.tick(200);assert.deepEqual(plain(f.sockets[0].sent.at(-1)),{type:'input',keys:['d','w']});
 f.presence.close();assert.deepEqual(plain(f.sockets[0].sent.at(-1)),{type:'leave'});assert.equal(f.intervals.size,0);
});
test('an idle player sends no input; held keys keep the 0.1s refresh and a release is sent once',()=>{
 const f=app({floors});f.welcome();const inputs=()=>f.sockets[0].sent.filter(packet=>packet.type==='input').length;
 const idle=inputs();for(let t=100;t<=1000;t+=16)f.tick(t);assert.equal(inputs(),idle);
 f.presence.setInput(['w']);const pressed=inputs();for(let t=1016;t<=1500;t+=16)f.tick(t);assert.ok(inputs()-pressed>=4);
 f.presence.setInput([]);assert.deepEqual(plain(f.sockets[0].sent.at(-1)),{type:'input',keys:[]});const released=inputs();
 for(let t=1516;t<=2500;t+=16)f.tick(t);assert.equal(inputs(),released);f.presence.close();
});
test('accepted state supplies self position and interpolates remote walking across both floors',()=>{
 const f=app({floors});f.welcome();assert.deepEqual(f.element('presenceStatus').children.slice(1).map(child=>child.textContent),['접속 중','2','명']);
 f.sockets[0].receive({type:'move',player:f.peer({x:1230.25,moving:true})});f.tick(116);
 assert.ok(f.presence.peers.get('peer').x>1200&&f.presence.peers.get('peer').x<1230.25);
 f.sockets[0].receive({type:'move',player:f.peer({floor:'rooftop',x:2200,y:1700})});
 assert.equal(f.presence.peers.get('peer').x,2200);assert.deepEqual(f.element('presenceStatus').children.slice(1).map(child=>child.textContent),['접속 중','2','명']);
 f.sockets[0].receive({type:'control_state',state:{main:f.peer({id:'self',x:1400,y:600,dir:'right'})}});
 assert.equal(f.player.targetX,1400);assert.equal(f.player.dir,'right');f.presence.close();
});
test('batched moves update known people only, so a late batch cannot bring back someone who left',()=>{
 const f=app({floors});f.welcome();
 f.sockets[0].receive({type:'moves',players:[f.peer({x:1230.25,moving:true}),f.peer({id:'self',x:5}),f.peer({id:'stranger',x:900})]});f.tick(116);
 assert.ok(f.presence.peers.get('peer').x>1200&&f.presence.peers.get('peer').moving);assert.equal(f.presence.peers.has('stranger'),false);
 f.sockets[0].receive({type:'leave',id:'peer'});f.sockets[0].receive({type:'moves',players:[f.peer({x:1240})]});
 assert.equal(f.presence.peers.has('peer'),false);assert.deepEqual(f.element('presenceStatus').children.slice(1).map(child=>child.textContent),['접속 중','1','명']);f.presence.close();
});
test('compact moves retain identity, accepted fractions, final stops and classroom/floor changes',()=>{
 const f=app({floors});f.welcome();const socket=f.sockets[0];
 const move={id:'peer',floor:'campus',x:1230.125,y:440.875,dir:'right',moving:true,inClassroom:false};
 socket.receive({type:'moves',players:[move]});
 let peer=f.presence.peers.get('peer');
 assert.equal(peer.targetX,1230.125);assert.equal(peer.targetY,440.875);assert.equal(peer.moving,true);
 assert.equal(peer.nickname,'조원 이름');assert.equal(peer.avatar,'female-2');assert.equal(peer.color,'#4e9b91');
 socket.receive({type:'moves',players:[{...move,floor:'rooftop',x:2200.125,y:1700.875,moving:false,inClassroom:true}]});
 assert.equal(peer.x,2200.125);assert.equal(peer.y,1700.875);assert.equal(peer.moving,false);
 assert.equal(peer.floor,'rooftop');assert.equal(peer.inClassroom,true);
 socket.receive({type:'move',player:f.peer({nickname:'새 이름',avatar:'male-2',color:'#8a78bb'})});
 socket.receive({type:'moves',players:[{...move,x:1231,moving:false}]});
 assert.equal(peer.nickname,'새 이름');assert.equal(peer.avatar,'male-2');assert.equal(peer.color,'#8a78bb');
 assert.equal(f.element('presencePeers').children[0].textContent,'새 이름');f.presence.close();
});
test('partial moves cannot create peers or bypass full-state and coordinate validation',()=>{
 const f=app({floors});f.welcome();const socket=f.sockets[0];
 const move={id:'peer',floor:'campus',x:1230,y:440,dir:'right',moving:true,inClassroom:false};
 socket.receive({type:'join',player:{...move,id:'partial-join'}});
 socket.receive({type:'move',player:f.peer({id:'late-move'})});
 socket.receive({type:'moves',players:[{...move,id:'unknown'},{...move,id:'self'}]});
 assert.equal(f.presence.peers.size,1);assert.equal(f.player.x,1200);
 for(const bad of [{x:-1},{x:Infinity},{floor:'unknown'},{floor:undefined},{moving:'yes'},{dir:'sideways'},
   {inClassroom:'yes'},{inClassroom:undefined},{nickname:null},{avatar:'unknown'},{color:'#000000'}]){
   socket.receive({type:'moves',players:[{...move,...bad}]});
   assert.equal(f.presence.peers.get('peer').targetX,1200);
 }
 const {moving,...incomplete}=move;socket.receive({type:'moves',players:[incomplete]});
 assert.equal(f.presence.peers.get('peer').targetX,1200);
 socket.receive({type:'leave',id:'peer'});socket.receive({type:'moves',players:[move]});
 socket.receive({type:'move',player:f.peer()});assert.equal(f.presence.peers.size,0);f.presence.close();
});
test('reconnection restores full identities before compact updates resume',()=>{
 const f=app({floors});f.welcome();f.sockets[0].close(1006);
 [...f.timers.values()][0].callback();const socket=f.sockets.at(-1);
 socket.receive({type:'welcome',selfId:'self',players:[f.peer({id:'self'}),f.peer({nickname:'복구 이름',avatar:'male-1',x:1500})]});
 socket.receive({type:'moves',players:[{id:'peer',floor:'campus',x:1501.5,y:437,dir:'left',moving:false,inClassroom:false}]});
 const peer=f.presence.peers.get('peer');assert.equal(peer.nickname,'복구 이름');assert.equal(peer.avatar,'male-1');
 assert.equal(peer.targetX,1501.5);f.presence.close();
});
test('the same avatar and nickname do not redraw the avatar panel; a real change does once',()=>{
 const f=app({floors});f.welcome();const avatar=()=>f.events.filter(type=>type==='campus:avatar').length;const first=avatar();
 for(let i=0;i<5;i++)f.sockets[0].receive({type:'control_state',state:{main:f.peer({id:'self',nickname:'내 이름',x:1400+i})}});
 assert.equal(avatar(),first);
 f.sockets[0].receive({type:'control_state',state:{main:f.peer({id:'self',nickname:'내 이름',avatar:'male-1'})}});
 assert.equal(avatar(),first+1);f.presence.close();
});
test('chat validation stays routed to the server; door messages are gone and control errors still reach the toast',()=>{
 const f=app({floors});f.welcome();assert.equal(f.presence.sendChat('가'.repeat(201)),false);assert.equal(f.presence.sendChat('   '),false);
 assert.equal(f.presence.sendChat('  안녕  '),true);assert.deepEqual(plain(f.sockets[0].sent.at(-1)),{type:'chat',text:'안녕'});
 const message={id:'m1',playerId:'peer',nickname:'조원',floor:'rooftop',text:'안녕',sentAt:'2026-10-13T01:00:00Z'};
 f.sockets[0].receive({type:'chat',message});f.sockets[0].receive({type:'chat',message:{...message,floor:'unknown'}});assert.equal(f.chats.length,1);
 assert.equal('requestDoor' in f.presence,false);f.sockets[0].receive({type:'door_state',doorId:'door-storage',open:true});  // 문은 없앴다(계획 M1)
 f.sockets[0].receive({type:'control_error',message:'잠시 후 다시 시도해 주세요.'});assert.deepEqual(f.controlErrors,['잠시 후 다시 시도해 주세요.']);
 f.presence.close();
});
test('control results match request IDs and disconnect rejects pending actions',async()=>{
 const f=app({floors});f.welcome();const result=f.presence.command('open_booth',{objectId:'roof-booth-3'});const packet=f.sockets[0].sent.at(-1);
 f.sockets[0].receive({type:'control_result',requestId:packet.requestId,result:{type:'booth_open'}});assert.equal((await result).type,'booth_open');
 const pending=f.presence.command('interact');f.presence.close();await assert.rejects(pending,/연결/);assert.equal(f.timers.size,0);
});
test('unexpected disconnection reconnects once and expired authentication cannot retry',()=>{
 const f=app({floors});f.welcome();f.sockets[0].close(1006);assert.equal(f.timers.size,1);[...f.timers.values()][0].callback();assert.equal(f.sockets.length,2);f.presence.close();
 const expired=app();expired.welcome();expired.sockets[0].close(4401);assert.equal(expired.expired,1);assert.equal(expired.timers.size,0);
});
test('reconnect delays are spread per person around the doubling backoff',()=>{
 const f=app({floors});f.welcome();const delays=[];
 for(let i=0;i<5;i++){f.sockets.at(-1).close(1006);const timer=[...f.timers.values()].at(-1);delays.push(timer.delay);f.timers.clear();timer.callback();}
 [1000,2000,4000,8000,10000].forEach((base,i)=>assert.ok(delays[i]>=base*.5&&delays[i]<=base*1.5,String(delays)));
 assert.notDeepEqual(delays,[1000,2000,4000,8000,10000]);f.presence.close();  // 모두가 같은 순간에 다시 오지 않는다
});
test('a 4401 is checked against the session first: alive reconnects, expired goes to login, a repeat does not loop',async()=>{
 const flush=()=>new Promise(resolve=>setImmediate(resolve));
 let verified=0;const alive=app({floors,session:{verify:async()=>{verified++;return true;},expire(){},receive(){}}});alive.welcome();
 alive.sockets[0].close(4401);await flush();assert.equal(alive.expired,0);assert.equal(alive.timers.size,1);
 [...alive.timers.values()][0].callback();assert.equal(alive.sockets.length,2);
 alive.sockets[1].close(4401);await flush();assert.equal(verified,1);assert.equal(alive.expired,1);  // 다시 붙기 전에 또 4401: 확인 없이 끝낸다
 const gone=app({floors,session:{verify:async()=>false,expire(){},receive(){}}});gone.welcome();
 gone.sockets[0].close(4401);await flush();assert.equal(gone.expired,1);assert.equal(gone.timers.size,0);
});

test('jumps and pokes riding the 0.1s moves batch reach the same handlers as single messages',()=>{
 const f=app();f.welcome();const socket=f.sockets[0];
 socket.receive({type:'moves',players:[f.peer({x:1210,moving:true})],effects:[
  {type:'jump',id:'j1',actorId:'peer',floor:'campus',context:'world'},{type:'poke_state',id:'p1',actorId:'peer',active:true},{type:'chat'},null]});
 assert.deepEqual(plain(f.jumps).map(row=>row.id),['j1']);
 assert.deepEqual(plain(f.pokes).map(row=>row.id),['p1']);
 assert.equal(f.presence.peers.get('peer').targetX,1210);
 socket.receive({type:'jump',id:'j2',actorId:'peer',floor:'campus',context:'world'});  // 따로 오는 것도 그대로 받는다
 socket.receive({type:'moves',players:[]});
 assert.deepEqual(plain(f.jumps).map(row=>row.id),['j1','j2']);
 f.presence.close();
});
