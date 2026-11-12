const test=require('node:test'),assert=require('node:assert/strict');

async function attempt(timeLimitMs=11110){
 const {AttemptController}=await import('../minigames/typing/src/core/attemptController.js');
 let now=0;const events=[];
 const controller=new AttemptController({id:'deadline',version:1,language:'python',difficulty:'beginner',code:'abc'},'natural',
  {emit:event=>events.push(event)},{timeLimitMs},'play',{monotonic:()=>now,utc:()=>new Date(0).toISOString()});
 return {controller,events,set now(value){now=value;}};
}

for(const elapsed of [11109,11110])test(`typing can finish at ${elapsed}ms`,async()=>{
 const f=await attempt();f.controller.input({type:'append',text:'a'});f.now=elapsed;
 f.controller.input({type:'append',text:'bc'});
 assert.equal(f.controller.getSnapshot().status,'completed');
 assert.equal(f.controller.metrics().elapsed_ms,elapsed);
 assert.equal(f.controller.expired,false);assert.equal(f.controller.metrics().accuracy_pct,100);
 f.now=30000;assert.equal(f.controller.expire(),false);
 assert.equal(f.controller.metrics().elapsed_ms,elapsed);
});

for(const elapsed of [11110.1,11111,20000])test(`typing rejects late input at ${elapsed}ms`,async()=>{
 const f=await attempt();f.controller.input({type:'append',text:'a'});f.now=elapsed;
 f.controller.input({type:'append',text:'bc'});
 assert.equal(f.controller.getSnapshot().status,'abandoned');
 assert.equal(f.controller.getSnapshot().cursor,1);assert.equal(f.controller.expired,true);
 assert.equal(f.events.at(-1).abandon_reason,'timeout');
 assert.deepEqual(f.events.map(e=>e.event_type),['code_typing.started','code_typing.abandoned']);
 f.controller.expire();f.controller.input({type:'append',text:'bc'});
 assert.equal(f.events.length,2);
});

test('typing waits for the first character and expires without another input',async()=>{
 const f=await attempt();f.now=60000;
 assert.equal(f.controller.expire(),false);assert.equal(f.controller.getSnapshot().status,'idle');
 f.controller.input({type:'append',text:'a'});
 assert.equal(f.controller.metrics().elapsed_ms,0);
 f.now=71110;assert.equal(f.controller.expire(),false);
 f.now=71111;assert.equal(f.controller.expire(),true);
 assert.equal(f.controller.metrics().elapsed_ms,11111);
 f.now=80000;assert.equal(f.controller.metrics().elapsed_ms,11111);
});

test('typing display never rounds an overtime result back to 11.11 seconds',async()=>{
 const {formatDuration}=await import('../minigames/typing/src/core/metrics.js');
 assert.equal(formatDuration(11109),'00:11.11');
 assert.equal(formatDuration(11110),'00:11.11');
 assert.equal(formatDuration(11110.1),'00:11.12');
 assert.equal(formatDuration(11111),'00:11.12');
});

for(const elapsed of [20000,40000,40000.1])test(`40-second typing round boundary at ${elapsed}ms`,async()=>{
 const f=await attempt(40000);f.controller.input({type:'append',text:'a'});f.now=elapsed;
 f.controller.input({type:'append',text:'bc'});
 assert.equal(f.controller.getSnapshot().status,elapsed<=40000?'completed':'abandoned');
 assert.equal(f.controller.expired,elapsed>40000);
});
