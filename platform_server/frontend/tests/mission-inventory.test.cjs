const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../dist/main-quest.js'),'utf8');
function fixture(avatar='male-1',remaining=10){
 const player={avatar,peperoRemaining:remaining};
 const scope={room:0,actor:{},state:{canPractice:false},held:{clear(){}},practiceLabel:{hidden:true},canvas:{focus(){}},interior:{open:false,querySelector(){return {};},showModal(){this.open=true;},close(){this.open=false;}},dialog:{open:false},gameDialog:{open:false},missionMap:null,missionRenderer:null,nearStation:null,
 window:{CampusApp:{player},CampusMissionRooms:{create:n=>({name:'ROOM '+n})},CampusIsometric:{create:()=>({draw(){}})}}};
 vm.createContext(scope);vm.runInContext(source.slice(source.indexOf('function syncRoomInventory()'),source.indexOf("interior.querySelector('.mq-close')")),scope);
 return {scope,player,enter:n=>scope.applyMissionState({subject:'mission'+n,actor:{x:100,y:120,dir:'down',moving:false,running:false}})};
}
test('both mission rooms retain the held Pepero for every avatar on entry and re-entry',()=>{
 for(const avatar of ['male-1','male-2','female-1','female-2'])for(const room of [1,2]){
  const f=fixture(avatar);f.enter(room);assert.equal(f.scope.actor.peperoRemaining,10);assert.equal(f.scope.actor.avatar,avatar);
  f.scope.applyMissionState(null);f.enter(room);assert.equal(f.scope.actor.peperoRemaining,10);
 }
});
test('server inventory changes and an empty hand update the room actor without resetting its position',()=>{
 const f=fixture();f.enter(1);f.player.peperoRemaining=6;f.scope.syncRoomInventory();assert.equal(f.scope.actor.peperoRemaining,6);assert.equal(f.scope.actor.x,100);
 f.player.peperoRemaining=0;f.scope.syncRoomInventory();assert.equal(f.scope.actor.peperoRemaining,0);
 f.player.peperoRemaining=10;f.scope.applyMissionState({subject:'mission1',actor:{x:110,y:120,dir:'left',moving:true}});assert.equal(f.scope.actor.peperoRemaining,10);assert.equal(f.scope.actor.targetX,110);
});
