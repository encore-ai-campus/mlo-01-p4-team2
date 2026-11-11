const fs=require('fs'),vm=require('vm'),path=require('path'),assert=require('node:assert/strict'),{test}=require('node:test');
test('rendered map is the standalone canonical event source',()=>{
 const root=path.resolve(__dirname,'..'),ctx=vm.createContext({window:{}});
 vm.runInContext(fs.readFileSync(path.join(root,'dist/event-map.js'),'utf8'),ctx);
 const source=JSON.parse(fs.readFileSync(path.join(root,'maps/pepero-worlds.json'),'utf8'));
 assert.deepEqual(JSON.parse(JSON.stringify(ctx.window.CampusWorlds)),source.worlds);
 const html=fs.readFileSync(path.join(root,'dist/index.html'),'utf8');
 assert.match(html, /src="event-map\.js(?:\?[^"\s]*)?"/);
 assert.ok(!/src="(?:map-data|rooftop-data|pepero-layout|season-map-switch)\.js/.test(html));
});
