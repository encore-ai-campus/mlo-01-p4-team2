const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path'),assert=require('node:assert/strict'),test=require('node:test');
const root=path.resolve(__dirname,'..');
function api(){const window={CAMPUS_SEASON:'pepero',dispatchEvent(){}};vm.runInNewContext(fs.readFileSync(path.join(root,'dist/pepero-theme.js'),'utf8'),{window,Image:class{},matchMedia:()=>({matches:true}),CustomEvent:class{}});return window.CampusSeasonal;}
test('furniture front edges align with the wall while verticals remain upright',()=>{
 const renderer=api(),stations=require('../maps/minigames.json');
 for(const tilt of [.58,.7]){const p=(x,y,z=0)=>({x:(x-y)*Math.SQRT1_2,y:(x+y)*Math.SQRT1_2*tilt-z});
  for(const o of stations.filter(o=>o.artPlaneSlopes)){const projection=renderer.artProjection(o,p),[a,b]=o.artPlaneSlopes;
   assert(Math.abs((a+projection.shearY)/projection.scaleX+tilt)<1e-10);
   if(!o.artDepthScale)assert(Math.abs((b+projection.shearY)/projection.scaleX-tilt)<1e-10);
   else {const ordinary=renderer.artProjection({...o,artDepthScale:1},p);assert(Math.abs(projection.scaleX/ordinary.scaleX-o.artDepthScale)<1e-10);}
   assert(projection.scaleX>0);
  }
 }
});
test('ordinary assets and the upright pairing cabinet retain their original projection',()=>{const pairing=require('../maps/minigames.json').find(o=>o.gameCode==='term_match');for(const o of [{},pairing]){const projection=api().artProjection(o,()=>({x:0,y:0}));assert.equal(projection.scaleX,1);assert.equal(projection.shearY,0);}});
