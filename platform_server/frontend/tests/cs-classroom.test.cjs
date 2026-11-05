const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const root=path.join(__dirname,'..');
const source=JSON.parse(fs.readFileSync(path.join(root,'maps/pepero-worlds.json'),'utf8')).worlds.campus;
test('the learning strip is three equal rooms ordered SQL, CS, Python without moving its outer bounds',()=>{
 const rooms=['class2','class-cs','class1'].map(id=>source.rooms.find(r=>r.id===id));
 assert.deepEqual(rooms.map(r=>r.name),['SQL 학습교실','CS 학습교실','Python 학습교실']);
 assert.equal(rooms[0].x,792);assert.equal(rooms[2].x+rooms[2].w,1708.5);
 for(let i=0;i<rooms.length;i++){
  const r=rooms[i];assert.equal(r.w,305.5);assert.equal(r.h,393);if(i)assert.equal(rooms[i-1].x+rooms[i-1].w,r.x);
  assert.ok(source.objects.some(o=>o.roomId===r.id&&o.type==='wall-sign'&&o.text===r.name));
 }
});
test('CS reuses the complete interior while keeping all subject labels and monitor IDs separate',()=>{
 const c=vm.createContext({window:{}});vm.runInContext(fs.readFileSync(path.join(root,'learning/static/learning/classroom-map.js'),'utf8'),c);
 const base=c.window.CampusClassroomMap.data,cs=c.window.CampusClassroomMap.forSubject('cs').data;
 assert.equal(cs.subject,'cs');assert.equal(cs.name,'CS 학습교실');assert.equal(cs.width,base.width);assert.equal(cs.height,base.height);
 assert.deepEqual(Array.from(cs.spawn),Array.from(base.spawn));assert.deepEqual(JSON.parse(JSON.stringify(cs.walls)),JSON.parse(JSON.stringify(base.walls)));
 assert.equal(cs.objects.length,base.objects.length);assert.equal(cs.residents.length,base.residents.length);
 assert.equal(cs.objects.find(o=>o.type==='board').text,'11.11 CS CLASS');
 const pcs=cs.objects.filter(o=>o.action==='learning');assert.ok(pcs.every(o=>o.id.startsWith('cs_')));
 assert.ok(pcs.some(o=>o.name==='CS 개념 빈칸 채우기'));assert.ok(pcs.some(o=>o.name==='CS 객관식'));
 assert.equal(base.objects.find(o=>o.type==='board').text,'11.11 PYTHON CLASS');
 assert.throws(()=>c.window.CampusClassroomMap.forSubject('unknown'));
});

test('all three learning signs are centered on their left walls and remain passable',()=>{
 for(const roomId of ['class2','class-cs','class1']){
  const room=source.rooms.find(r=>r.id===roomId);
  const signs=source.objects.filter(o=>o.roomId===roomId&&o.type==='wall-sign');
  assert.equal(signs.length,1);
  const sign=signs[0];assert.equal(sign.text,room.name);assert.equal(sign.solid,false);
  assert.equal(sign.x,room.x+5);assert.equal(sign.w,3);assert.equal(sign.h,235.5);
  assert.equal(sign.y+sign.h/2,room.y+room.h/2);
  assert.ok(sign.y>room.y&&sign.y+sign.h<room.y+room.h);
 }
});
