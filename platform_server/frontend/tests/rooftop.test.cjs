const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const sandbox=vm.createContext({window:{}});
for(const name of ['map-data.js','rooftop-data.js','isometric.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'../dist',name),'utf8'),sandbox);
const worlds=sandbox.window.CampusWorlds,roof=worlds.rooftop;
test('stairs connect both floors and arrive outside the reverse trigger',()=>{
  for(const data of Object.values(worlds))for(const portal of data.portals){
    const object=data.objects.find(o=>o.id===portal.objectId),destination=worlds[object.toFloor],[x,y]=object.arrival;
    assert.ok(destination&&x>0&&y>0&&x<destination.width&&y<destination.height);
    assert.ok(destination.rooms.some(r=>x>=r.x&&x<=r.x+r.w&&y>=r.y&&y<=r.y+r.h));
    assert.ok(!destination.portals.some(p=>x>=p.x&&x<=p.x+p.w&&y>=p.y&&y<=p.y+p.h));
    assert.ok(object.approach[0]>=portal.x&&object.approach[0]<=portal.x+portal.w&&object.approach[1]>=portal.y&&object.approach[1]<=portal.y+portal.h);
  }
});
test('central vertical terrace divides equal areas with three aligned open booths',()=>{
  assert.equal(roof.rooms.filter(r=>r.id.startsWith('festival-')).length,3);
  const booths=roof.objects.filter(o=>o.type==='exhibition-booth');
  assert.equal(booths.length,3);
  assert.equal(new Set(booths.map(o=>o.x)).size,1,'booth backs must share a floor line');
  const P=sandbox.window.CampusIsometric.createProjection(roof),axis=P.vector(0,1);
  const centers=booths.map(o=>P.project(o.x+o.w/2,o.y+o.h/2,roof.elevationAt(o.x,o.y)));
  for(let i=1;i<booths.length;i++){
    assert.ok(booths[i].y-booths[i-1].y>440,'the long floor row must have wide spacing');
    const dx=centers[i].x-centers[i-1].x,dy=centers[i].y-centers[i-1].y;
    assert.ok(Math.abs(dx*axis.y-dy*axis.x)<1e-8,'projected row must follow the rotated floor axis');
    assert.ok(Math.abs(dy)>100,'the floor row must retain the rotated perspective');
  }
  assert.ok(booths.every(o=>o.entrance==='right'&&o.rotation===90&&o.x+o.w<roof.terrace.x&&o.solid===false&&o.collision.w===9));
  const t=roof.terrace,upper=roof.surfaces[0],lower=roof.surfaces.at(-1);
  assert.equal(t.levels,5);assert.equal(t.rise,12);
  assert.equal(roof.elevationAt(booths[0].x,950),60);assert.equal(roof.elevationAt(1500,950),0);
  for(let i=0;i<t.levels;i++)assert.equal(roof.elevationAt(t.x+(i+.5)*t.w/t.levels,950),(t.levels-i)*t.rise);
  assert.equal(t.x-(booths[0].x+booths[0].w),328,'front aisle reduced by a further 20 percent');
  assert.equal(booths[0].x-upper.x,265.5,'rear aisle remains half of the original gap');
  assert.ok(!roof.objects.some(o=>o.type==='fountain'||o.type==='plaza-display'));
  assert.ok(!roof.objects.some(o=>['tree','planter'].includes(o.type)&&o.x<booths[0].x));
  assert.equal(lower.x+lower.w,2384);assert.equal(upper.x+upper.w,t.x);
  const rear=roof.walls.find(w=>w.x1===upper.x&&w.x2===upper.x);assert.ok(rear);
  const tall=roof.walls.find(w=>w.topZ===420);assert.ok(tall);assert.equal(tall.x1,upper.x);

});
test('lounge portal and relocated roof portal connect without automatic transfer',()=>{
 const from=worlds.campus.objects.find(o=>o.id==='campus-lounge2-portal');
 const back=roof.objects.find(o=>o.id==='roof-lounge2-portal');
 assert.ok(from&&back);assert.equal(from.toFloor,'rooftop');assert.equal(back.toFloor,'campus');
 assert.equal(from.confirmTransfer,false);assert.equal(back.confirmTransfer,false);assert.equal(back.solid,false);
 assert.deepEqual(Array.from(from.arrival),[1110,1000]);assert.deepEqual(Array.from(back.approach),Array.from(from.arrival));
 assert.deepEqual(Array.from(back.arrival),[from.x+from.w/2,from.y+from.h/2]);
 assert.ok(!worlds.campus.portals.some(p=>p.objectId===from.id));assert.equal(roof.portals.length,0);
 assert.ok(back.x+back.w<roof.terrace.x,'portal remains clear of the stairs');
});

test('all three booth centers have usable interaction points outside their furniture',()=>{
  const actions=roof.objects.filter(o=>o.action==='booth');assert.equal(actions.length,3);
  for(const action of actions){
    const shell=roof.objects.find(o=>o.roomId===action.roomId&&o.type==='exhibition-booth');
    assert.equal(action.interactionPoint,undefined);
    assert.equal(action.x,shell.x+10);assert.equal(action.y,shell.y+10);assert.equal(action.w,shell.w-20);assert.equal(action.h,shell.h-20);
    const x=action.x+action.w/2,y=action.y+action.h/2;
    for(const o of roof.objects.filter(o=>o.solid!==false)){
      const r=o.collision?{x:o.x+o.collision.x,y:o.y+o.collision.y,w:o.collision.w,h:o.collision.h}:o;
      const distance=Math.hypot(Math.max(r.x-x,0,x-r.x-r.w),Math.max(r.y-y,0,y-r.y-r.h));
      assert.ok(distance>5.5,action.id+' must not be inside '+o.id);
    }
  }
});
test('every raised surface stays inside the clockwise map and has a consistent elevation',()=>{
  const P=sandbox.window.CampusIsometric.createProjection(roof);
  for(const surface of roof.surfaces){const x=surface.x+surface.w/2,y=surface.y+surface.h/2,q=P.project(x,y,surface.z),back=P.unproject(q.x,q.y+surface.z);
    assert.equal(roof.elevationAt(x,y),surface.z);assert.ok(Math.abs(back.x-x)<1e-8&&Math.abs(back.y-y)<1e-8);
    assert.ok(q.x>0&&q.y>0&&q.x<P.width&&q.y<P.height);
  }
});
