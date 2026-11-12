const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function fixture(){
 const requests=[],listeners=new Map(),elements=new Map();
 const get=id=>{if(!elements.has(id))elements.set(id,{open:false,hidden:true,children:[],replaceChildren(){this.children=[];},append(item){this.children.push(item);}});return elements.get(id);};
 class Image{set src(url){this.url=url;requests.push(url);queueMicrotask(()=>this.onload?.());}decode(){return Promise.resolve();}}
 const context={window:{addEventListener:(name,fn)=>listeners.set(name,fn)},document:{hidden:false,currentScript:{src:'http://campus.test/static/campus/roof-slideshow.js'},getElementById:get,createElement:()=>({append(){}})},
  Image,URL,performance:{now:()=>0},setTimeout:()=>1,clearTimeout(){}};
 vm.createContext(context);
 for(const file of ['roof-slideshow-config.js','roof-slideshow.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'../dist',file),'utf8'),context);
 return {api:context.window.CampusAds,requests,listeners,get};
}
test('login readiness and the campus floor do not download slideshow photos',()=>{
 const f=fixture();f.listeners.get('campus:ready')?.();
 f.api.tick({now:0,floor:'campus',renderer:{setDisplayImage(){assert.fail('wrong floor');}}});
 assert.equal(f.requests.length,0);
});
test('floor transfer downloads only the displayed pair and closing does not preload the next pair',async()=>{
 const f=fixture();f.get('floorTransitionDialog').open=true;await f.api.startPortal();
 assert.equal(f.requests.length,2);assert.equal(f.get('floorTransitionAdSlot').children.length,2);
 f.listeners.get('campus:floor-transition-end')();assert.equal(f.requests.length,2);
 await f.api.startPortal();assert.equal(f.requests.length,4);
});
test('the actual rooftop display loads one photo and then prepares its next photo',async()=>{
 const f=fixture(),shown=[],renderer={setDisplayImage:(id,image)=>{shown.push(id);return true;}};
 f.api.tick({now:0,floor:'rooftop',renderer});assert.equal(f.requests.length,1);await tick();
 f.api.tick({now:1,floor:'rooftop',renderer});assert.deepEqual(shown,['roof-ad-board']);assert.equal(f.requests.length,2);
});
