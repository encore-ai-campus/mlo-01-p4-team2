const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../learning/static/learning/learning.js'),'utf8');
const start=source.indexOf('  async function completeSession()');
const end=source.indexOf('  const fetchSessionResult',start);
assert.ok(start>=0&&end>start);
function fixture(result,error){
 const events=[],calls=[];
 const context={state:{sessionId:17},post:async path=>{calls.push(path);if(error)throw error;return result;},
 window:{dispatchEvent:event=>events.push(event)},CustomEvent:class{constructor(type,data){this.type=type;this.detail=data.detail;}}};
 vm.createContext(context);vm.runInContext(source.slice(start,end),context);
 return {events,calls,complete:context.completeSession};
}
test('server-confirmed learning completions update the collection and emit earned IDs',async()=>{
 const result={achievements:[{event_uuid:'python',achieved:true}],newly_completed:['python']},f=fixture(result);
 assert.equal(await f.complete(),result);assert.deepEqual(f.calls,['sessions/17/complete/']);
 assert.deepEqual(f.events.map(event=>event.type),['campus:achievements','campus:achievement-earned']);
 assert.equal(f.events[1].detail,result);
});
test('completion retries update state without replaying earned IDs',async()=>{
 const f=fixture({achievements:[],newly_completed:[]});await f.complete();
 assert.deepEqual(f.events.map(event=>event.type),['campus:achievements']);
});
test('failed saves and ineligible completions do not fabricate an achievement',async()=>{
 const f=fixture(null,new Error('save failed'));await assert.rejects(f.complete(),/save failed/);assert.equal(f.events.length,0);
 const ordinary=fixture({correct_count:10});await ordinary.complete();assert.equal(ordinary.events.length,0);
});
