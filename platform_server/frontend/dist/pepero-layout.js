/* 11.11 reference layout. Event-only; functional IDs and floor portals are retained. */
(() => {
  'use strict';
  if(window.CAMPUS_SEASON!=='pepero')return;
  const {campus:C,rooftop:R}=window.CampusWorlds;let seq=0;
  const add=(d,type,x,y,w,h,extra={})=>{const o={id:`reference-${d.id}-${seq++}`,type,x,y,w,h,solid:false,renderHeight:160,...extra};d.objects.push(o);return o;};
  const art=(d,key,x,y,w,h,pixels,extra={})=>add(d,'pepero-art',x,y,w,h,{seasonalArt:key,artWidth:pixels,artHeight:pixels*1.25,renderHeight:pixels*1.25,artAnchor:.92,solid:true,...extra});
  const flowers=(d,x,y,span=115,axis='x')=>art(d,'flowerbox',x,y,axis==='x'?span:24,axis==='x'?24:span,span*1.12,{flip:axis==='y',solid:true});
  const pedestal=(d,x,y)=>art(d,'pedestal',x,y,46,46,118);
  const table=(d,x,y,w=170)=>art(d,'workbench',x,y,w,66,w*1.25,{renderHeight:235});
  const rug=(d,x,y,w,h)=>add(d,'rug',x,y,w,h,{color:'#b9667b'});
  const lamp=(d,x,y)=>add(d,'pepero-ribbon-lamp',x,y,12,12,{renderHeight:145});
  const sign=(d,x,y,w,label,extra={})=>add(d,'pepero-reference-sign',x,y,w,8,{label,renderHeight:150,...extra});
  const trim=(d,x,y,w,h,axis='x')=>add(d,'pepero-light-rail',x,y,w,h,{axis,height:62,renderHeight:95});
  C.objects=C.objects.filter(o=>['stairs','fire-extinguisher','glass-wall','sliding-door'].includes(o.type)||o.id==='class3-4-noticeboard'||o.action==='learning');
  C.seasonalReference=true;R.seasonalReference=true;
  const titles={'left-top':['PEPERO CLASS','빼빼로 클래스'],'left-middle':['SWEET WORKSHOP','스위트 워크숍'],'left-bottom':['CHOCOLATE ATELIER','초콜릿 아틀리에'],class1:['PEPERO CLASS','Python 학습교실'],class2:['CHOCOLATE ATELIER','SQL 학습교실']};
  for(const [id,[title,name]] of Object.entries(titles)){
    const r=C.rooms.find(r=>r.id===id),{x,y,w,h}=r;r.name=name;r.photo=false;r.description='빼빼로 데이 '+name;r.label=false;
    rug(C,x+15,y+20,w-30,h-40);
    sign(C,x+25,y+16,w-50,title);
    // Two long worktables with stools, flanked by compact gift displays.
    table(C,x+w*.5-85,y+100,170);table(C,x+w*.5-85,y+h-125,170);
    for(const [dx,dy] of [[25,68],[w-71,68],[25,h-86],[w-71,h-86]])pedestal(C,x+dx,y+dy);
    flowers(C,x+18,y+28,72);flowers(C,x+w-100,y+28,72);
    lamp(C,x+15,y+90);lamp(C,x+w-27,y+90);
    trim(C,x+15,y+h-15,w-30,4);
    const anchors=C.objects.filter(o=>o.roomId===id&&o.action==='learning');
    anchors.forEach((o,i)=>Object.assign(o,{type:'pepero-art',seasonalArt:'pedestal',x:x+20+i*(w-70),y:y+h-54,w:45,h:35,artWidth:70,artHeight:95,artAnchor:.92,renderHeight:110,solid:false}));
    // Heart-framed wall panels are behind the furniture, not floating labels.
    for(const dx of [w*.28,w*.62])add(C,'pepero-heart-panel',x+dx,y+25,70,4,{mounted:true,renderHeight:100});
  }
  const H=C.rooms.find(r=>r.id==='class3-4');H.name='11.11 시그니처 홀';H.photo=false;
  rug(C,H.x+22,H.y+30,H.w-44,H.h-50);
  art(C,'hero',H.x+H.w*.5-85,H.y+135,170,115,420,{renderHeight:480});
  for(const dx of [30,H.w-88])for(const dy of [75,H.h-94])pedestal(C,H.x+dx,H.y+dy);
  for(const dx of [32,H.w-176])flowers(C,H.x+dx,H.y+H.h-32,130);
  trim(C,H.x+16,H.y+H.h-7,H.w-32,4);
  add(C,'pepero-garland',H.x+20,H.y+14,H.w-40,3,{height:153,renderHeight:180});
  const board=C.objects.find(o=>o.id==='class3-4-noticeboard');board.referenceBoard=true;
  const L=C.rooms.find(r=>r.id==='lounge1');L.name='기프트 마켓';L.photo=false;
  sign(C,L.x+35,L.y+28,L.w-70,'PEPERO MARKET');
  for(const dx of [42,264])art(C,'stall',L.x+dx,L.y+115,170,90,320,{stallLabel:'GIFT MARKET',renderHeight:300});
  for(const dx of [55,282])pedestal(C,L.x+dx,L.y+290);
  for(const yy of [355,560]){flowers(C,L.x+22,L.y+yy,110,'y');lamp(C,L.x+42,L.y+yy+120);}
  // Right wall salon mirrors the reference's two pink seats and gifts.
  for(const yy of [410,570]){
    add(C,'sofa',L.x+L.w-95,L.y+yy,70,35,{color:'#e49aa9',solid:true,renderHeight:65});
    pedestal(C,L.x+L.w-66,L.y+yy-70);
  }
  art(C,'cafe',L.x+175,L.y+480,115,100,170);
  const A=C.rooms.find(r=>r.id==='lounge2');A.name='스위트 갤러리';A.photo=false;
  for(const yy of [90,330,580]){pedestal(C,A.x+30,A.y+yy);pedestal(C,A.x+175,A.y+yy+30);lamp(C,A.x+12,A.y+yy+65);}
  for(const id of ['office','meeting']){const r=C.rooms.find(r=>r.id===id);pedestal(C,r.x+35,r.y+45);flowers(C,r.x+20,r.y+r.h-45,r.w-40);}
  for(const r of C.rooms.filter(r=>r.floor==='class')){
    for(let x=r.x+25;x<r.x+r.w-60;x+=110)add(C,'pepero-heart-panel',x,r.y+r.h-8,66,4,{mounted:true,renderHeight:86,low:true});
  }
  const E=C.entrance;
  art(C,'gate',E.x-18,E.y+5,E.w+36,15,230,{solid:false,renderHeight:270});
  rug(C,E.x-5,E.y+15,E.w+10,E.h-20);
  for(const dx of [-42,E.w+8]){lamp(C,E.x+dx,E.y-24);flowers(C,E.x+dx-15,E.y+E.h-25,65);}
  // Roof: three decorated activity counters on the upper deck, three gift stalls opposite.
  const keep=new Set(['stairs','floor-portal','booth-interaction','plaza-display']);
  R.objects=R.objects.filter(o=>keep.has(o.type));R.wallColor='#f2d7b1';
  R.rooms.find(r=>r.id==='roof-plaza').name='빼빼로 데이 광장';
  R.rooms.find(r=>r.id==='roof-upper').name='초콜릿 워크숍';
  const boothNames=['DIPPING','DECORATE','CHOCOLATE LAB'];
  for(const [i,id] of ['festival-youtube','festival-instagram','festival-booth3'].entries()){
    const room=R.rooms.find(r=>r.id===id),y=room.y+10;
    art(R,'stall',425,y+20,150,260,430,{flip:true,stallLabel:boothNames[i],renderHeight:350});
    const anchor=R.objects.find(o=>o.roomId===id&&o.action==='booth');
    Object.assign(anchor,{x:680,y:y+125,interactionPoint:[692,y+137]});room.target=[710,y+137];
    flowers(R,400,y+5,160,'y');pedestal(R,720,y+45);pedestal(R,720,y+245);
    lamp(R,760,y+145);
  }
  for(const [i,x] of [1480,1785,2090].entries()){
    art(R,'stall',x,240,205,115,380,{stallLabel:['GIFT MARKET','WRAPPING','SWEET GIFTS'][i],renderHeight:330});
    pedestal(R,x+95,410);flowers(R,x,195,170);
  }
  const roofTitles=['PEPERO DAY   11.11','A LITTLE SWEETER TOGETHER'];
  for(const [i,x] of [400,1400].entries())sign(R,x,70,840,roofTitles[i],{wallPanel:true,mounted:true,renderHeight:435});
  for(let x=85;x<2350;x+=205)add(R,'pepero-chocolate-column',x,76,18,18,{renderHeight:420,height:350});
  // Flower strips along alternating stair edges leave the two circulation lanes open.
  for(const y of [320,690,1060,1430])for(const x of [1055,1100,1145,1190,1235,1280,1325,1370])flowers(R,x,y,43);
  for(const y of [330,700,1070,1440])trim(R,1048,y-15,342,4);
  // The three stair runs are broad and legible; inset lights follow tread edges.
  for(const lane of R.terrace.lanes)for(const y of lane)add(R,'stair-handrail',1044,y,360,6,{axis:'x',solid:false,renderHeight:135});
  rug(R,1485,725,480,420);
  art(R,'hero',1615,845,190,130,490,{renderHeight:570});
  for(const [x,y] of [[1485,740],[1830,740],[1485,1110],[1830,1110]])flowers(R,x,y,130);
  for(const [x,y] of [[1480,885],[1910,885]])pedestal(R,x,y);
  for(const [x,y] of [[190,390],[190,1050],[2140,900],[2140,1310]])art(R,'cafe',x,y,130,115,235);
  for(let y=160;y<1500;y+=180){flowers(R,95,y,125,'y');lamp(R,96,y+130);}
  for(let y=500;y<1540;y+=235)flowers(R,2310,y,160,'y');
  for(let x=280;x<2000;x+=240)flowers(R,x,1690,170);
  const roofBoard=R.objects.find(o=>o.id==='roof-ad-board');Object.assign(roofBoard,{x:1530,y:1460,h:150});
  // Event entry placed beside the active stairs, with the arrival square kept clear.
  art(R,'gate',1980,1535,180,30,350,{solid:false,renderHeight:315});
  for(const [x,y] of [[1940,1500],[2185,1500],[1495,1240],[1920,1240]])lamp(R,x,y);
  for(const x of [160,390,620,850])add(R,'pepero-garland',x,100,230,3,{height:190,renderHeight:220});
})();
