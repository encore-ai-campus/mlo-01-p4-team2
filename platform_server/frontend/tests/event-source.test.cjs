const fs=require('fs'),vm=require('vm'),path=require('path'),assert=require('node:assert/strict'),{test}=require('node:test');
test('rendered map is the standalone canonical event source',()=>{
 const root=path.resolve(__dirname,'..'),ctx=vm.createContext({window:{}});
 vm.runInContext(fs.readFileSync(path.join(root,'dist/event-map.js'),'utf8'),ctx);
 const source=JSON.parse(fs.readFileSync(path.join(root,'maps/pepero-worlds.json'),'utf8'));
 const generated=JSON.parse(JSON.stringify(ctx.window.CampusWorlds));
 assert.deepEqual(generated,require('../tools/build-event-map.cjs').build());
 for(const world of Object.values(generated)){world.objects=world.objects.filter(o=>o.type!=='manager'&&o.action!=='minigame');if(!source.worlds[world.id].npcs)delete world.npcs;}
 assert.deepEqual(generated,source.worlds,'manager composition must preserve every original map object');
 const html=fs.readFileSync(path.join(root,'dist/index.html'),'utf8');
 assert.match(html, /src="event-map\.js(?:\?[^"\s]*)?"/);
 assert.ok(!/src="(?:map-data|rooftop-data|pepero-layout|season-map-switch)\.js/.test(html));
});
