/* Generate BOTH browser scenery and server colliders from the event source. */
const fs=require('fs'),path=require('path');
const root=path.resolve(__dirname,'..');
function build(){
  const source=JSON.parse(fs.readFileSync(path.join(root,'maps/pepero-worlds.json'),'utf8'));
  const games=JSON.parse(fs.readFileSync(path.join(root,'maps/minigames.json'),'utf8'));
  const ids=new Set();
  for(const game of games){
    const world=source.worlds[game.floor];
    if(!world||ids.has(game.id)||!['timer','typing'].includes(game.gameCode)||!game.interactionPoint||
       ![game.x,game.y,game.w,game.h,...game.interactionPoint].every(Number.isFinite))throw new Error('Invalid minigame: '+game.id);
    ids.add(game.id);
    world.objects.push({...game,type:'game-pc-station',action:'minigame',solid:true,tags:['E키로 게임 시작']});
  }
  return source.worlds;
}
function render(){return '/* Generated from frontend/maps/pepero-worlds.json and minigames.json. Do not hand edit. */\n'+
  'window.CAMPUS_SEASON="pepero";\nwindow.CampusWorlds='+JSON.stringify(build())+';\n'+
  'window.CampusData=window.CampusWorlds.campus;\n'+
  'for(const d of Object.values(window.CampusWorlds)){if(d.surfaces)d.elevationAt=(x,y)=>{const s=d.surfaces.find(s=>x>=s.x&&x<=s.x+s.w&&y>=s.y&&y<=s.y+s.h);return s?.z||0;};}\n';}
if(require.main===module){
  const mission=JSON.parse(fs.readFileSync(path.join(root,'../backend/event_story/mission_rooms.json'),'utf8'));
  const missionPath=path.join(root,'dist/mission-room-map.js');
  fs.writeFileSync(missionPath,fs.readFileSync(missionPath,'utf8').replace(/^const spec=.*;$/m,()=> 'const spec='+JSON.stringify(mission)+';'));
  const managers=mission.npcs.map(({id,floor,x,y,name,sprite})=>({id,floor,x,y,name,sprite}));
  const roomManager=mission.rooms.mission1.objects.find(o=>o.managerId);
  managers.push({id:roomManager.managerId,floor:'mission1',x:roomManager.x+roomManager.w/2,y:roomManager.y+roomManager.h/2,name:roomManager.name,sprite:roomManager.sprite,objectId:roomManager.id});
  fs.writeFileSync(path.join(root,'maps/managers.json'),JSON.stringify(managers,null,2)+'\n');
  fs.writeFileSync(path.join(root,'dist/event-map.js'),render());
  const exporter=require('./export-worlds.cjs');fs.writeFileSync(exporter.TARGET,exporter.render());
  console.log('Pepero scene and server collision data rebuilt from the same source.');
}
module.exports={build,render};
