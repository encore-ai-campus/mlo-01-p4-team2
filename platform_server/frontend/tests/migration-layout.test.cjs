const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const context=vm.createContext({window:{}});
for(const file of ['map-data.js','rooftop-data.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'../dist',file),'utf8'),context);
const {campus,rooftop}=context.window.CampusWorlds;
test('all compact booths retain matching interior interaction bounds',()=>{
 const booths=rooftop.objects.filter(o=>o.type==='exhibition-booth');assert.equal(booths.length,3);
 for(const booth of booths){const area=rooftop.objects.find(o=>o.id===booth.roomId+'-interaction');assert.ok(booth.w<200);assert.equal(area.x,booth.x+10);assert.equal(area.w,booth.w-20);assert.equal(area.h,booth.h-20);assert.equal(area.action,'booth');}
});
test('only the requested plaza return portal is removed',()=>{assert.ok(!rooftop.objects.some(o=>o.id==='roof-plaza-campus-portal'));assert.ok(rooftop.objects.some(o=>o.id==='roof-lounge2-portal'));});
test('guide collision and mounted PLAYDATA board survive migration',()=>{assert.ok(campus.objects.some(o=>o.id==='campus-guide-kkuwang'&&o.solid&&o.w===48));assert.ok(campus.objects.some(o=>o.id==='class3-4-noticeboard'&&o.mounted));});
test('remote lounge table consolidation survives local map edits',()=>{const tables=campus.objects.filter(o=>o.type==='coffee');assert.equal(tables.length,1);assert.equal(tables[0].w,39);assert.equal(tables[0].h,72);});
