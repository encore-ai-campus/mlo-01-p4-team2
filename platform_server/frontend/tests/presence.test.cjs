const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');

function app({authenticated = true, secure = false, floors, restore = false, session, freeze} = {}) {
  const elements = new Map(), sockets = [], timers = new Map(), intervals = new Map(), listeners = new Map();
  let clock = 0, nextTimer = 0, redirected = null, expired = 0, logoutPosts = 0;
  const chats = [], chatErrors = [], connectionChanges = [], restores = [], controlErrors = [], jumps = [], pokes = [];
  const logoutChanges = [];
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
      submit() {if(id==='campusLogoutForm')logoutPosts++;},
    });
    return elements.get(id);
  }
  if (authenticated) element('campusPresenceConfig').textContent = JSON.stringify({movementSocketPath: '/ws/movement/', chatSocketPath:'/ws/chat/', loginUrl: '/accounts/login/'});
  class Socket {
    static OPEN = 1; static CLOSED = 3;
    constructor(url) {this.url = url; this.readyState = 1; this.sent = []; sockets.push(this);}
    send(text) {this.sent.push(JSON.parse(text));}
    close(code = 1000) {this.readyState = 3; this.onclose?.({code});}
    receive(data) {this.onmessage({data: JSON.stringify(data)});}
  }
  const protocol = secure ? 'https:' : 'http:';
  const context = vm.createContext({URL, Event, CustomEvent:class{constructor(type,options){this.type=type;this.detail=options.detail;}}, WebSocket: Socket, performance: {now: () => clock},
    window: {location: {protocol, href: protocol + '//127.0.0.1:8000/', assign(url) {redirected = url;}},
      addEventListener(name, callback) {listeners.set('window:' + name, callback);}, dispatchEvent(event) {events.push(event.type);listeners.get('window:'+event.type)?.(event);}, CampusSession: session, CampusFreeze: freeze},
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
    onControlError(message) {controlErrors.push(message);}, onLogoutPending(value) {logoutChanges.push(value);}, onJump(message) {jumps.push(message);}, onPoke(message) {pokes.push(message);}});
  const peer = (changes = {}) => ({id: 'peer', nickname: '조원 이름', avatar: 'female-2', x: 1200, y: 437,
    dir: 'down', color: '#4e9b91', moving: false, ...changes});
  const movement = () => sockets.findLast(socket=>new URL(socket.url).pathname==='/ws/movement/');
  const chat = () => sockets.findLast(socket=>new URL(socket.url).pathname==='/ws/chat/');
  function welcome(socket = movement(), {selfId='self',token='token-campus',readyChat=true} = {}) {
    socket.receive({type: 'welcome', selfId, chatToken:token, players: [peer({id: selfId, nickname: '내 이름'}), peer()]});
    if(readyChat)chat().receive({type:'chat_ready'});
  }
  return {presence, player, sockets, timers, intervals, listeners, element, peer, welcome, movement, chat, events, chats, chatErrors, connectionChanges, restores, controlErrors, jumps, pokes, logoutChanges,
    submitLogout() {let prevented=false;const result=listeners.get('campusLogoutForm:submit')({preventDefault(){prevented=true;}});assert.equal(prevented,true);return result;},
    get logoutPosts() {return logoutPosts;},
    tick(t, dt = .016) {clock = t; presence.tick(t, dt);},
    get redirected() {return redirected;}, get expired() {return expired;}};
}

const test = require('node:test');
const floors={campus:{width:2448,height:1230,objects:[]},rooftop:{width:2448,height:1800}};
const plain=value=>JSON.parse(JSON.stringify(value));
test('personal achievement packets preserve server state and dispatch no unready or malformed updates',()=>{
 const f=app({floors}),updates=[];f.listeners.set('window:campus:achievements',event=>updates.push(event.detail));
 const achievements=[{event_uuid:'greeting',name:'수상한 친화력',achieved:true,completed_at:'2026-11-03T00:00:00Z',image_url:'/static/campus/assets/achievements/manager-greeting.png'}];
 f.movement().receive({type:'achievement_update',achievements});assert.equal(updates.length,0);
 f.welcome();f.movement().receive({type:'achievement_update',achievements});
 assert.deepEqual(plain(updates),[achievements]);
 f.movement().receive({type:'achievement_update',achievements:{}});assert.equal(updates.length,1);f.presence.close();
});
test('legacy global discovery packets do not publish another account records to the notebook',()=>{
 const f=app({floors});f.welcome();
 f.movement().receive({type:'achievement_discoveries',discoveries:[{event_uuid:'greeting',discovered:true,first_discoverer:{nickname:'다른 계정'}}]});
 assert.ok(!f.events.includes('campus:achievement-discoveries'));assert.ok(!f.events.includes('campus:achievements'));
 f.presence.close();
});
test('standalone creates no socket; authenticated connections negotiate server control and WSS',()=>{
 const offline=app({authenticated:false});offline.tick(200);assert.equal(offline.sockets.length,0);
 for(const secure of [false,true]){const f=app({secure});assert.equal(f.sockets[0].url,`${secure?'wss':'ws'}://127.0.0.1:8000/ws/movement/?control=server&moves=compact&view=nearby`);assert.equal(f.sockets.length,1);f.welcome();assert.equal(f.chat().url,`${secure?'wss':'ws'}://127.0.0.1:8000/ws/chat/`);f.presence.close();}
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
 assert.equal(f.presence.sendChat('  안녕  '),true);assert.deepEqual(plain(f.chat().sent.at(-1)),{type:'chat',text:'안녕',actorToken:'token-campus'});
 const message={id:'m1',playerId:'peer',nickname:'조원',floor:'rooftop',text:'안녕',sentAt:'2026-10-13T01:00:00Z'};
 f.chat().receive({type:'chat',message});f.chat().receive({type:'chat',message:{...message,floor:'unknown'}});assert.equal(f.chats.length,1);
 assert.equal('requestDoor' in f.presence,false);f.sockets[0].receive({type:'door_state',doorId:'door-storage',open:true});  // 문은 없앴다(계획 M1)
 f.sockets[0].receive({type:'control_error',message:'잠시 후 다시 시도해 주세요.'});assert.deepEqual(f.controlErrors,['잠시 후 다시 시도해 주세요.']);
 f.presence.close();
});
test('control results match request IDs and disconnect rejects pending actions',async()=>{
 const f=app({floors});f.welcome();const result=f.presence.command('open_booth',{objectId:'roof-booth-3'});const packet=f.sockets[0].sent.at(-1);
 f.sockets[0].receive({type:'control_result',requestId:packet.requestId,result:{type:'booth_open'}});assert.equal((await result).type,'booth_open');
 const pending=f.presence.command('interact');f.presence.close();await assert.rejects(pending,/연결/);assert.equal(f.timers.size,0);
});
test('logout waits for saved acknowledgement and sends the HTTP form once',async()=>{
 const f=app({floors});f.welcome();f.presence.setInput(['w']);
 const submitted=f.submitLogout(),packet=f.movement().sent.at(-1);
 assert.equal(packet.action,'prepare_logout');assert.equal(f.logoutPosts,0);assert.equal(f.presence.connected,true);
 assert.deepEqual(plain(f.presence.inputKeys),[]);assert.deepEqual(f.logoutChanges,[true]);
 await f.submitLogout();assert.equal(f.movement().sent.filter(message=>message.action==='prepare_logout').length,1);
 f.movement().receive({type:'control_result',requestId:packet.requestId,result:{saved:true}});
 await submitted;assert.equal(f.logoutPosts,1);assert.equal(f.presence.connected,false);
 await f.submitLogout();assert.equal(f.logoutPosts,1);
});
test('logout errors and missing save confirmation keep the session open for retry',async()=>{
 for(const response of [{error:'위치 저장에 실패했어요.'},{result:{saved:false}}]){
  const f=app({floors});f.welcome();const submitted=f.submitLogout(),packet=f.movement().sent.at(-1);
  f.movement().receive({type:'control_result',requestId:packet.requestId,...response});await submitted;
  assert.equal(f.logoutPosts,0);assert.equal(f.presence.connected,true);assert.deepEqual(f.logoutChanges,[true,false]);assert.equal(f.controlErrors.length,1);
  const retry=f.submitLogout(),next=f.movement().sent.at(-1);
  f.movement().receive({type:'control_result',requestId:next.requestId,result:{saved:true}});await retry;assert.equal(f.logoutPosts,1);
 }
});
test('logout timeout ignores a late acknowledgement and permits a new request',async()=>{
 const f=app({floors});f.welcome();const submitted=f.submitLogout(),packet=f.movement().sent.at(-1);
 const [id,timer]=[...f.timers].find(([,timer])=>timer.delay===10000);f.timers.delete(id);timer.callback();await submitted;
 assert.equal(f.logoutPosts,0);assert.match(f.controlErrors[0],/다시 시도/);assert.deepEqual(f.logoutChanges,[true,false]);
 const retry=f.submitLogout(),next=f.movement().sent.at(-1);
 f.movement().receive({type:'control_result',requestId:packet.requestId,result:{saved:true}});assert.equal(f.logoutPosts,0);
 f.movement().receive({type:'control_result',requestId:next.requestId,result:{saved:true}});await retry;assert.equal(f.logoutPosts,1);
});
test('logout without a movement connection sends no HTTP and can retry after connection',async()=>{
 const f=app({floors});await f.submitLogout();assert.equal(f.logoutPosts,0);assert.match(f.controlErrors[0],/연결/);
 f.welcome();const retry=f.submitLogout(),packet=f.movement().sent.at(-1);
 f.movement().receive({type:'control_result',requestId:packet.requestId,result:{saved:true}});await retry;assert.equal(f.logoutPosts,1);
});
test('unexpected disconnection reconnects once and expired authentication cannot retry',()=>{
 const f=app({floors});f.welcome();f.sockets[0].close(1006);assert.equal(f.timers.size,1);[...f.timers.values()][0].callback();assert.equal(f.sockets.length,3);f.presence.close();
 const expired=app();expired.welcome();expired.sockets[0].close(4401);assert.equal(expired.expired,1);assert.equal(expired.timers.size,0);
});
test('reconnect delays are spread per person around the doubling backoff',()=>{
 const f=app({floors});f.welcome();const delays=[];
 for(let i=0;i<5;i++){f.movement().close(1006);const timer=[...f.timers.values()].at(-1);delays.push(timer.delay);f.timers.clear();timer.callback();}
 [1000,2000,4000,8000,10000].forEach((base,i)=>assert.ok(delays[i]>=base*.5&&delays[i]<=base*1.5,String(delays)));
 assert.notDeepEqual(delays,[1000,2000,4000,8000,10000]);f.presence.close();  // 모두가 같은 순간에 다시 오지 않는다
});
test('a 4401 is checked against the session first: alive reconnects, expired goes to login, a repeat does not loop',async()=>{
 const flush=()=>new Promise(resolve=>setImmediate(resolve));
 let verified=0;const alive=app({floors,session:{verify:async()=>{verified++;return true;},expire(){},receive(){}}});alive.welcome();
 alive.sockets[0].close(4401);await flush();assert.equal(alive.expired,0);assert.equal(alive.timers.size,1);
 [...alive.timers.values()][0].callback();assert.equal(alive.sockets.length,3);
 alive.movement().close(4401);await flush();assert.equal(verified,1);assert.equal(alive.expired,1);  // 다시 붙기 전에 또 4401: 확인 없이 끝낸다
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

test('chat waits for its ready message and the current movement actor token',()=>{
 const f=app({floors});assert.equal(f.presence.sendChat('hello'),false);
 assert.equal(f.sockets.length,1);f.listeners.get('window:online')();assert.equal(f.sockets.length,2);
 assert.equal(f.chat(),undefined);
 f.welcome(f.movement(),{readyChat:false});assert.equal(f.presence.sendChat('hello'),false);
 for(const invalid of [null,1,'bad']){f.movement().receive(invalid);f.chat().receive(invalid);}
 f.chat().receive({type:'chat_ready'});assert.equal(f.presence.sendChat('hello'),true);
 assert.deepEqual(plain(f.chat().sent.at(-1)),{type:'chat',text:'hello',actorToken:'token-campus'});
 assert.equal(f.movement().sent.some(packet=>packet.type==='chat'),false);
 assert.equal(f.connectionChanges.at(-1),true);f.presence.close();
 const missing=app({floors});
 missing.movement().receive({type:'welcome',selfId:'self',players:[missing.peer({id:'self'})]});
 missing.chat().receive({type:'chat_ready'});
 assert.equal(missing.presence.sendChat('hello'),false);missing.presence.close();
});

test('floor changes rotate the token without reconnecting chat and foreign actors cannot replace it',()=>{
 const f=app({floors});f.welcome();const chat=f.chat();
 f.movement().receive({type:'control_state',state:{main:f.peer({id:'self',x:1210})}});
 f.presence.sendChat('same floor');assert.equal(chat.sent.at(-1).actorToken,'token-campus');
 f.movement().receive({type:'control_state',chatToken:'foreign-token',state:{main:f.peer({id:'other',floor:'rooftop'})}});
 f.presence.sendChat('same actor');assert.equal(chat.sent.at(-1).actorToken,'token-campus');
 f.movement().receive({type:'control_state',chatToken:'token-roof',state:{main:f.peer({id:'self',floor:'rooftop',x:2200,y:1700})}});
 assert.equal(f.presence.sendChat('roof'),true);assert.equal(chat.sent.at(-1).actorToken,'token-roof');
 assert.equal(f.chat(),chat);assert.equal(f.sockets.length,2);
 f.movement().receive({type:'control_state',state:{main:f.peer({id:'self',floor:'campus'})}});
 assert.equal(f.presence.sendChat('missing floor token'),false);f.presence.close();
});

test('movement reconnect replaces actor and token while late old messages cannot overwrite them',()=>{
 const f=app({floors});f.welcome();const old=f.movement(),chat=f.chat();
 old.close(1006);assert.equal(f.presence.sendChat('during reconnect'),false);
 [...f.timers.values()][0].callback();const current=f.movement();
 f.welcome(current,{selfId:'new-self',token:'fresh-token',readyChat:false});
 old.receive({type:'welcome',selfId:'self',chatToken:'old-token',players:[f.peer({id:'self'})]});
 old.receive({type:'control_state',chatToken:'old-token',state:{main:f.peer({id:'new-self'})}});
 old.onclose({code:4401});
 assert.equal(f.presence.selfId,'new-self');assert.equal(f.chat(),chat);
 assert.equal(f.presence.sendChat('fresh'),true);assert.equal(chat.sent.at(-1).actorToken,'fresh-token');
 assert.equal(f.expired,0);f.presence.close();
});

test('chat reconnect leaves movement active and rejects callbacks from its old connection',()=>{
 const f=app({floors});f.welcome();const movement=f.movement(),old=f.chat();
 old.close(1006);assert.equal(f.presence.connected,true);assert.equal(f.presence.sendChat('wait'),false);
 [...f.timers.values()][0].callback();const current=f.chat();
 assert.equal(f.movement(),movement);assert.equal(f.presence.sendChat('wait'),false);
 current.receive({type:'chat_ready'});
 old.receive({type:'chat_ready'});old.receive({type:'chat_error',message:'old error'});old.onclose({code:4401});
 assert.equal(f.presence.sendChat('ready'),true);assert.equal(current.sent.at(-1).actorToken,'token-campus');
 assert.deepEqual(f.chatErrors,[]);assert.equal(f.expired,0);f.presence.close();
});

test('chat alone receives messages and private notifications; expiry stops both connections',()=>{
 const f=app({floors,session:{receive(){},expire(){}}});f.welcome();
 const message={id:'m1',playerId:'peer',nickname:'조원',floor:'campus',text:'안녕',sentAt:'2026-10-13T01:00:00Z'};
 f.movement().receive({type:'chat',message});f.movement().receive({type:'private_message',conversationId:'old'});
 assert.equal(f.chats.length,0);assert.equal(f.events.includes('campus:private-message'),false);
 f.chat().receive({type:'chat',message});f.chat().receive({type:'private_message',conversationId:'current'});
 f.chat().receive({type:'chat_error',message:'채팅 오류'});
 assert.equal(f.chats.length,1);assert.equal(f.events.includes('campus:private-message'),true);
 assert.deepEqual(f.chatErrors,['채팅 오류']);
 f.chat().receive({type:'session_expired',reason:'replaced'});
 assert.equal(f.movement().readyState,3);assert.equal(f.chat().readyState,3);
 assert.equal(f.timers.size,0);assert.equal(f.intervals.size,0);assert.equal(f.presence.connected,false);
});

test('page lifecycle closes and restores both transports with no stale token',()=>{
 const f=app({floors});f.welcome();const oldMovement=f.movement(),oldChat=f.chat();
 f.listeners.get('window:pagehide')();assert.equal(f.intervals.size,0);
 f.listeners.get('window:pageshow')({persisted:true});
 oldMovement.receive({type:'welcome',selfId:'old',chatToken:'stale',players:[f.peer({id:'old'})]});
 oldChat.receive({type:'chat_ready'});assert.equal(f.presence.sendChat('wait'),false);
 f.welcome(f.movement(),{token:'restored-token'});
 assert.equal(f.presence.sendChat('restored'),true);assert.equal(f.chat().sent.at(-1).actorToken,'restored-token');f.presence.close();
});

test('chat 4401 checks the session once and cannot close a newer chat connection',async()=>{
 const flush=()=>new Promise(resolve=>setImmediate(resolve));let resolveVerification;
 const f=app({floors,session:{verify:()=>new Promise(resolve=>{resolveVerification=resolve;}),expire(){},receive(){}}});
 f.welcome();const old=f.chat();old.close(4401);
 f.listeners.get('window:online')();const fresh=f.chat();
 resolveVerification(false);await flush();assert.equal(f.expired,0);assert.notEqual(fresh,old);
 fresh.receive({type:'chat_ready'});assert.equal(f.presence.sendChat('fresh'),true);f.presence.close();
});


test('nearby visibility preserves the global roster and restores full pose without sliding or stale effects',()=>{
 const f=app({floors});f.welcome();const socket=f.movement(),peer=f.presence.peers.get('peer');
 socket.receive({type:'moves',players:[f.peer({inView:true,guarding:true,running:false,peperoRemaining:4})]});
 peer._campusPokeLoop={id:'old'};peer._peperoBreak={started:0};
 f.tick(1000);assert.equal(peer.guarding,true); // No repeated broadcast needed for held guard.
 socket.receive({type:'moves',players:[],hidden:['peer']});
 assert.equal(peer.inView,false);assert.equal(peer.guarding,false);
 assert.equal(peer._campusPokeLoop,undefined);assert.equal(peer._peperoBreak,undefined);
 assert.equal(f.presence.peers.size,1);assert.equal(f.element('presenceStatus').children[2].textContent,'2');
 socket.receive({type:'moves',players:[f.peer({x:1250,y:450,inView:true,guarding:true,peperoRemaining:2})]});
 assert.equal(peer.inView,true);assert.equal(peer.x,1250);assert.equal(peer.y,450);
 assert.equal(peer.guarding,true);assert.equal(peer.peperoRemaining,2);
 socket.receive({type:'moves',players:[{id:'peer',floor:'campus',inClassroom:false,x:1250,y:450,dir:'down',moving:false,guarding:false,inView:true}]});
 assert.equal(peer.guarding,false);f.presence.close();
});

test('a distant identity change updates the roster without revealing position or showing the actor',()=>{
 const f=app({floors});f.welcome();const peer=f.presence.peers.get('peer');
 f.movement().receive({type:'moves',players:[],hidden:['peer']});
 f.movement().receive({type:'peer_identity',player:{id:'peer',nickname:'새 닉네임',avatar:'male-1',color:'#dfa34d',x:999,y:999}});
 assert.equal(peer.nickname,'새 닉네임');assert.equal(peer.x,1200);assert.equal(peer.inView,false);
 assert.equal(f.element('presencePeers').children[0].textContent,'새 닉네임');f.presence.close();
});


test('freeze revision accompanies movement and paused clients block input and commands',async()=>{
 const freeze={blocking:false,revision:7};const f=app({floors,freeze});f.welcome();
 f.presence.setInput(['d']);assert.deepEqual(plain(f.movement().sent.at(-1)),{type:'input',freezeRevision:7,keys:['d']});
 freeze.blocking=true;freeze.revision=8;f.presence.setInput(['d']);
 assert.deepEqual(plain(f.movement().sent.at(-1)),{type:'input',freezeRevision:8,keys:[]});
 const count=f.movement().sent.length;await assert.rejects(f.presence.command('quick_travel'),/일시정지/);assert.equal(f.movement().sent.length,count);
 freeze.blocking=false;freeze.revision=9;f.presence.setInput(['d']);
 assert.deepEqual(plain(f.movement().sent.at(-1)),{type:'input',freezeRevision:9,keys:['d']});f.presence.close();
});
