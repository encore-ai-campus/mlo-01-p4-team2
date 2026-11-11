const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict'),{test}=require('node:test');
const root=require('node:path').resolve(__dirname,'../..');
const source=fs.readFileSync(root+'/frontend/dist/isometric.js','utf8');
const start=source.indexOf('  function artFrontDepth('),end=source.indexOf('  function createProjection(');
const ctx={};vm.runInNewContext(source.slice(start,end)+';this.front=artFrontDepth;',ctx);
const outline=[{x:0,y:20},{x:40,y:0},{x:80,y:20},{x:40,y:40}];
test('art front edge follows the existing base rather than an elevated image',()=>{
 assert.equal(ctx.front(outline,40),40);assert.equal(ctx.front(outline,10),25);
 assert.ok(15<ctx.front(outline,40));assert.ok(45>ctx.front(outline,40));
 assert.equal(ctx.front(outline,-10),20);assert.equal(ctx.front(outline,90),20);
});
test('actor stays behind foreground art even if a later sprite is behind the actor',()=>{
 const a=source.indexOf('    function placeActors('),b=source.indexOf('    function actorInView(',a);
 const context={p:(x,y)=>({x,y}),projectActor:a=>a,overlaps:()=>true};
 vm.runInNewContext(source.slice(start,end)+source.slice(a,b)+';this.place=placeActors;',context);
 const front={o:{id:'front'},b:{},r:{x:0,y:0,w:80,h:40},depth:20,groundOutline:outline};
 const back={o:{id:'back'},b:{},r:{x:0,y:-50,w:80,h:10},depth:-45};
 const actor={x:40,y:15};const entities=context.place([front,back],[actor]);
 assert.ok(entities.findIndex(e=>e.actor===actor)<entities.indexOf(front));
 const ahead={x:40,y:45};const ordered=context.place([front,back],[ahead]);
 assert.ok(ordered.findIndex(e=>e.actor===ahead)>ordered.indexOf(front));
});
