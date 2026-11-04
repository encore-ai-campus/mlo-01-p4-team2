const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=path.resolve(__dirname,'..'),source=fs.readFileSync(root+'/dist/minigames.js','utf8');
const plain=x=>JSON.parse(JSON.stringify(x));
function fixture(){
 const elements=new Map(),listeners=new Map(),requests=[],starts=[],events=[];let blocked=false,opens=0,closes=0;
 function el(){return {hidden:true,open:false,textContent:'',children:[],dataset:{},handlers:{},contentWindow:{postMessage:(...args)=>sent.push(args)},
  addEventListener(k,fn){this.handlers[k]=fn;},focus(){},showModal(){this.open=true;},close(){this.open=false;},replaceChildren(...items){this.children=items;}};}
 const sent=[],get=id=>{if(!elements.has(id))elements.set(id,el());return elements.get(id);};
 const window={addEventListener:(name,fn)=>listeners.set(name,fn),dispatchEvent:event=>events.push(event)},document={cookie:'campus_csrftoken=csrf',getElementById:get,createElement:el};
 vm.runInNewContext(source,{window,document,AbortSignal,CustomEvent:class{constructor(type,options){this.type=type;this.detail=options.detail;}}, location:{origin:'http://campus.test'},setTimeout:()=>1,clearTimeout(){},
  fetch:(url,options)=>new Promise((resolve,reject)=>requests.push({url,options,resolve,reject}))});
 const api=window.CampusMinigames.create({start:async id=>{starts.push(id);return {play_uuid:starts.length===1?'play':'play-'+starts.length,game_code:'timer',game_name:'타이머',config:{targetMs:11110}};},isBlocked:()=>blocked,onOpen(){opens++;},onClose(){closes++;}});
 return {api,get,starts,requests,sent,events,freeze(){listeners.get('campus:freeze')({detail:{blocking:true}});},set blocked(v){blocked=v;},get opens(){return opens;},get closes(){return closes;},
  frame:()=>get('minigameFrame').children[0],message(data,options={}){listeners.get('message')({origin:'http://campus.test',source:this.frame()?.contentWindow,data:{version:1,gameId:'timer',playId:this.frame()?.dataset.sessionId||'play',...data},...options});},
  async respond(data,index=requests.length-1,ok=true){requests[index].resolve({ok,json:async()=>data});await new Promise(r=>setImmediate(r));}};
}
test('game launch and message bridge bind only the active origin, frame, game and play',async()=>{
 const f=fixture();f.blocked=true;await f.api.open({id:'terminal'});assert.equal(f.starts.length,0);
 f.blocked=false;await f.api.open({id:'terminal'});assert.equal(f.api.blocking,true);assert.equal(f.opens,1);
 assert.equal(f.frame().dataset.sessionId,'play');assert.match(f.frame().src,/timer.html(?:\?|$)/);
 for(const options of [{origin:'https://foreign.test'},{source:{}},{}]){
  f.message({type:'minigame:ready',...(Object.keys(options).length?{}:{playId:'old'})},options);
 }
 assert.equal(f.sent.length,0);f.message({type:'minigame:ready'});
 assert.equal(f.sent.length,1);assert.equal(f.sent[0][0].config.targetMs,11110);
});
test('duplicate results send one frozen payload and failed saves retry the same result',async()=>{
 const f=fixture();await f.api.open({id:'terminal'});f.message({type:'minigame:ready'});
 const result={status:'SUCCESS',duration_ms:11110};
 f.message({type:'minigame:result',result});f.message({type:'minigame:result',result});
 assert.equal(f.requests.length,1);assert.deepEqual(plain(JSON.parse(f.requests[0].options.body)),result);
 assert.equal(f.requests[0].options.headers['X-CSRFToken'],'csrf');
 await f.respond({error:'다시 시도'},0,false);assert.equal(f.get('minigameRetry').hidden,false);
 f.get('minigameRetry').onclick();assert.equal(f.requests[1].options.body,f.requests[0].options.body);
 await f.respond({status:'SUCCESS'});await f.get('minigameClose').onclick();
 assert.equal(f.requests.length,2);assert.equal(f.api.blocking,false);assert.equal(f.closes,1);
});
test('close during play persists INTERRUPTED and waits for confirmed storage',async()=>{
 const f=fixture();await f.api.open({id:'terminal'});const close=f.get('minigameClose').onclick();
 assert.deepEqual(JSON.parse(f.requests[0].options.body),{duration_ms:null,status:'INTERRUPTED'});
 assert.equal(f.get('minigameDialog').open,true);await f.respond({status:'INTERRUPTED'});await close;
 assert.equal(f.get('minigameDialog').open,false);assert.equal(f.api.blocking,false);
});
test('invalid or stale results cannot create requests',async()=>{
 const f=fixture();await f.api.open({id:'terminal'});f.message({type:'minigame:result',result:{status:'FAILED',duration_ms:0}});
 assert.equal(f.requests.length,0);f.message({type:'minigame:ready'});
 for(const result of [{status:'SUCCESS',duration_ms:-1},{status:'SUCCESS',duration_ms:1,difficulty:'beginner'}])f.message({type:'minigame:result',result});
 f.message({type:'minigame:result',playId:'old',result:{status:'FAILED',duration_ms:0}});
 assert.equal(f.requests.length,0);
});
test('timer success uses the displayed centisecond and rejects adjacent displayed values',async()=>{
 const {evaluate,TimerGame}=await import('../minigames/timer/engine.js');
 const cfg={targetMs:11110,toleranceMs:0,overtimeMs:2000};
 for(const ms of [11105,11109,11110,11114.9])assert.equal(evaluate(ms,cfg).success,true);
 for(const ms of [11104.9,11115,11100,11120])assert.equal(evaluate(ms,cfg).success,false);
 let now=0;const timer=new TimerGame(cfg,{now:()=>now,id:()=> 'id'});timer.start();now=11110;assert.equal(timer.finish().success,true);assert.equal(timer.finish(),null);
});
test('editable stations match browser and server geometry, including game code',()=>{
 const games=require('../maps/minigames.json'),worlds=require('../tools/build-event-map.cjs').build(),server=require('../../backend/campus/data/worlds.json');
 for(const game of games){
  const obj=worlds[game.floor].objects.find(o=>o.id===game.id),exported=server.worlds[game.floor].objects.find(o=>o.id===game.id);
  assert.equal(obj.action,'minigame');assert.equal(obj.solid,true);assert.equal(exported.gameCode,game.gameCode);
  assert.deepEqual(exported.interactionPoint,game.interactionPoint);
 }
 for(const name of ['timer','typing']){
  const html=fs.readFileSync(root+'/dist/minigames/'+name+'.html','utf8');
  assert.ok(html.includes('window.minigameChannel'));assert.ok(html.includes('duration_ms:'));
  assert.doesNotMatch(html,/<script[^>]+src=|<link[^>]+stylesheet/);
 }
});

test('server-closed plays let the player close without claiming a successful save',async()=>{
 const f=fixture();await f.api.open({id:'terminal'});f.message({type:'minigame:ready'});
 f.message({type:'minigame:result',result:{status:'FAILED',duration_ms:0}});
 f.requests[0].resolve({ok:false,status:409,json:async()=>({error:'이미 종료된 게임'})});
 await new Promise(r=>setImmediate(r));assert.equal(f.get('minigameRetry').hidden,true);
 assert.match(f.get('minigameStatus').textContent,/이미 종료/);
 await f.get('minigameClose').onclick();assert.equal(f.api.blocking,false);assert.equal(f.requests.length,1);
});

test('only a confirmed server result updates achievements and emits newly earned IDs',async()=>{
 const f=fixture();await f.api.open({id:'terminal'});f.message({type:'minigame:ready'});
 f.message({type:'minigame:result',result:{status:'SUCCESS',duration_ms:11110}});
 assert.equal(f.events.length,0);
 await f.respond({error:'저장 실패'},0,false);assert.equal(f.events.length,0);
 f.get('minigameRetry').onclick();
 const achievements=[{event_uuid:'timer',name:'찰나의 기적',achieved:true}];
 await f.respond({status:'SUCCESS',achievements,newly_completed:['timer']});
 assert.deepEqual(f.events.map(e=>e.type),['campus:achievements','campus:achievement-earned']);
 assert.deepEqual(plain(f.events[0].detail),achievements);
 assert.deepEqual(plain(f.events[1].detail.newly_completed),['timer']);
 f.get('minigameRetry').onclick();
 await f.respond({status:'SUCCESS',achievements,newly_completed:[]});
 assert.deepEqual(f.events.map(e=>e.type),['campus:achievements','campus:achievement-earned','campus:achievements']);
});

test('typing draws only beginner problems and each problem remains completable',async()=>{
 const {problems,pickBeginnerProblem}=await import('../minigames/typing/src/problems.js');
 const {validateProblems}=await import('../minigames/typing/src/core/normalize.js');
 const {AttemptController}=await import('../minigames/typing/src/core/attemptController.js');
 const pool=validateProblems(problems),beginners=pool.filter(p=>p.difficulty==='beginner');
 assert.equal(beginners.length,5);
 for(let i=0;i<5;i++){
  const problem=pickBeginnerProblem(pool,()=> (i+.5)/5);
  assert.equal(problem.id,beginners[i].id);assert.equal(problem.difficulty,'beginner');
  const controller=new AttemptController(problem,'natural',{emit(){}},{},'play');
  for(const char of problem.code)controller.input({type:'append',text:char});
  assert.equal(controller.getSnapshot().status,'completed');
  assert.equal(controller.metrics().accuracy_pct,100);
 }
 assert.throws(()=>pickBeginnerProblem([]),/입문/);
 const html=fs.readFileSync(root+'/dist/minigames/typing.html','utf8');
 assert.doesNotMatch(html,/id="(?:difficulty|problem)"/);
});

test('stations face the long office wall with reachable east-facing interaction points',()=>{
 const world=require('../tools/build-event-map.cjs').build().campus;
 const games=require('../maps/minigames.json');
 assert.equal(games[1].y-games[0].y-games[0].h,86);
 assert.ok(!world.objects.some(o=>o.id==='reference-campus-109'));
 for(const game of games){
  assert.equal(game.interactionPoint[0]-(game.x+game.w),28);
  assert.equal(game.interactionPoint[1],game.y+game.h/2);
  assert.equal(game.facing,'east');
  assert.ok(game.renderHeight>=170);
 }
});

test('a confirmed freeze closes the standalone frame without retrying a forbidden result',async()=>{
 const f=fixture();await f.api.open({id:'terminal'});f.freeze();
 assert.equal(f.api.blocking,false);assert.equal(f.get('minigameDialog').open,false);
 assert.equal(f.requests.length,0);assert.equal(f.closes,1);
});

test('replay waits for confirmed storage, starts a new play, and ignores the old frame',async()=>{
 const f=fixture();await f.api.open({id:'terminal'});f.message({type:'minigame:ready'});
 const oldFrame=f.frame();f.message({type:'minigame:result',result:{status:'FAILED',duration_ms:10}});
 f.message({type:'minigame:restart'});f.message({type:'minigame:restart'});
 assert.equal(f.starts.length,1);
 await f.respond({status:'FAILED'});
 assert.equal(f.starts.length,2);assert.equal(f.frame().dataset.sessionId,'play-2');assert.notEqual(f.frame(),oldFrame);
 f.message({type:'minigame:ready'});
 f.message({type:'minigame:result',playId:'play',result:{status:'SUCCESS',duration_ms:11110}},{source:oldFrame.contentWindow});
 assert.equal(f.requests.length,1);
 f.message({type:'minigame:result',result:{status:'FAILED',duration_ms:20}});
 assert.equal(f.requests.length,2);assert.match(f.requests[1].url,/play-2/);
});

test('replay never skips a failed result save',async()=>{
 const f=fixture();await f.api.open({id:'terminal'});f.message({type:'minigame:ready'});
 f.message({type:'minigame:result',result:{status:'FAILED',duration_ms:10}});f.message({type:'minigame:restart'});
 await f.respond({error:'저장 실패'},0,false);assert.equal(f.starts.length,1);
 f.get('minigameRetry').onclick();await f.respond({status:'FAILED'});
 assert.equal(f.starts.length,2);
});
