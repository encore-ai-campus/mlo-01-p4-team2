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
