const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const context=vm.createContext({window:{}});
for(const name of ['map-data.js','rooftop-data.js','stair-motion.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'../dist',name),'utf8'),context);
const {CampusWorlds:W,CampusStairs:S}=context.window;

test('only current campus and roof portals have destination labels',()=>{
 const up=W.campus.objects.find(o=>o.id==='campus-roof-stairs');assert.equal(up.portalGlow,true);
 assert.equal(W.rooftop.objects.some(o=>o.id==='roof-campus-stairs'||o.id==='roof-plaza-campus-portal'),false);
 const back=W.rooftop.objects.filter(o=>o.type==='floor-portal');assert.equal(back.length,1);assert.equal(back[0].id,'roof-lounge2-portal');assert.equal(back[0].portalLabel,'18F');
 assert.ok(W.campus.objects.some(o=>o.id==='campus-lounge2-portal'&&o.type==='floor-portal'));
});
test('paired portals use updated destinations and require E rather than walking triggers',()=>{
 const a=W.campus.objects.find(o=>o.id==='campus-lounge2-portal'),b=W.rooftop.objects.find(o=>o.id==='roof-lounge2-portal');
 assert.deepEqual(Array.from(a.arrival),[1110,1000]);assert.deepEqual(Array.from(b.arrival),[a.x+a.w/2,a.y+a.h/2]);
 assert.equal(a.confirmTransfer,false);assert.equal(b.confirmTransfer,false);assert.equal(W.rooftop.portals.length,0);assert.ok(!W.campus.portals.some(p=>p.objectId===a.id));
});
test('live advertisement is wall mounted with frame and no rooftop stand',()=>{
 const ad=W.campus.objects.find(o=>o.id==='roof-ad-board');assert.equal(ad.type,'wall-poster');assert.equal(ad.source,'ad');assert.equal(ad.imageUrl,'');assert.equal(ad.ledFrame,true);assert.equal(ad.solid,false);assert.equal(ad.inFrontOfWalls,true);
 assert.ok(!W.rooftop.objects.some(o=>o.id==='roof-ad-board'));
});
test('campus destination marker is on the lounge floor outside the physical stair flight',()=>{
 const up=W.campus.objects.find(o=>o.id==='campus-roof-stairs'),[x,y]=up.portalAnchor;
 assert.deepEqual(Array.from(up.interactionPoint),[x,y]);assert.equal(S.heightAt(W.campus,x,y),0);
 assert.ok(y<up.y);assert.ok(W.campus.rooms.some(r=>x>=r.x&&x<=r.x+r.w&&y>=r.y&&y<=r.y+r.h));
});
test('stairs retain their trigger and arrive on a safe flat roof landing',()=>{
 const up=W.campus.objects.find(o=>o.id==='campus-roof-stairs'),trigger=W.campus.portals.find(p=>p.objectId===up.id);
 assert.deepEqual(Array.from(up.arrival),[1760,880]);assert.equal(up.solid,false);
 assert.ok(up.approach[0]>=trigger.x&&up.approach[0]<=trigger.x+trigger.w);assert.ok(up.approach[1]>=trigger.y&&up.approach[1]<=trigger.y+trigger.h);
 assert.equal(S.heightAt(W.rooftop,...up.arrival),0);assert.equal(S.railLength(W.campus,up,'left'),0);
});
