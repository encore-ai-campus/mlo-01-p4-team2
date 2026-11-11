const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const sandbox=vm.createContext({window:{},Image:class {}});
for(const name of ['map-data.js','rooftop-data.js','stair-motion.js','avatars.js'])
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../dist',name),'utf8'),sandbox);
const worlds=sandbox.window.CampusWorlds,S=sandbox.window.CampusStairs,A=sandbox.window.CampusAvatars;
const roof=worlds.rooftop,t=roof.terrace,depth=t.w/t.levels;

test('terrace height is continuous, reversible and meets both landings and tread centers',()=>{
  const samples=[];
  for(let x=t.x-10;x<=t.x+t.w+10;x+=.25)samples.push(S.heightAt(roof,x,950));
  assert.equal(samples[0],60);assert.equal(samples.at(-1),0);
  for(let i=1;i<samples.length;i++) {
    assert.ok(samples[i]<=samples[i-1]+1e-9,'descending path must never climb');
    assert.ok(samples[i-1]-samples[i]<.3,'a nosing must not introduce a height jump');
  }
  for(let i=0;i<t.levels;i++) {
    const x=t.x+(i+.5)*depth;
    assert.equal(S.heightAt(roof,x,950),roof.elevationAt(x,950),'feet must rest on tread centers');
    assert.equal(S.groundAt(roof,x,950),roof.elevationAt(x,950),'physical step heights are retained');
  }
  assert.equal(S.heightAt(roof,800,950),60);assert.equal(S.heightAt(roof,1600,950),0);
});

test('both walking directions follow actual height and alternate all four poses',()=>{
  for(const [start,end,direction] of [[1420,1030,'up'],[1030,1420,'down']]) {
    const actor={floor:'rooftop',x:start,y:950,moving:true,dir:direction==='up'?'left':'right'};
    S.resetActor(roof,actor);
    const frames=new Set();let previous=actor.visualZ;
    for(let x=start;x!==end;x+=Math.sign(end-start)*2) {
      actor.x=x;S.updateActor(roof,actor,1/60);
      assert.equal(actor.visualZ,S.heightAt(roof,x,950),'height cannot lag behind position');
      assert.ok(Math.abs(actor.visualZ-previous)<2.4);previous=actor.visualZ;
      if(actor.stairMotion.direction) {
        assert.equal(actor.stairMotion.direction,direction);
        assert.ok(actor.stairMotion.lift>=0&&actor.stairMotion.lift<=1.6);
        frames.add(A.frameFor(actor,1000).column);
      }
    }
    assert.deepEqual([...frames].sort(),[0,1,2,3]);
  }
});

test('walking height is independent of frame rate and network interpolation history',()=>{
  const actors=[{x:1418,y:950,moving:true},{x:1418,y:950,moving:true}];
  actors.forEach(actor=>S.resetActor(roof,actor));
  for(let i=0;i<2;i++)for(let x=1418;x>=1260;x-=i?1:2) {
    actors[i].x=x;S.updateActor(roof,actors[i],i?1/120:1/60);
  }
  assert.equal(actors[0].visualZ,actors[1].visualZ);
  assert.equal(actors[0].visualZ,S.heightAt(roof,1260,950));
});

test('standing still and moving across a tread do not animate climbing',()=>{
  const actor={x:1200,y:930,moving:true,dir:'down'};S.resetActor(roof,actor);
  actor.y+=2;S.updateActor(roof,actor,1/60);
  assert.equal(actor.stairMotion.direction,null);assert.equal(actor.stairFrame,null);
  const height=actor.visualZ;actor.x-=3;S.updateActor(roof,actor,1/60);
  actor.moving=false;
  for(let i=0;i<90;i++)S.updateActor(roof,actor,1/60);
  assert.ok(actor.stairMotion.lift<1e-9);assert.ok(Math.abs(actor.stairMotion.lean)<1e-9);
  assert.equal(actor.stairFrame,null);assert.equal(actor.visualZ,height);
});

test('18F flight retains height while the removed roof descent stays absent',()=>{
 const data=worlds.campus,o=data.objects.find(o=>o.type==='stairs'),f=S.flightFor(data,o),x=o.x+o.w/2;
 assert.equal(f.top-f.bottom,33);assert.ok(S.heightAt(data,x,o.y+f.run/2)>10);
 assert.equal(S.heightAt(data,x,o.y-10),0);assert.equal(roof.objects.some(o=>o.type==='stairs'),false);
});

test('restoring and teleporting never borrow an old stair height or walking pose',()=>{
  const actor={floor:'rooftop',x:1250,y:950,moving:true};S.resetActor(roof,actor);
  actor.x-=3;S.updateActor(roof,actor,1/60);assert.equal(actor.stairMotion.direction,'up');
  Object.assign(actor,{floor:'campus',x:2233.5,y:912,moving:false});S.resetActor(worlds.campus,actor);
  assert.equal(actor.stairFrame,null);assert.equal(actor.stairMotion.lift,0);
  assert.equal(actor.visualZ,S.heightAt(worlds.campus,actor.x,actor.y));
  Object.assign(actor,{floor:'rooftop',x:900,y:950,moving:true});S.updateActor(roof,actor,1/60);
  assert.equal(actor.visualZ,60);assert.equal(actor.stairMotion.direction,null);
  actor.x=1700;S.updateActor(roof,actor,1/60);
  assert.equal(actor.visualZ,0);assert.equal(actor.stairMotion.lift,0);
});

test('18F entry and portal return stay on a flat clear landing',()=>{
 const data=worlds.campus,o=data.objects.find(o=>o.id==='campus-roof-stairs');
 assert.equal(S.barriers(data).length,0);assert.equal(S.railLength(data,o,'left'),0);assert.equal(S.railLength(data,o,'right'),0);
 const returnPoint=o.portalAnchor;
 for(let dx=-20;dx<=20;dx++)assert.equal(S.heightAt(data,returnPoint[0]+dx,returnPoint[1]),0);
 const [x,y]=o.arrival;assert.equal(S.heightAt(roof,x,y),0);
});

test('removed rooftop descent adds no flights or handrail barriers',()=>{
 assert.equal(roof.objects.filter(o=>o.type==='stairs').length,0);
 assert.equal(S.barriers(roof).length,0);
});

test('stairs and poses add no fields to movement reports or change world coordinates',()=>{
  const actor={floor:'rooftop',x:1219,y:950,dir:'left',moving:true};
  const position=JSON.stringify({floor:actor.floor,x:actor.x,y:actor.y,dir:actor.dir});
  S.resetActor(roof,actor);S.updateActor(roof,actor,1/60);
  assert.equal(JSON.stringify({floor:actor.floor,x:actor.x,y:actor.y,dir:actor.dir}),position);
  const presence=fs.readFileSync(path.join(__dirname,'../dist/presence.js'),'utf8');
  assert.match(presence,/type:'input',keys:inputKeys/);
  assert.doesNotMatch(presence,/stairMotion|stairFrame|visualZ/);
});

test('stairs load before renderer and movement so the map cannot start with missing geometry',()=>{
  const html=fs.readFileSync(path.join(__dirname,'../dist/index.html'),'utf8');
  const scripts=[...html.matchAll(/<script src="([^"?]+)(?:\?[^" ]*)?"/g)].map(match=>match[1]);
  const index=name=>scripts.indexOf(name);
  assert.ok(index('stair-motion.js')>index('rooftop-data.js'));
  assert.ok(index('stair-motion.js')<index('isometric.js'));
  assert.ok(index('stair-motion.js')<index('campus.js'));
});
