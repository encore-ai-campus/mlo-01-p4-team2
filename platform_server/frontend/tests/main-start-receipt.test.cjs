const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {webcrypto}=require('node:crypto');
const source=fs.readFileSync(path.join(__dirname,'../dist/main-quest.js'),'utf8');
const plain=value=>JSON.parse(JSON.stringify(value));
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function part(start,end){const a=source.indexOf(start),b=source.indexOf(end,a);assert.ok(a>=0&&b>a);return source.slice(a,b);}
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
function fixture(api){
 const calls=[],screens=[],opened=[],toasts=[];let nextUuid=0;
 const scope={state:{stage:1},stateSequence:0,lastRefresh:0,pendingStarts:new Map(),
  crypto:{randomUUID:()=>'00000000-0000-4000-8000-'+String(++nextUuid).padStart(12,'0')},
  window:{CampusApp:{toast:message=>toasts.push(message)}},
  renderTracker:()=>calls.push(['render']),
  ticket:async target=>{calls.push(['ticket',target]);return 'renewed-proof';},
  dialog:{open:false,close(){this.open=false;calls.push(['close']);}},
  info:(...args)=>{screens.push(args);calls.push(['info',args[0]]);},
  api:async data=>{calls.push([data?'post':'get',plain(data||{})]);return api(data);},
  open:play=>{opened.push(play);calls.push(['open',play.play]);}};
 vm.createContext(scope);
 vm.runInContext(part('async function refresh()','async function ticket('),scope);
 return {scope,calls,screens,opened,toasts};
}
const receipt={play:'play-B',token:'original-token',challenge:{seed:42},practice:false,status:'RUNNING'};

test('confirmed start opens immediately while the separate status GET is pending',{timeout:1000},async()=>{
 const read=deferred(),f=fixture(data=>data?receipt:read.promise);
 await f.scope.beginStart('sql_memory',false,'room1',f.scope.open,'initial-proof');
 assert.deepEqual(f.calls.map(call=>call[0]),['post','open','get']);
 assert.equal(f.opened[0],receipt);assert.equal(f.scope.pendingStarts.size,0);
 assert.equal(f.scope.state.stage,1);
 read.resolve({stage:2});await tick();assert.equal(f.scope.state.stage,2);
});

test('a failed background GET preserves successful start and the existing mission state',async()=>{
 const f=fixture(data=>data?receipt:Promise.reject(new Error('status unavailable')));
 await f.scope.beginStart('sql_memory',false,'room1',f.scope.open,'initial-proof');await tick();
 assert.equal(f.opened.length,1);assert.equal(f.screens.length,0);
 assert.equal(f.scope.state.stage,1);assert.equal(f.toasts.length,1);
 assert.match(f.toasts[0],/저장은 완료/);
});

test('explicit retry retains game mode and request UUID after a lost start response',async()=>{
 let lost=true;
 const f=fixture(data=>{if(!data)return {stage:2};if(lost){lost=false;throw new Error('lost response');}return receipt;});
 await f.scope.beginStart('sql_memory',false,'room1',f.scope.open,'initial-proof');
 assert.equal(f.opened.length,0);assert.equal(f.scope.pendingStarts.size,1);
 await f.screens.at(-1)[2][0][1]();
 const posts=f.calls.filter(call=>call[0]==='post').map(call=>call[1]);
 assert.equal(posts.length,2);
 assert.deepEqual({...posts[0],ticket:undefined},{...posts[1],ticket:undefined});
 assert.equal(posts[1].ticket,'renewed-proof');
 assert.equal(f.opened[0],receipt);assert.equal(f.scope.pendingStarts.size,0);
});

test('reopening an unresolved start sends no additional request until the user chooses',async()=>{
 const f=fixture(()=>Promise.reject(new Error('lost response')));
 await f.scope.beginStart('sql_memory',false,'room1',f.scope.open,'proof');
 await f.scope.beginStart('sql_memory',false,'room1',f.scope.open,'proof');
 assert.equal(f.calls.filter(call=>call[0]==='post').length,1);
 assert.equal(f.scope.pendingStarts.size,1);
 assert.match(f.screens.at(-1)[1],/같은 요청을 다시 확인/);
});

test('choosing a new attempt assigns a different UUID after an uncertain start',async()=>{
 let lost=true;
 const f=fixture(data=>{if(!data)return {stage:2};if(lost){lost=false;throw new Error('lost response');}return receipt;});
 await f.scope.beginStart('sql_memory',false,'room1',f.scope.open,'proof');
 await f.screens.at(-1)[2][1][1]();
 const posts=f.calls.filter(call=>call[0]==='post').map(call=>call[1]);
 assert.equal(posts.length,2);assert.notEqual(posts[0].request_uuid,posts[1].request_uuid);
 assert.equal(f.opened.length,1);
});

test('terminal receipts never run a game and a deliberate new game gets a new UUID',async()=>{
 for(const status of ['INTERRUPTED','FINISHED','SUCCESS','FAILED']){
  let first=true;
  const f=fixture(data=>{if(!data)return {stage:2};if(first){first=false;return {...receipt,status};}return receipt;});
  await f.scope.beginStart('sql_memory',false,'room1',f.scope.open,'proof');
  assert.equal(f.opened.length,0);assert.match(f.screens.at(-1)[0],/이미 종료/);
  await f.screens.at(-1)[2][0][1]();
  const posts=f.calls.filter(call=>call[0]==='post').map(call=>call[1]);
  assert.notEqual(posts[0].request_uuid,posts[1].request_uuid);assert.equal(f.opened.length,1);
 }
});

test('UUID generation works on private LAN HTTP without crypto.randomUUID',()=>{
 const f=fixture(()=>receipt);
 f.scope.crypto={getRandomValues:bytes=>webcrypto.getRandomValues(bytes)};
 const first=f.scope.newStartUuid(),second=f.scope.newStartUuid();
 assert.match(first,/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
 assert.notEqual(first,second);
});

test('older GET responses cannot replace state supplied by a later action',async()=>{
 const read=deferred(),f=fixture(data=>data?{state:{stage:4}}:read.promise);
 const pending=f.scope.refresh();
 await f.scope.act('game_finish',{play:'B'});
 read.resolve({stage:1});await pending;
 assert.equal(f.scope.state.stage,4);
});

test('out-of-order GET responses preserve the latest requested mission snapshot',async()=>{
 const reads=[deferred(),deferred()];let index=0;
 const f=fixture(()=>reads[index++].promise);
 const first=f.scope.refresh(),second=f.scope.refresh();
 reads[1].resolve({stage:3});await second;
 reads[0].resolve({stage:1});await first;
 assert.equal(f.scope.state.stage,3);
});

test('investigation shows popup before ticket and displays success before a slow status GET',{timeout:1000},async()=>{
 const read=deferred(),f=fixture(data=>data?{first:true,correct:true}:read.promise);
 f.scope.story=(...args)=>{f.screens.push(args);f.calls.push(['story',args[0]]);};
 f.scope.kline=f.scope.pline=text=>({text});f.scope.guidance=()=> 'saved guidance';
 vm.runInContext(part('async function investigate()','async function confession()'),f.scope);
 await f.scope.investigate();
 assert.deepEqual(f.calls.map(call=>call[0]),['story']);
 await f.screens[0][3][0][1]();
 assert.deepEqual(f.calls.map(call=>call[0]),['story','ticket','post','story','get']);
 assert.equal(f.screens[1][0],'조사를 시작합니다');
 assert.deepEqual(f.calls.find(call=>call[0]==='post')[1],{action:'investigate',ticket:'renewed-proof'});
 read.resolve({stage:2});await tick();
});

test('investigation success remains visible when the subsequent status GET fails',async()=>{
 const f=fixture(data=>data?{first:true,correct:true}:Promise.reject(new Error('status unavailable')));
 f.scope.story=(...args)=>f.screens.push(args);f.scope.kline=f.scope.pline=text=>({text});f.scope.guidance=()=> 'saved guidance';
 vm.runInContext(part('async function investigate()','async function confession()'),f.scope);
 await f.scope.investigate();await f.screens[0][3][0][1]();await tick();
 assert.equal(f.screens.at(-1)[0],'조사를 시작합니다');assert.equal(f.toasts.length,1);
});

test('station start and touch challenge both use the shared retryable start path',async()=>{
 const starts=[],scope={held:{clear(){}},refresh:async()=>{},room:1,roomTicket:'room-proof',
  state:{games:{},locked:{},room1Briefed:true,steps:[{complete:true},{},{complete:true}]},
  practiceLabel:{querySelector:()=>({checked:false})},window:{CampusApp:{presence:{setInput(){}}}},
  beginStart:(...args)=>{starts.push(args);return 'queued';},openTouch:()=>{}};
 vm.createContext(scope);
 vm.runInContext(part('async function interactStation(','async function finish('),scope);
 vm.runInContext(part('function startTouch(','function openTouch('),scope);
 assert.equal(await scope.interactStation({code:'sql_memory',name:'SQL'}),'queued');
 assert.deepEqual(starts[0].slice(0,3),['sql_memory',false,'room1']);
 assert.equal(starts[0][4],'room-proof');assert.equal(typeof starts[0][3],'function');
 assert.equal(scope.startTouch('guide-proof'),'queued');
 assert.deepEqual(starts[1].slice(0,3),['kkuwang_touch',false,'roof-guide']);
 assert.equal(starts[1][3],scope.openTouch);assert.equal(starts[1][4],'guide-proof');
});

test('result-save retry keeps the same play token and result evidence',async()=>{
 const posts=[],screens=[],proof={success:true,picks:[[0,1]]};
 const scope={activePlay:{play:'B',token:'original-token',code:'card_match',practice:false,done:false},
  act:async(action,data)=>{posts.push({action,data});if(posts.length===1)throw new Error('response lost');return {success:true};},
  info:(...args)=>screens.push(args),gameDialog:{close(){}},practiceCompleted:new Set()};
 vm.createContext(scope);vm.runInContext(part('async function finish(','let cleanupGame='),scope);
 await scope.finish(proof);assert.equal(scope.activePlay.done,false);
 await screens[0][2][0][1]();
 assert.equal(posts.length,2);assert.equal(posts[0].action,'game_finish');
 assert.deepEqual(plain(posts[0]),plain(posts[1]));assert.equal(posts[1].data.proof,proof);
 assert.equal(scope.activePlay.done,true);
});
