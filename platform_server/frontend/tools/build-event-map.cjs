/* Generate BOTH browser scenery and server colliders from the event source. */
const fs=require('fs'),path=require('path');
const root=path.resolve(__dirname,'..');
const source=JSON.parse(fs.readFileSync(path.join(root,'maps/pepero-worlds.json'),'utf8'));
const js='/* Generated from frontend/maps/pepero-worlds.json. Do not hand edit. */\n'+
  'window.CAMPUS_SEASON="pepero";\nwindow.CampusWorlds='+JSON.stringify(source.worlds)+';\n'+
  'window.CampusData=window.CampusWorlds.campus;\n'+
  'for(const d of Object.values(window.CampusWorlds)){if(d.surfaces)d.elevationAt=(x,y)=>{const s=d.surfaces.find(s=>x>=s.x&&x<=s.x+s.w&&y>=s.y&&y<=s.y+s.h);return s?.z||0;};}\n';
fs.writeFileSync(path.join(root,'dist/event-map.js'),js);
const exporter=require('./export-worlds.cjs');fs.writeFileSync(exporter.TARGET,exporter.render());
console.log('Pepero scene and server collision data rebuilt from the same source.');
