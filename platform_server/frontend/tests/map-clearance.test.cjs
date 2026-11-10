const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const context=vm.createContext({window:{}});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../dist/map-data.js'),'utf8'),context);
const D=context.window.CampusData;
const overlap=(a,b)=>a.x<b.x+b.w&&a.x+a.w>b.x&&a.y<b.y+b.h&&a.y+a.h>b.y;
test('former storage enclosure and printer are removed',()=>{
 const storage=D.rooms.find(r=>r.id==='storage');
 assert.ok(!D.objects.some(o=>o.type==='printer'||o.id==='door-storage'));
 assert.ok(!D.walls.some(w=>w.y1===storage.y+storage.h&&w.y2===storage.y+storage.h&&w.x1>=storage.x&&w.x2<=storage.x+storage.w));
 assert.ok(!D.walls.some(w=>w.x1===storage.x&&w.x2===storage.x&&w.y1>=storage.y&&w.y2<=storage.y+storage.h));
});

test('lounge seating remains separated from the front wall and nearby furniture',()=>{
 const sofa=D.objects.find(o=>o.type==='sofa'&&o.dir==='up'&&o.roomId==='lounge1');
 assert.ok(946.5-(sofa.y+sofa.h)>=45);
 for(const o of D.objects.filter(o=>['coffee','ottoman','woodwall'].includes(o.type)))assert.equal(overlap(sofa,o),false);
 const wood=D.objects.find(o=>o.type==='woodwall');assert.ok(wood.x>1708.5+10);
});
test('classroom prompts, old panels and glass doors are removed while the PLAYDATA board remains',()=>{
 assert.equal(D.objects.filter(o=>o.type==='board').length,0);
 for(const room of D.rooms.filter(r=>r.floor==='class')){
  assert.equal(room.info,false);assert.equal(room.label,false);
 }
 assert.equal(D.objects.filter(o=>o.type==='glass-door').length,0);
 assert.ok(D.objects.find(o=>o.id==='roof-ad-board'&&o.type==='wall-poster'));
 assert.ok(!D.objects.some(o=>o.id==='class3-4-noticeboard'));
});

test('consultation tables, monitors and chairs fit inside their rooms with separate footprints',()=>{
 for(const room of D.rooms.filter(r=>r.id.startsWith('consult-'))){
  const desk=D.objects.find(o=>o.type==='desk'&&o.roomId===room.id);
  assert.ok(desk.x>=room.x+30&&desk.x+desk.w<=room.x+room.w-12);
  assert.ok(desk.y>=room.y+30&&desk.y+desk.h<=room.y+room.h-40);
  assert.ok(!D.objects.some(o=>o.type==='monitor'&&o.roomId===room.id));
  assert.equal(D.objects.filter(o=>o.type==='chair'&&o.roomId===room.id).length,2);
  for(const chair of D.objects.filter(o=>o.type==='chair'&&o.x>=room.x&&o.x<room.x+room.w&&o.y>=room.y&&o.y<room.y+room.h))assert.equal(overlap(chair,desk),false);
 }
});

test('solid campus furniture neither overlaps other furniture nor sinks into a wall',()=>{
 const onTop=new Set(['monitor','laptop','notebook','water','coffee-machine','bin','rug','floor-arrow']);
 // Wall-mounted or wall-like pieces are allowed to touch walls.
 const wallLike=new Set(['woodwall','glass-wall','pillar','stairs','fire-extinguisher']);
 const items=D.objects.filter(o=>o.solid!==false&&!o.mounted&&!onTop.has(o.type));
 for(const [i,a] of items.entries())for(const b of items.slice(i+1))assert.equal(overlap(a,b),false,`${a.id} ${a.type} / ${b.id} ${b.type}`);
 const k=D.spaceScale;
 for(const o of items.filter(o=>!wallLike.has(o.type)))for(const w of D.walls){
  const band={x:Math.min(w.x1,w.x2)-5*k,y:Math.min(w.y1,w.y2)-5*k,w:Math.abs(w.x2-w.x1)+10*k,h:Math.abs(w.y2-w.y1)+10*k};
  assert.equal(overlap(o,band),false,`${o.id} ${o.type} in wall ${w.x1},${w.y1}`);
 }
});

test('back-to-back chairs keep a visible aisle between them',()=>{
 const gap=(a,b)=>Math.max(b.x-(a.x+a.w),a.x-(b.x+b.w),b.y-(a.y+a.h),a.y-(b.y+b.h));
 const chairs=D.objects.filter(o=>o.type==='chair');
 for(const [i,a] of chairs.entries())for(const b of chairs.slice(i+1))assert.ok(gap(a,b)>=6*D.spaceScale,`${a.id} / ${b.id}`);
});
