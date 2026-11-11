const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../dist/main-quest.js'),'utf8');
test('five-step guidance targets ROOM1 immediately after investigation and keeps the final guide',()=>{
 const scope={state:null,mainGuide:{id:'guide'},portals:[{id:'room1'},{id:'room2'}]};
 vm.createContext(scope);
 for(const [start,end] of [['function guidance()','function renderTracker()'],['function goal()','function questArrowPlacement(']])vm.runInContext(source.slice(source.indexOf(start),source.indexOf(end)),scope);
 const targets=['guide','room1','guide','room2','guide'];
 for(let stage=1;stage<=5;stage++){
  scope.state={stage,completed:false};assert.equal(scope.goal().id,targets[stage-1]);assert.ok(scope.guidance());assert.doesNotMatch(scope.guidance(),/Python|학습교실/);
 }
 scope.state={stage:6,completed:true};assert.equal(scope.goal(),null);assert.equal(scope.guidance(),'사건 해결 완료');
});

test('Yeongseok conversation skips the easter egg hint and still records the briefing',async()=>{
 const scenes=[],actions=[];let closed=false;
 const scope={state:{games:{sql_memory:false},room1Briefed:false},roomTicket:'proof',
  story:(...args)=>scenes.push(args),mline:text=>({text}),pline:text=>({text}),
  act:async(...args)=>actions.push(args),dialog:{close(){closed=true;}}};
 vm.createContext(scope);
 vm.runInContext(source.slice(source.indexOf('function managerConversation()'),source.indexOf('function managerAnswer()')),scope);
 scope.managerConversation();
 assert.equal(scenes.at(-1)[2].length,4);
 assert.match(scenes.at(-1)[2][0].text,/안녕하세요.*영석/);
 assert.ok(scenes.at(-1)[2].every(line=>!line.text.includes('숨겨진 이스터에그')));
 await scenes.at(-1)[3][0][1]();
 assert.equal(actions[0][0],'room1_briefing');assert.equal(actions[0][1].ticket,'proof');assert.equal(closed,true);
 for(const state of [{games:{sql_memory:false},room1Briefed:true},{games:{sql_memory:true},room1Briefed:true}]){
  scope.state=state;scope.managerConversation();
  assert.equal(scenes.at(-1)[2].length,1);
  assert.ok(scenes.at(-1)[2].every(line=>!line.text.includes('숨겨진 이스터에그')));
 }
});
