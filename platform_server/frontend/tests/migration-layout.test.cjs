const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const context=vm.createContext({window:{}});
for(const file of ['map-data.js','rooftop-data.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'../dist',file),'utf8'),context);
const {campus,rooftop}=context.window.CampusWorlds;
test('all compact booths retain matching interior interaction bounds',()=>{
 const booths=rooftop.objects.filter(o=>o.type==='exhibition-booth');assert.equal(booths.length,3);
 for(const booth of booths){const area=rooftop.objects.find(o=>o.id===booth.roomId+'-interaction');assert.ok(booth.w<200);assert.equal(area.x,booth.x+10);assert.equal(area.w,booth.w-20);assert.equal(area.h,booth.h-20);assert.equal(area.action,'booth');}
});
test('only the requested plaza return portal is removed',()=>{assert.ok(!rooftop.objects.some(o=>o.id==='roof-plaza-campus-portal'));assert.ok(rooftop.objects.some(o=>o.id==='roof-lounge2-portal'));});
test('guide collision and live wall advertisement survive migration',()=>{assert.ok(campus.objects.some(o=>o.id==='campus-guide-kkuwang'&&o.solid&&o.w===48));assert.ok(campus.objects.some(o=>o.id==='roof-ad-board'&&o.type==='wall-poster'&&o.source==='ad'));});
test('remote lounge table consolidation survives local map edits',()=>{const tables=campus.objects.filter(o=>o.type==='coffee');assert.equal(tables.length,1);assert.equal(tables[0].w,39);assert.equal(tables[0].h,72);});

test('rooftop lunch wheel has a collider and the overlay follows the same map object',()=>{
 const wheel=rooftop.objects.find(o=>o.id==='roof-lunch-wheel');assert.ok(wheel&&wheel.solid&&wheel.action==='lunch');
 const source=fs.readFileSync(path.join(__dirname,'../dist/campus-world-actions.js'),'utf8');assert.ok(source.includes("wheelObject.x+wheelObject.w/2"));
});
test('opened storage and relocated worktable clear the previous aisle',()=>{
 const scale=campus.spaceScale,shift=campus.layoutShiftX;
 assert.ok(!campus.walls.some(w=>w.y1===84*scale&&w.y2===84*scale&&w.x1>=1290*scale+shift&&w.x2<=1399*scale+shift));
 const table=campus.objects.find(o=>o.id==='lounge1-worktable');assert.equal(table.y,346*scale);
 assert.equal(campus.objects.filter(o=>o.workSeat).length,3);
});
