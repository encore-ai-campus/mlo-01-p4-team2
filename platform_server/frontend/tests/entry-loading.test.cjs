const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../dist/entry-loading.js'),'utf8');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function fixture(floor='campus'){
 const nodes=new Map(),timers=new Map(),started=[],idle=[],events=[];let next=0;
 const node=id=>{if(!nodes.has(id))nodes.set(id,{hidden:false,dataset:{},value:0,textContent:'',focus(){this.focused=true;},setAttribute(k,v){this[k]=v;}});return nodes.get(id);};
 node('campusPresenceConfig').textContent=JSON.stringify({avatar:'male-1',position:{floor}});
 node('entryRetry').hidden=true;
 const window={CampusApp:{floor,presence:{connected:true,setInput(value){events.push(['input',value]);}}},CampusWorlds:{campus:{objects:[{seasonalArt:'cart'}]},rooftop:{objects:[{type:'pepero-monument'}]}},dispatchEvent(event){events.push(event.type);}};
 const context={window,document:{getElementById:node,documentElement:{classList:{remove(name){events.push(name);}}}},performance:{now:()=>123,mark:name=>events.push(name)},Event:class{constructor(type){this.type=type;}},setTimeout(fn,ms){const id=++next;timers.set(id,{fn,ms});return id;},clearTimeout:id=>timers.delete(id),requestAnimationFrame:fn=>queueMicrotask(fn),requestIdleCallback:fn=>idle.push(fn),location:{reload(){events.push('reload');}}};
 window.requestIdleCallback=context.requestIdleCallback;
 vm.runInNewContext(source,context);
 function image(){const listeners=new Map();return {addEventListener(name,fn){listeners.set(name,fn);},removeEventListener(name){listeners.delete(name);},set src(url){this.url=url;started.push(url);},decode:async()=>{},load(){return listeners.get('load')?.();},fail(){return listeners.get('error')?.();}};}
 return {api:window.CampusEntry,window,node,image,timers,started,idle,events};
}
test('arrival images run three at a time; other floors and background art wait',async()=>{
 const f=fixture(),images=Array.from({length:5},()=>f.image());
 const ready=images.slice(0,4).map((im,i)=>f.api.image(im,'critical-'+i,{floors:['campus']}));
 const later=f.api.image(images[4],'roof',{floors:['rooftop']});
 assert.equal(f.started.length,3);await images[0].load();assert.equal(f.started.length,4);
 await Promise.all(images.slice(1,4).map(im=>im.load()));await Promise.all(ready);
 assert.equal(f.started.includes('roof'),false);
 const floor=f.api.ensureFloor('rooftop');assert.equal(f.started.at(-1),'roof');await images[4].load();await Promise.all([floor,later]);
});
test('overlay waits for decoded arrival art and connected player, then starts background work singly',async()=>{
 const f=fixture(),critical=f.image(),background=[f.image(),f.image()];
 const asset=f.api.image(critical,'player');background.forEach((im,i)=>f.api.image(im,'background-'+i,{background:true}));
 let prepared=false;const preparation=f.api.prepare().then(()=>{prepared=true;});await tick();assert.equal(prepared,false);
 await critical.load();await Promise.all([asset,preparation]);assert.equal(f.api.blocking,true);
 f.window.CampusApp.presence.connected=false;const completion=f.api.completeWhenConnected();await tick();assert.equal(f.node('campusEntry').hidden,false);
 f.window.CampusApp.presence.connected=true;const poll=[...f.timers.values()].find(t=>t.ms===100);assert.ok(poll);poll.fn();await completion;
 assert.equal(f.api.blocking,false);assert.equal(f.node('campusEntry').hidden,true);assert.equal(f.node('entryProgress').value,100);assert.ok(f.events.includes('campus:entry-ready'));
 assert.equal(f.started.length,1);f.idle[0]();assert.equal(f.started.length,2);await background[0].load();assert.equal(f.started.length,3);await background[1].load();
});
test('failed critical image leaves entry blocked with a retry action',async()=>{
 const f=fixture(),im=f.image();f.api.image(im,'missing');const preparation=f.api.prepare();im.fail();await assert.rejects(preparation,/이미지/);
 assert.equal(f.api.blocking,true);assert.equal(f.node('entryRetry').hidden,false);assert.deepEqual(Array.from(f.api.metrics.failed),['missing']);f.node('entryRetry').onclick();assert.ok(f.events.includes('reload'));
});
test('arrival timeout shows retry instead of exposing an unprepared map',async()=>{
 const f=fixture('rooftop');f.api.image(f.image(),'slow',{floors:['rooftop']});const preparation=f.api.prepare();
 const timeout=[...f.timers.values()].find(t=>t.ms===30000);assert.ok(timeout);timeout.fn();await assert.rejects(preparation,/지연/);assert.equal(f.node('entryRetry').hidden,false);assert.equal(f.api.blocking,true);
});
test('seasonal image routing follows objects on each floor',()=>{
 const f=fixture();assert.deepEqual(Array.from(f.api.floorsForArt('cart')),['campus']);assert.deepEqual(Array.from(f.api.floorsForArt('monument')),['rooftop']);assert.deepEqual(Array.from(f.api.floorsForArt('unused')),[]);
});
test('known image URLs use optimized files and unknown URLs retain their original path',async()=>{
 const f=fixture(),known=f.image(),unknown=f.image();
 const first=f.api.image(known,'assets/characters/male-1-walk-v5.png');
 const second=f.api.image(unknown,'assets/characters/other.png');
 assert.match(f.started[0],/^assets\/entry-optimized\/male-1-walk-v5-[a-f0-9]+\.webp$/);
 assert.equal(f.started[1],'assets/characters/other.png');
 assert.ok(fs.existsSync(path.join(__dirname,'../dist',f.started[0])));
 await Promise.all([known.load(),unknown.load(),first,second]);
});
test('all optimized image mappings point to existing originals and WebP assets',()=>{
 const mapping=JSON.parse(source.match(/const optimizedImages=(\{.*?\});/)[1]);
 assert.equal(Object.keys(mapping).length,34);
 for(const [original,optimized] of Object.entries(mapping)){
  assert.ok(fs.existsSync(path.join(__dirname,'../dist',original)),original);
  const data=fs.readFileSync(path.join(__dirname,'../dist',optimized));
  assert.equal(data.toString('ascii',0,4),'RIFF',optimized);assert.equal(data.toString('ascii',8,12),'WEBP',optimized);
 }
});
