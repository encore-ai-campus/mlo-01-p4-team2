const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../dist/campus-loading.js'),'utf8');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
function fixture(failGame=false){
 const elements=new Map(),listeners=new Map(),requests=[],blobs=[],imageRequests=[];
 const get=id=>{if(!elements.has(id))elements.set(id,{hidden:false,textContent:'',attributes:{},style:{setProperty(){}},setAttribute(k,v){this.attributes[k]=v;},focus(){}});return elements.get(id);};
 class Image{
  constructor(){this.complete=false;this.naturalWidth=0;this.listeners=new Map();this.fail=false;}
  set src(value){this.url=value;imageRequests.push(value);this.complete=true;this.naturalWidth=this.fail?0:8;queueMicrotask(()=>this.listeners.get(this.fail?'error':'load')?.());}
  get src(){return this.url;}
  addEventListener(name,fn){this.listeners.set(name,fn);}
  removeEventListener(name){this.listeners.delete(name);}
  decode(){return Promise.resolve();}
 }
 class TestURL extends URL{static createObjectURL(blob){blobs.push(blob);return 'blob:http://campus.test/'+blobs.length;}}
 const classes=new Set(['campus-loading']),workspace={removeAttribute(name){this.removed=name;}};
 const document={baseURI:'http://campus.test/static/campus/',images:[],fonts:{ready:Promise.resolve()},getElementById:get,
  documentElement:{classList:{remove:name=>classes.delete(name)}},querySelector:()=>workspace};
 const window={addEventListener:(name,fn)=>listeners.set(name,fn)};
 let failed=failGame;
 vm.runInNewContext(source,{window,document,Image,URL:TestURL,Blob,requestAnimationFrame:fn=>setImmediate(fn),
  fetch:async(url,options)=>{requests.push({url,options});const ok=!failed;failed=false;
   return {ok,redirected:false,headers:{get:()=> 'text/html'},text:async()=>'<html><head></head><body>game</body></html>'};}});
 return {window,api:window.CampusLoading,document,get,Image,requests,blobs,imageRequests,classes,workspace,ready:()=>listeners.get('campus:ready')()};
}
test('startup waits for image decoding, downloads games without running them, then releases the workspace',async()=>{
 const f=fixture(),decode=deferred(),image=new f.Image();image.src='assets/map.png';image.decode=()=>decode.promise;
 f.api.track(image);let done=false;const ready=f.api.prepare('male-1').then(()=>done=true);
 await tick();assert.equal(done,false);assert.ok(Number(f.get('campusLoadingProgress').attributes['aria-valuenow'])<100);
 assert.equal(f.requests.length,5);assert.equal(f.blobs.length,0);assert.ok(f.classes.has('campus-loading'));
 decode.resolve();await ready;assert.equal(f.get('campusLoadingPercent').textContent,'100%');
 assert.ok(f.classes.has('campus-loading'),'keep the overlay until campus initialization finishes');
 f.ready();assert.equal(f.get('campusLoading').hidden,true);assert.equal(f.workspace.removed,'inert');
 assert.equal(f.get('campusLoading').attributes['aria-busy'],'false');
});
test('failed downloads keep the gate closed and retry only the failed game file',async()=>{
 const f=fixture(true);let done=false;const ready=f.api.prepare('female-1').then(()=>done=true);
 await tick();assert.equal(done,false);assert.equal(f.get('campusLoadingRetry').hidden,false);
 assert.equal(f.get('campusLoadingError').hidden,false);assert.notEqual(f.get('campusLoadingPercent').textContent,'100%');
 assert.equal(f.requests.length,5);await f.get('campusLoadingRetry').onclick();await ready;
 assert.equal(f.requests.length,6);assert.equal(f.requests[0].url,f.requests.at(-1).url);
 assert.equal(f.get('campusLoadingPercent').textContent,'100%');
});
test('failed images retry on the existing image object without losing successful downloads',async()=>{
 const f=fixture(),image=new f.Image();image.fail=true;image.src='assets/map.png';f.api.track(image);
 let done=false;const ready=f.api.prepare('male-1').then(()=>done=true);await tick();assert.equal(done,false);
 image.fail=false;await f.get('campusLoadingRetry').onclick();await ready;
 assert.equal(f.imageRequests.filter(url=>url==='assets/map.png').length,2);assert.equal(f.requests.length,5);
});
test('games reuse their downloaded source and blob URL with the correct relative asset base',async()=>{
 const f=fixture();await f.api.prepare('male-1');
 const game='minigames/timer.html?v=20261105-typing-replay-v1';
 assert.match(f.api.gameSource(game),/game/);const url=f.api.gameURL(game);
 assert.equal(f.api.gameURL('/static/campus/'+game),url);assert.equal(f.blobs.length,1);assert.equal(f.requests.length,5);
 assert.match(await f.blobs[0].text(),/<base href="http:\/\/campus.test\/static\/campus\/minigames\/">/);
 assert.throws(()=>f.api.gameURL('https://foreign.test/game.html'),/준비되지/);
 for(const request of f.requests){assert.equal(request.options.credentials,'same-origin');assert.equal(request.options.cache,'no-cache');
  const url=new URL(request.url);assert.ok(fs.existsSync(path.join(__dirname,'../dist',url.pathname.replace('/static/campus/',''))));}
});
test('lazy achievement and slideshow images stay out of the startup gate',async()=>{
 const f=fixture(),badge=new f.Image(),slide=new f.Image();
 badge.src='http://campus.test/static/campus/assets/achievements/badge.png';
 slide.src='http://campus.test/static/campus/assets/roof-slides/photo.png';
 badge.decode=slide.decode=()=>new Promise(()=>{});f.document.images=[badge,slide];
 await f.api.prepare('male-1');assert.equal(f.get('campusLoadingPercent').textContent,'100%');
 assert.ok(!f.requests.some(r=>/achievements|roof-slides/.test(r.url)));
});
test('campus initialization does not create a renderer or connection while the loading gate is pending',async()=>{
 const gate=deferred(),access=[];let prepared=0;
 const boot=vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../dist/campus.js'),'utf8'),{
  window:{CampusWorlds:{},CampusAvatars:{resolve:x=>x},CampusLoading:{prepare:()=>{prepared++;return gate.promise;}}},
  document:{getElementById:id=>{access.push(id);assert.equal(id,'campusPresenceConfig');return {textContent:'{"avatar":"male-1"}'};}}});
 await tick();assert.equal(prepared,1);assert.deepEqual(access,['campusPresenceConfig']);
 const stopped=assert.rejects(boot,/test stop/);gate.reject(new Error('test stop'));await stopped;
});

test('PR66 entry screen waits for game downloads and retries without a second overlay',async()=>{
 const f=fixture(true);f.window.CampusEntry={};let done=false;const ready=f.api.prepare('male-1').then(()=>done=true);
 await tick();assert.equal(done,false);assert.equal(f.get('entryRetry').hidden,false);assert.equal(f.requests.length,1);
 f.get('entryRetry').onclick();await ready;assert.equal(f.requests.length,6);
 assert.match(f.api.gameSource('event-games/card.html'),/game/);
 f.ready();assert.equal(f.classes.has('campus-loading'),true);
});
