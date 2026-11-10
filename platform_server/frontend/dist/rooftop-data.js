/* Rooftop plaza: centered exhibition row, fountain court and garden seating. */
(() => {
  'use strict';
  const campus=window.CampusData;
  campus.id='campus';campus.name='18F 캠퍼스';campus.spawn=[campus.entrance.x+campus.entrance.w/2,campus.entrance.y-36];
  const stairs=campus.objects.find(o=>o.type==='stairs');
  Object.assign(stairs,{x:campus.entrance.x,y:635*campus.spaceScale,w:campus.entrance.w,h:68*campus.spaceScale});
  Object.assign(stairs,{id:'campus-roof-stairs',name:'옥상으로 올라가기',roomId:'lounge1',toFloor:'rooftop',stairDirection:'up',stairSteps:6,stairRise:5.5,stairRun:stairs.h,stairEntrySide:'left',stairRails:false,portalGlow:true,portalLabel:'옥상이동',portalScale:1.5,renderHeight:100,arrival:[1760,880],approach:[stairs.x+stairs.w/2,stairs.y+stairs.h*.12],description:'라운지 쪽 옆문으로 평평한 발판에 들어와 계단 위쪽으로 걸어 올라가거나 가까이에서 E를 누르면 옥상 광장으로 이동합니다.',tags:['옥상 연결','유튜브 · 심리 테스트 · 게임 부스']});
  stairs.portalAnchor=[1250*campus.spaceScale+campus.layoutShiftX,480*campus.spaceScale];
  stairs.interactionPoint=stairs.portalAnchor.slice();
  const extinguisher=campus.objects.find(o=>o.id==='campus-lounge2-portal');
  const lounge2=campus.rooms.find(r=>r.id===extinguisher.roomId);
  const stairRoom=campus.rooms.find(r=>r.id==='stairs');
  Object.assign(stairRoom,{labelX:stairs.x+stairs.w*.13,labelY:stairs.y+stairs.h*.86});
  // This shortcut requires E near the extinguisher; walking past it never transfers.
  Object.assign(extinguisher,{portalGlow:true,portalRadius:18,portalFloorClip:{x:lounge2.x+6,y:lounge2.y+6,w:lounge2.w-12,h:lounge2.h-12},renderHeight:80,toFloor:'rooftop',arrival:[1110,1000],confirmTransfer:false,description:'가까이에서 E를 누르면 옥상 왼편 포탈로 이동할지 확인합니다. 예를 누르면 이동합니다.'});
  campus.portals=[{x:stairs.x,y:stairs.y,w:stairs.w,h:stairs.h*.24,objectId:stairs.id}];
  const width=2448,height=1800,rooms=[],objects=[],walls=[];
  const rearEdge=411.5; // Preserve rear clearance as booth backs move to x=677.
  const bounds={x:rearEdge,y:64,w:2384-rearEdge,h:1672};
  const terrace={x:1179,y:64,w:225,h:1672,axis:'x',levels:5,rise:12,lanes:[[140,285],[900,1240]]};
  const step=terrace.w/terrace.levels,top=terrace.levels*terrace.rise,lowerX=terrace.x+terrace.w;
  const elevationAt=(x,y)=>x<bounds.x||x>bounds.x+bounds.w||y<bounds.y||y>bounds.y+bounds.h?0:x<terrace.x?top:x<lowerX?(terrace.levels-Math.floor((x-terrace.x)/step))*terrace.rise:0;
  const surfaces=[{x:rearEdge,y:64,w:terrace.x-rearEdge,h:1672,z:top,floor:'roof'}];
  for(let i=0;i<terrace.levels;i++)surfaces.push({x:terrace.x+i*step,y:64,w:step,h:1672,z:(terrace.levels-i)*terrace.rise,floor:'roof',stair:true});
  surfaces.push({x:lowerX,y:64,w:2384-lowerX,h:1672,z:0,floor:'roof'});
  const room=(id,name,x,y,w,h,target,extra={})=>rooms.push({id,name,x,y,w,h,target,floor:'roof',...extra});
  room('roof-plaza','분수 광장',rearEdge,64,2384-rearEdge,1672,[1680,1110],{label:false,description:'유튜브·심리 테스트·게임 부스 세 개를 바닥의 긴 방향을 따라 나란히 배치한 광장입니다. 분수 둘레의 원형 포장과 산책로, 벤치·꽃 화단을 갖춘 휴게 광장입니다. 입구 왼편의 광고·행사 게시판을 확인할 수 있습니다.'});
  room('roof-upper','위쪽 휴게 광장',rearEdge+26,100,1010-rearEdge-26,1510,[rearEdge+286,950],{labelX:300,labelY:970,description:'옥상 중앙 계단 위의 휴게 광장입니다. 아래쪽 광장과 길이를 맞췄고 유튜브·심리 테스트·게임 부스를 배치했습니다. 오른쪽 입구로 부스 안에 들어갈 수 있습니다.',tags:['휴게 광장','중앙 계단 연결']});
  room('roof-terrace','중앙 계단형 쉼터',terrace.x,64,terrace.w,1672,[1224,950],{labelX:1160,labelY:590,description:'맵의 1/2 지점에 세로로 길게 놓은 다섯 단 쉼터입니다. 낮은 단과 난간을 따라 두 계단 통로로 오갈 수 있습니다.',tags:['중앙 1/2','세로 배치','낮은 5단 계단']});
  room('festival-youtube','01 유튜브 스튜디오',1425,175,310,400,[1565,530],{labelX:1570,labelY:552,description:'영상 제작을 테마로 꾸민 열린 부스입니다. 영상 화면과 재생 버튼 기념패를 배치했습니다.',tags:['SECTION 01','YouTube','촬영 · 영상 편집']});
  room('festival-instagram','02 심리테스트',1740,175,310,400,[1880,530],{labelX:1885,labelY:552,description:'성격과 기분을 알아보는 심리 테스트 공간입니다. 심리테스트 안내 보드를 두고 부스 안에서 E를 누르면 심심테스트가 열립니다.',tags:['SECTION 02','심리 테스트','성격 · 감정 유형']});
  room('festival-booth3','03 게임 부스',2055,175,310,400,[2195,530],{labelX:2200,labelY:552,description:'아케이드 기기, 닌텐도 휴대용 콘솔과 스팀 게이밍 PC로 꾸민 게임 부스입니다. 중앙에서 E를 누르면 오목·테트리스·죽림고수·팔라독·솔리테어 등 16가지 게임 카드가 열리고, 선택한 게임을 같은 팝업에서 플레이할 수 있습니다.',tags:['SECTION 03','Nintendo · Steam','아케이드 · 게이밍 PC','오목 · 전쟁시대 · 아빠와 나 · 지뢰찾기']});
  const obj=(type,x,y,w,h,extra={})=>{const o={id:'roof-object-'+objects.length,type,x,y,w,h,...extra};objects.push(o);return o;};
  const tree=(x,y)=>obj('tree',x,y,42,42,{solid:true,renderHeight:105});
  const planter=(x,y,w=100)=>obj('planter',x,y,w,30);
  // The same rear wall runs over both landings and every stair tier.
  walls.push({x1:rearEdge,y1:64,x2:2384,y2:64,height:420,topZ:420},
    {x1:2384,y1:64,x2:2384,y2:1736},
    {x1:rearEdge,y1:1736,x2:2384,y2:1736},{x1:rearEdge,y1:64,x2:rearEdge,y2:1736});
  for(const y of [130,750,1320])tree(2300,y===1320?1540:y);
  for(let i=0;i<terrace.levels;i++){
    const x=terrace.x+i*step;
    for(const [y,h] of [[310,240],[670,210],[1400,240]])obj('terrace-bench',x+11,y,22,h);
    for(const y of [590,1310]){
      const treeIndex=[[0,590],[2,1310],[4,590]].findIndex(([level,row])=>level===i&&row===y);
      if(treeIndex<0)obj('planter',x+10,y,24,74,{naturalPlanting:true,plantingVariant:i+(y===1310?2:0)});
      if(treeIndex>=0)Object.assign(tree(x+2,y+15),{treeScale:[.8,.85,.82][treeIndex],canopyVariant:treeIndex,renderHeight:[84,89.25,86.1][treeIndex],terraceTree:true});
    }
  }
  for(const lane of terrace.lanes)for(const y of lane)if(y!==140)obj('stair-handrail',terrace.x,y,terrace.w,6,{axis:'x',solid:false,renderHeight:135});
  const boothObjectStart=objects.length;
  function booth(x,roomId,platform,color,title){
    const y=200,w=280,h=300,r=rooms.find(r=>r.id===roomId);
    obj('rug',x,y,w,h,{solid:false,color:platform==='youtube'?'#f8e6e6':platform==='psych'?'#eee9f8':platform==='game'?'#e0e7f6':'#ebe9dc'});
    const backdrop=obj('exhibition-booth',x,y,w,h,{name:r.name,roomId,platform,title,color,entrance:'bottom',solid:false,collision:{x:0,y:0,w,h:10},renderHeight:175,description:r.description,tags:r.tags});
    obj('booth-collider',x,y,w,10,{solid:true});
    // The side panels stand in front of the backdrop they touch, so someone between them is drawn in the right layer.
    for(const sx of [x,x+w-10])obj('booth-side',sx,y,10,h,{solid:true,color,renderHeight:70,occludes:[backdrop.id]});
  }
  booth(1440,'festival-youtube','youtube','#ed383b','YouTube');
  booth(1755,'festival-instagram','psych','#8171b6','심리테스트');
  booth(2070,'festival-booth3','game','#637db9','GAME ARCADE');
  // Keep a clear route from the open entrance to the central E marker.
  obj('game-brand-sign',2079,206,262,9,{id:'game-brand-sign',solid:false,renderHeight:160});
  const boothObjects=objects.slice(boothObjectStart);
  // Walkable paving connects the fountain court, seating and the front entrance.
  obj('plaza-paving',1930,260,96,1300,{style:'walkway',solid:false});
  obj('plaza-paving',2030,210,245,300,{style:'patio',solid:false});
  const garden=(x,y,w,h)=>obj('planter',x,y,w,h,{flowers:true,renderHeight:55});
  const seat=(x,y,w,h,backSide='top')=>obj('terrace-bench',x,y,w,h,{backrest:true,backSide,renderHeight:65});
  const lamp=(x,y)=>obj('plaza-lamp',x,y,18,18,{solid:true,collision:{x:6,y:6,w:6,h:6},renderHeight:150});
  // Leave the stair lanes and the paths around the fountain completely open.
  seat(1550,575,210,34);garden(1545,500,220,42);lamp(1495,560);
  seat(1550,1260,210,34,'bottom');garden(1545,1325,220,42);lamp(1495,1305);
  seat(2090,735,34,220,'right');garden(2160,725,42,240);lamp(2095,665);
  seat(2070,250,180,34);seat(2240,340,34,150,'right');
  garden(2060,165,200,40);
  garden(2210,1175,42,210);seat(2140,1190,34,180,'right');lamp(2180,1135);
  const campusReturn=[stairs.portalAnchor[0]+48*campus.spaceScale,stairs.portalAnchor[1]-32*campus.spaceScale];
  // Align booth backs on the long floor axis; projection supplies the 45-degree view.
  // Rotate the complete local
  // layout 90 degrees so the open entrances face right, including all colliders.
  const boothLayout=[['festival-youtube',1440,677,300],['festival-instagram',1755,677,760],['festival-booth3',2070,677,1220]];
  for(const [roomId,oldX,newX,newY] of boothLayout){
    const r=rooms.find(r=>r.id===roomId);
    Object.assign(r,{x:newX,y:newY-10,w:194,h:320,target:[newX+190,newY+140],labelX:newX+154,labelY:newY+315});
    for(const o of boothObjects.filter(o=>o.x>=oldX&&o.x+o.w<=oldX+280&&o.y>=200&&o.y+o.h<=500)){
      const local={x:o.x-oldX,y:(o.y-200)*.58,w:o.w,h:o.h*.58,originX:newX,originY:newY,span:280};
      Object.assign(o,{x:newX+local.y,y:newY+280-local.x-local.w,w:local.h,h:local.w,drawing:local,rotation:90,roomId});
      if(o.type==='exhibition-booth'){o.entrance='right';o.collision={x:0,y:0,w:9,h:280};}
    }
  }
  for(const shell of objects.filter(o=>o.type==='exhibition-booth')){
    const cx=shell.x+shell.w/2,cy=shell.y+shell.h/2;
    obj('booth-interaction',shell.x+10,shell.y+10,shell.w-20,shell.h-20,{id:shell.roomId+'-interaction',name:shell.platform==='youtube'?'유튜브 부스 안내':shell.platform==='psych'?'심리테스트 안내':shell.platform==='game'?'게임 선택':'기본 부스 안내',roomId:shell.roomId,platform:shell.platform,action:'booth',solid:false,renderHeight:0});
  }
  const shortcut=obj('floor-portal',1070,960,80,80,{id:'roof-lounge2-portal',name:'18F 라운지 2로 이동',roomId:'roof-upper',toFloor:'campus',arrival:[extinguisher.x+extinguisher.w/2,extinguisher.y+extinguisher.h/2],approach:[1110,1000],confirmTransfer:false,solid:false,portalStyle:'compact',portalScale:1.5,portalRadius:12,portalLabel:'18F',renderHeight:80,description:'18층 통합 강의실과 연결된 포탈입니다. 가까이에서 E를 누르면 18층 라운지 2로 이동합니다.',tags:['18층 라운지 2 연결','E키 이동']});
  for(const [i,o] of objects.filter(o=>o.type==='balloons').entries()){const [, ,x,y]=boothLayout[i];Object.assign(o,{x:x+285,y:y+170});}
  for(let i=objects.length-1;i>=0;i--)if(['laptop','keyboard','monitor'].includes(objects[i].type))objects.splice(i,1);
  obj('guide-npc',1794,798,52,44,{id:'roof-lunch-wheel',name:'점심추천 돌림판',action:'lunch',roomId:'roof-plaza',solid:true,renderHeight:0});
  const rooftop={id:'rooftop',name:'RF 옥상 광장',width,height,padding:140,paddingTop:440,rooms,objects,walls,surfaces,terrace,elevationAt,wallHeight:18,wallColor:'#e8eee8',spawn:[1760,880],portals:[]};
  window.CampusWorlds={campus,rooftop};
})();
