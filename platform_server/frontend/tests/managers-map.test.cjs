const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),test=require('node:test'),crypto=require('node:crypto');
const root=path.resolve(__dirname,'..'),managers=require('../maps/managers.json');
const worlds=require('../tools/build-event-map.cjs').build(),server=require('../../backend/campus/data/worlds.json').worlds;
test('six identities reuse incoming manager locations and reference-based sprite files',()=>{
 const spec=JSON.parse(fs.readFileSync(root+'/../backend/event_story/mission_rooms.json'));
 assert.equal(managers.length,6);assert.equal(new Set(managers.map(m=>m.id)).size,6);
 for(const [id,floor,x,y,sprite] of [['manager-suhyeon','campus',2090,818,'suhyeon'],['manager-yebin','rooftop',850,650,'yebin'],['manager-yeongseok','mission1',355,162,'yeongseok']]){
  const manager=managers.find(m=>m.id===id);assert.deepEqual([manager.floor,manager.x,manager.y,manager.sprite],[floor,x,y,sprite]);
 }
 for(const name of ['songhee','huiae','jeonghun']){
  const npc=spec.npcs.find(n=>n.sprite===name);assert.ok(npc);assert.ok(fs.statSync(root+'/dist/assets/characters/manager-'+name+'-idle-v1.png').size>0);
 }
 assert.ok(!managers.some(m=>m.id==='manager-yuhyeong'));
 assert.ok(!spec.npcs.some(n=>n.id==='manager-yuhyeong'));
 assert.ok(!fs.readFileSync(root+'/dist/mission-room-map.js','utf8').includes('yuhyeong'));
 const station=spec.rooms.mission1.objects.find(o=>o.id==='sql_answer');assert.equal(station.managerId,'manager-yeongseok');assert.equal(station.action,'manager_greet');
});
test('achievement PNGs are copied byte-for-byte from the supplied original images',()=>{
 for(const [name,originalHash] of [['manager-greeting.png','719a823f42802071cb542961efddc0bde1921500c5773a69aae718336502bd80'],
  ['manager-poking.png','549bfc38119bdaa5822ef1db6cdf5ad2037dc9f2449f43e85ff372b4385fd980']]){
  const copied=fs.readFileSync(path.join(root,'dist/assets/achievements',name));
  assert.equal(crypto.createHash('sha256').update(copied).digest('hex'),originalHash);
 }
});

test('the two new learning badge assets preserve the supplied PNG bytes',()=>{
 for(const [name,hash] of [['all-subjects.png','f2094d9cb7a2c046816692758c0b77af5ab9ec600cc2723ec17ad1a1d53d9b7c'],['cs-intermediate.png','804a790e56a58698219145f3b6140dcc9aac7c60e2f3bee0950e0a3ee396b631']])
  assert.equal(crypto.createHash('sha256').update(fs.readFileSync(path.join(root,'dist/assets/achievements',name))).digest('hex'),hash);
});
