const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');

function setup(files=[]){
  const draws=[],names=[];
  const context=new Proxy({measureText:text=>({width:text.length*11}),fillText:text=>names.push(text),
    createLinearGradient:()=>({addColorStop(){}}),createRadialGradient:()=>({addColorStop(){}})},
    {get:(target,key)=>target[key]??(()=>{})});
  const sandbox=vm.createContext({window:{matchMedia:()=>({matches:false}),CampusAvatars:{size:40,draw:(_ctx,actor)=>{draws.push(actor.id);return true;}}},
    document:{createElement:()=>({getContext:()=>context})},performance:{now:()=>0},
    Path2D:class{constructor(){return new Proxy({},{get:()=>()=>{}});}}});
  for(const name of [...files,'isometric.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'../dist',name),'utf8'),sandbox,{filename:name});
  return {api:sandbox.window.CampusIsometric,window:sandbox.window,context,draws,names};
}
const emptyWorld=()=>({id:'test',width:10000,height:10000,angle:0,tilt:1,padding:0,rooms:[],objects:[],walls:[]});
const actor=(id,x,y,extra={})=>({id,nickname:'사용자 '+id,x,y,avatar:'male-1',color:'#4e9b91',dir:'down',moving:false,visualZ:0,...extra});
const frame=(e,renderer,actors,options={})=>{
  e.draws.length=0;e.names.length=0;
  renderer.draw(e.context,{camera:{x:0,y:0,zoom:1},actors,t:110,viewportWidth:1600,viewportHeight:1000,...options});
};

for(const floor of ['campus','rooftop'])test(`${floor}: 100 users issue 100 draws in a wide view and 10 in the viewport`,()=>{
  const e=setup(['map-data.js','rooftop-data.js','stair-motion.js']),data=e.window.CampusWorlds[floor],renderer=e.api.create({data});
  const actors=Array.from({length:100},(_,i)=>actor('p'+i,i<10?data.spawn[0]+i%5*15:0,i<10?data.spawn[1]+Math.floor(i/5)*10:0));
  frame(e,renderer,actors,{viewportWidth:20000,viewportHeight:20000});
  assert.equal(e.draws.filter(id=>id.startsWith('p')).length,100);
  const q=renderer.projectActor(actors[0]),before=JSON.stringify(actors);
  frame(e,renderer,actors,{camera:{zoom:1.5,x:800-q.x*1.5,y:500-q.y*1.5}});
  const drawnPlayers=e.draws.filter(id=>id.startsWith('p'));
  assert.equal(drawnPlayers.length,10);assert.deepEqual(drawnPlayers.sort(),actors.slice(0,10).map(a=>a.id).sort());
  assert.equal(JSON.stringify(actors),before,'render culling must not remove or change received player state');
});

test('partial bodies, long names, jump and poke remain at each viewport edge across camera zooms',()=>{
  const e=setup(['jump.js','poke.js']),renderer=e.api.create({data:emptyWorld()});
  for(const zoom of [.35,1,1.5,3.6]){
    const camera={x:120,y:85,zoom};
    const at=(id,x,y,extra)=>actor(id,(x-camera.x)/zoom,(y-camera.y)/zoom,extra);
    const actors=[at('self',800,500),at('left-body',-5*zoom,300),at('right-body',1600+5*zoom,300),
      at('top-body',800,-zoom),at('bottom-body',800,1000+20*zoom),
      at('long-name',-70*zoom/Math.max(zoom,.55),400,{nickname:'아주 긴 닉네임'.repeat(20)}),
      at('jump',900,1000+60*zoom,{_campusJump:{started:-250}}),
      at('poke',-45*zoom,600,{_campusPoke:{started:0,dir:'right',reach:72}}),
      at('far-left',-2000,500),at('far-right',3600,500),at('far-top',800,-2000),at('far-bottom',800,3000)];
    frame(e,renderer,actors,{camera});
    assert.deepEqual([...e.draws].sort(),actors.slice(0,8).map(a=>a.id).sort(),`zoom ${zoom}`);
    assert.ok(e.names.includes(actors[5].nickname),'a partially visible long nickname must remain');
  }
});

test('the first visible peer never acquires the offscreen self label',()=>{
  const e=setup(),renderer=e.api.create({data:emptyWorld()});
  frame(e,renderer,[actor('self',5000,5000),actor('peer',100,100)]);
  assert.deepEqual(e.draws,['peer']);assert.deepEqual(e.names,['사용자 peer']);
});

test('custom classroom actor drawing remains available and respects viewport culling',()=>{
  const e=setup(),custom=[],data=emptyWorld();
  data.drawActor=(_ctx,a)=>{if(a.role!=='instructor')return false;custom.push(a.id);return true;};
  const renderer=e.api.create({data});
  frame(e,renderer,[actor('self',200,300),actor('instructor',385,115,{role:'instructor'}),
    actor('seated',220,250,{seated:true}),actor('distant-instructor',5000,5000,{role:'instructor'})]);
  assert.deepEqual(custom,['instructor']);assert.deepEqual([...e.draws].sort(),['seated','self']);
  assert.equal(e.names.length,3);
});
