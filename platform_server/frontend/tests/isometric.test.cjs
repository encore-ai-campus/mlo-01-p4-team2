const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const sandbox=vm.createContext({window:{}});
for(const name of ['map-data.js','isometric.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'../dist',name),'utf8'),sandbox);
const D=sandbox.window.CampusData,P=sandbox.window.CampusIsometric.createProjection(D);
test('clockwise 45 degrees: east is down-right, south is down-left',()=>{
  assert.equal(P.angle,Math.PI/4);const east=P.vector(100,0),south=P.vector(0,100);
  assert.ok(east.x>0&&east.y>0&&south.x<0&&south.y>0);
  assert.ok(Math.abs(Math.abs(east.x)-Math.abs(south.x))<1e-9);
});
test('every room, entrance and map corner round-trips through the projection',()=>{
  for(const r of [...D.rooms,D.entrance,{x:0,y:0,w:D.width,h:D.height}])for(const [x,y] of [[r.x,r.y],[r.x+r.w,r.y+r.h],r.target||[r.x+r.w/2,r.y+r.h/2]]) {
    const q=P.project(x,y),b=P.unproject(q.x,q.y);assert.ok(Math.abs(b.x-x)<1e-8&&Math.abs(b.y-y)<1e-8);
    assert.ok(q.x>=0&&q.x<=P.width&&q.y>=0&&q.y<=P.height);
  }
});
test('screen arrow directions map back to the same visible directions',()=>{
  for(const [x,y] of [[1,0],[-1,0],[0,1],[0,-1],[1,1],[-1,1]]){
    const world=P.inverseVector(x,y),visible=P.vector(world.x,world.y);
    assert.ok(Math.abs(visible.x-x)<1e-8&&Math.abs(visible.y-y)<1e-8);
  }
});
test('enlarged map merges classroom 3/4 with only a mounted board, and keeps the vertical worktable',()=>{
  assert.equal(D.width,2448);assert.equal(D.height,1230);assert.equal(D.spaceScale,1.5);
  assert.deepEqual(Array.from(D.rooms.filter(r=>r.floor==='class'),r=>r.name).sort(),['Python 학습교실','SQL 학습교실','강의실 3·4','강의실 5','강의실 6','강의실 7']);
  const merged=D.rooms.find(r=>r.id==='class3-4');assert.equal(merged.w,916.5);assert.equal(merged.h,364.5);
  assert.equal(merged.label,false);assert.equal(merged.info,false);
  assert.equal(D.rooms.some(r=>r.id==='class3'||r.id==='class4'),false);
  assert.equal(D.objects.some(o=>o.roomId==='class3'||o.roomId==='class4'),false);
  const board=D.objects.find(o=>o.id==='roof-ad-board');assert.equal(board.roomId,merged.id);
  assert.equal(board.type,'wall-poster');assert.equal(board.source,'ad');assert.equal(board.solid,false);
  assert.equal(board.imageUrl,'');assert.equal(board.ledFrame,true);assert.equal(board.inFrontOfWalls,true);
  assert.ok(board.posterHeight>160&&board.posterBottom>=35);
  assert.equal(board.x+board.w/2,merged.x+merged.w/2);
  assert.equal(D.objects.some(o=>o.id==='class3-4-noticeboard'||o.type==='printer'),false);
  const dividerX=(769+72)*D.spaceScale;
  assert.ok(D.walls.some(w=>w.x1===dividerX&&w.x2===dividerX&&w.y1===369*D.spaceScale));
  const worktable=D.objects.find(o=>o.id==='lounge1-worktable');assert.equal(worktable.w,42);assert.equal(worktable.h,172.5);
  assert.equal(D.objects.filter(o=>o.workSeat).length,3);
  assert.equal(D.rooms.find(r=>r.id==='lounge1').target[0],1893);
});

// 실제 렌더러 생성·이미지 교체 경로를 실행한다. Canvas 그리기만 대체한다.
function displayRenderer(board){
  const drawing=new Proxy({measureText:()=>({width:10}),createLinearGradient:()=>({addColorStop(){}})},
    {get:(target,key)=>key in target?target[key]:()=>{}});
  const context=vm.createContext({window:{addEventListener(){}},document:{createElement:()=>({getContext:()=>drawing})}});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../dist/isometric.js'),'utf8'),context);
  return context.window.CampusIsometric.create({data:{id:'test',width:1000,height:500,rooms:[],walls:[],objects:[board],elevationAt:()=>20}});
}

test('이미지 없는 실제 광고판은 사전 요청 좌표만 제공하고 이미지 교체 후에도 고정 크기를 유지한다',()=>{
  const board={...D.objects.find(o=>o.id==='roof-ad-board')},r=displayRenderer(board);
  assert.equal(r.displayPolygon(board.id),null);
  const before=r.displayPolygon(board.id,{placeholder:true});
  assert.equal(before.length,4);assert.ok(before.every(p=>Number.isFinite(p.x)&&Number.isFinite(p.y)));
  assert.equal(before[3].y-before[0].y,board.posterHeight);
  const bottom=r.projection.project(board.x,board.y,20+board.posterBottom);
  assert.deepEqual({...before[3]},{...bottom});
  for(const image of [{width:100,height:400},{width:400,height:100}]){
    assert.equal(r.setDisplayImage(board.id,image),true);
    assert.deepEqual(r.displayPolygon(board.id),before);
  }
  assert.equal(r.displayPolygon('missing',{placeholder:true}),null);
});

test('크기 미지정 벽 광고는 로딩 전 추정 크기를 사용하고 로딩 후 실제 이미지 비율을 따른다',()=>{
  const r=displayRenderer({id:'board',type:'wall-poster',x:20,y:20,w:160,h:1,imageOnly:true});
  const before=r.displayPolygon('board',{placeholder:true});assert.equal(before[3].y-before[0].y,90);
  r.setDisplayImage('board',{width:100,height:200});
  const after=r.displayPolygon('board');assert.equal(after[3].y-after[0].y,320);
  assert.deepEqual(after[3],before[3]);
});

test('독립형 광고판은 지정된 화면 크기로 미리 계산하고 이미지 로딩 후 실제 비율을 사용한다',()=>{
  const r=displayRenderer({id:'board',type:'plaza-display',x:20,y:20,w:160,h:1,screenWidth:200,screenHeight:120,imageOnly:true,displayAxis:'y'});
  assert.equal(r.displayPolygon('board'),null);
  const before=r.displayPolygon('board',{placeholder:true});assert.ok(Math.abs(before[3].y-before[0].y-120)<1e-8);
  r.setDisplayImage('board',{width:200,height:300});
  const after=r.displayPolygon('board');assert.ok(Math.abs(after[3].y-after[0].y-300)<1e-8);
  assert.deepEqual(after[3],before[3]);
});
