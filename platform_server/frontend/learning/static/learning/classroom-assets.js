/* Procedural classroom furniture. Geometry uses the campus renderer's projection and depth cache. */
(() => {
 'use strict';
 function paint(o,api,z=0){
  if(!o.classroomAsset)return false;
  const {g,p,plane,box,line,ellipse,verticalPanel,shadow}=api,{x,y,w,h,type}=o;
  const timber=['#ffe9cf','#c99479','#754c4a'],metal=['#d1a777','#996d57','#614448'];
  if(!['desk','monitor','laptop','door-threshold'].includes(type))shadow(o,z);
  if(type==='desk'){
   for(const xx of [x+5,x+w-8])for(const yy of [y+4,y+h-7])box(xx,yy,3,3,z,17,...metal);
   box(x+5,y+h-8,w-10,3,z+9,3,...metal);
   box(x,y,w,h,z+17,4,...timber);
   plane(x+2,y+2,w-4,3,z+21.3,'#d78b9f',null);
   // Small chocolate gift stays in the free corner of every desk, clear of the PC.
   const gx=x+w-15,gy=y+4;
   box(gx,gy,10,10,z+21,5,'#f5a6b9','#be6983','#86495d');
   plane(gx+4,gy,2,10,z+26.3,'#ffe0a1',null);
   plane(gx,gy+4,10,2,z+26.4,'#ffe0a1',null);
   for(let i=0;i<3;i++)line(p(x+7+i*3,y+h-8,z+21.3),p(x+7+i*3,y+h-17,z+21.3),i===1?'#e98fa6':'#6a3932',2.3);

  }else if(type==='chair'){
   for(const xx of [x+3,x+w-5])for(const yy of [y+3,y+h-5])box(xx,yy,2,2,z,12,...metal);
   box(x,y,w,h,z+12,4,'#dca7aa','#bd7585','#804653');
   box(x+1,y+h-5,w-2,4,z+16,17,'#efc1be','#cb8899','#9a5c72');
   plane(x+3,y+3,w-6,h-9,z+16.4,'#f4d2c7',null);
  }else if(type==='monitor'){
   plane(x+w*.32,y+h*.36,w*.36,h*.24,z+1,'#617b79','#425d5a');
   box(x+w*.45,y+h*.25,4,4,z+1,7,...metal);
   verticalPanel(x,y+3,w,22,z+7,'#432932');
   const color=o.contentType==='fill_blank'?'#654056':'#704554';
   verticalPanel(x+2,y+3,w-4,18,z+9,color);
   for(let i=0;i<4;i++)line(p(x+5,y+3,z+23-i*3),p(x+w-5-i*3,y+3,z+23-i*3),'#bce7d0',.8);
   // Keep every key inside the keyboard case; leave a clear gap to the mouse.
   const keyboardX=x-2,keyboardY=y+h-7,keyboardW=w+2,keyboardH=7;
   plane(keyboardX,keyboardY,keyboardW,keyboardH,z+1.3,'#536965','#304941');
   const gap=1,keyW=(keyboardW-4-7*gap)/8;
   for(let row=0;row<2;row++)for(let col=0;col<8;col++)
    plane(keyboardX+2+col*(keyW+gap),keyboardY+1+row*2.5,keyW,1.5,z+1.6,'#c3d5cd',null);
   plane(keyboardX+8,keyboardY+5,keyboardW-16,1,z+1.6,'#c3d5cd',null);
   const mouseX=keyboardX+keyboardW+4;
   plane(mouseX,keyboardY+.5,4,6,z+1.3,'#8b9f96','#526c63');
   line(p(mouseX+2,keyboardY+1,z+1.6),p(mouseX+2,keyboardY+2.5,z+1.6),'#526c63',.6);
  }else if(type==='board'){
   verticalPanel(x-5,y+h*.7,w+10,68,z+14,'#d6aa6f');
   verticalPanel(x,y+h*.7,w,60,z+18,'#422630','x',o.text||'11.11 PYTHON CLASS','#ffe4b6');
   verticalPanel(x+12,y+h*.7,w-24,10,z+20,'#a66878','x','♥   11.11   ♥','#fff3df');
   box(x-7,y+h-2,w+14,6,z+13,3,'#efc88e','#b6815c','#764b47');
   plane(x+12,y+h-1,13,3,z+16.3,'#fce5c1',null);
   box(x+w-29,y+h-2,18,5,z+16,3,'#e6a6ab','#bd7584','#874c5b');
  }else if(type==='bookshelf'){
   box(x,y,w,h,z,3,...timber);
   box(x,y,4,h,z+3,57,...timber);box(x+w-4,y,4,h,z+3,57,...timber);
   box(x,y,w,3,z+3,57,'#f2cfab','#c7967c','#85545a');
   const colors=['#7a4b5c','#ce8d9c','#f6d8ac','#9e6173','#dbab84','#eed1b0'];
   for(const level of [4,23,42]){
    for(let at=7,i=0;at<w-7;at+=6,i++){
     const height=12+(i%3)*2;
     verticalPanel(x+at,y+h-3,4,height,z+level,colors[i%colors.length]);
     line(p(x+at+.7,y+h-3,z+level+3),p(x+at+3.3,y+h-3,z+level+3),'#f3ebc3',.6);
    }
    box(x,y,w,h,z+level+17,2,...timber);
   }
   for(const at of [18,56]){
    box(x+at,y+5,15,12,z+60,9,'#dd9aaa','#b36d83','#7e4655');
    plane(x+at+6,y+5,3,12,z+69.2,'#ffe1a4',null);
   }
   // Display sticks are fixed to the bookshelf top, so no extra walkable prop appears.
   const vx=x+w/2-10,vy=y+5;
   box(vx,vy,20,12,z+60,12,'#e6a6b6','#bb748b','#854b60');
   plane(vx+2,vy+2,16,8,z+72.3,'#5e3542','#d6a468');
   for(const [dx,color] of [[-6,'#6c3934'],[0,'#ee9aaf'],[6,'#653632']]){
    line(p(x+w/2+dx,vy+6,z+72),p(x+w/2+dx+2,vy-2,z+98),color,3.6);
    line(p(x+w/2+dx+2,vy-2,z+98),p(x+w/2+dx+2,vy-6,z+98),'#ffdfad',2.5);
   }
  }else if(type==='plant'){
   box(x+w*.25,y+h*.25,w*.5,h*.5,z,14,'#f4c6cb','#c88797','#9f5b70');
   plane(x+w*.3,y+h*.3,w*.4,h*.4,z+14.2,'#596345',null);
   const cx=x+w/2,cy=y+h/2;
   line(p(cx,cy,z+14),p(cx,cy,z+32),'#60815b',2);
   const q=p(cx,cy,z+31);
   for(let i=0;i<9;i++){const angle=i*Math.PI*2/9;ellipse(q.x+Math.cos(angle)*w*.24,q.y+Math.sin(angle)*h*.16,w*.18,h*.22,['#547f55','#779c66','#92ad78'][i%3]);}
   ellipse(q.x,q.y-4,w*.17,h*.2,'#80a66b');
   for(const [dx,dy] of [[-7,-3],[5,-8],[9,2],[-1,4]])ellipse(q.x+dx,q.y+dy,3.3,3.3,'#e48ca7');
  }else if(type==='door-post'){
   // Cutaway foreground frame keeps the entering avatar visible.
   box(x,y,w,h,z,20,...timber);box(x-1,y-1,w+2,h+2,z+20,3,...timber);
  }else if(type==='door-threshold'){
   plane(x,y,w,h,z+.4,'#8aab9d','#527f6d');
   for(let at=7;at<w;at+=8)line(p(x+at,y+2,z+.7),p(x+at,y+h-2,z+.7),'#d2e6d6',1);
  }else return false;
  return true;
 }
 function paintFloor({g,P,subject="python"}){
  g.save();g.transform(P.c,P.tilt*P.s,-P.s,P.tilt*P.c,P.offsetX,P.offsetY);
  // The 11.11 interior uses the same cream, cocoa and rose palette as the outside.
  g.fillStyle='#f7e7cf';g.fillRect(15,15,610,490);
  g.strokeStyle='#dfc8ab';g.lineWidth=1;
  for(let x=28;x<625;x+=46){g.beginPath();g.moveTo(x,15);g.lineTo(x,505);g.stroke();}
  for(let y=25;y<505;y+=46){g.beginPath();g.moveTo(15,y);g.lineTo(625,y);g.stroke();}
  g.fillStyle='#e2afae';g.fillRect(289,148,62,350);
  g.fillStyle='#f2d1c2';g.fillRect(299,148,42,350);
  g.strokeStyle='#a96775';g.lineWidth=2;g.strokeRect(27,25,586,469);
  g.strokeStyle='#fdf0d5';g.lineWidth=2;g.strokeRect(34,32,572,455);
  for(const zone of [{x:70,color:'#c16e8455',edge:'#9d536b',text:({python:'Python',sql:'SQL',cs:'CS'}[subject]+' 객관식')},
                     {x:390,color:'#b9796955',edge:'#815154',text:subject==='cs'?'CS 개념 빈칸 채우기':'코드 빈칸 채우기'}]){
   g.fillStyle=zone.color;g.fillRect(zone.x,145,200,268);
   g.strokeStyle='#ffe3bd';g.lineWidth=6;g.strokeRect(zone.x,145,200,268);
   g.strokeStyle=zone.edge;g.lineWidth=2;g.strokeRect(zone.x,145,200,268);
   g.font='700 16px "Malgun Gothic",sans-serif';g.textAlign='center';g.textBaseline='middle';g.fillStyle=zone.edge;g.fillText(zone.text,zone.x+100,402,190);
   for(const xx of [zone.x+16,zone.x+184])for(const yy of [159,383]){
    g.fillStyle='#e58b9e';g.beginPath();g.arc(xx,yy,3.2,0,Math.PI*2);g.fill();
   }
  }
  g.fillStyle='#af7186';g.font='700 13px Georgia,serif';g.textAlign='center';
  g.fillText('11 · 11',320,430);
  g.restore();
 }
 // Draw the guide on the floor so desks and characters remain in front of it.
 function paintAreaGuide(g,P,{activeType,intro,time,reducedMotion}){
  if(!activeType&&intro<=0)return;
  const pulse=reducedMotion?1:.7+Math.sin(time*4)*.3;
  g.save();g.transform(P.c,P.tilt*P.s,-P.s,P.tilt*P.c,P.offsetX,P.offsetY);
  for(const zone of [{x:70,type:'multiple_choice',edge:'#b96d82'},{x:390,type:'fill_blank',edge:'#9b6d68'}]){
   const active=activeType===zone.type;
   if(!active&&intro<=0)continue;
   g.globalAlpha=active?1:pulse*Math.min(1,intro*3);
   g.fillStyle=active?(zone.type==='multiple_choice'?'#d98da628':'#c0937d28'):'#fff9b315';g.fillRect(zone.x,145,200,268);
   g.strokeStyle=active?(zone.type==='multiple_choice'?'#c47b9d66':'#ba8b7466'):zone.edge+'44';g.lineWidth=7;g.strokeRect(zone.x,145,200,268);
   g.strokeStyle=active?(zone.type==='multiple_choice'?'#a45d79':'#875654'):zone.edge;g.lineWidth=3;g.strokeRect(zone.x,145,200,268);
  }
  g.restore();
 }
 // Instructor variant retains the campus sprite's proportions, outlines and shading.
 let instructorSprite=null;
 function drawActor(ctx,actor){
  if(actor.role!=='instructor')return false;
  if(!instructorSprite){
   const image=window.CampusAvatars?.images.get('male-2');
   if(!image?.complete||!image.naturalWidth)return true;
   const sheet=document.createElement('canvas');sheet.width=128;sheet.height=128;
   const g=sheet.getContext('2d');g.drawImage(image,128,0,128,128,0,0,128,128);
   const pixels=g.getImageData(0,0,128,128);
   // Recolor only purple clothing; keep skin, hair, outlines and highlights intact.
   for(let y=67;y<115;y++)for(let x=0;x<128;x++){
    const at=(y*128+x)*4,red=pixels.data[at],green=pixels.data[at+1],blue=pixels.data[at+2];
    if(pixels.data[at+3]&&blue>green*1.15&&red>green*1.08&&blue>red*.92){
     pixels.data[at]=Math.round(red*.43);pixels.data[at+1]=Math.round(green*.7);pixels.data[at+2]=Math.round(blue*.67);
    }
   }
   g.putImageData(pixels,0,0);instructorSprite=sheet;
  }
  ctx.save();ctx.imageSmoothingEnabled=false;
  ctx.drawImage(instructorSprite,-20,-38.75,40,40);
  // Small pixel glasses and a teal lesson notebook distinguish the instructor.
  ctx.strokeStyle='#273641';ctx.lineWidth=.75;
  ctx.strokeRect(-6.5,-23.5,5.5,3.2);ctx.strokeRect(1,-23.5,5.5,3.2);
  ctx.beginPath();ctx.moveTo(-1,-22);ctx.lineTo(1,-22);ctx.stroke();
  ctx.fillStyle='#244f52';ctx.fillRect(7,-15,5,7);
  ctx.fillStyle='#e4ddbf';ctx.fillRect(8,-14,3,5);
  ctx.restore();return true;
 }
 function paintWallCorners(ctx,P){
  // Fixed fairy lights run along the back wall; they are painted once per scene view,
  // never sent over the movement socket and never added to collision geometry.
  ctx.save();ctx.strokeStyle='#c89759';ctx.lineWidth=2;
  const first=P.project(35,15,76),last=P.project(605,15,76);
  ctx.beginPath();ctx.moveTo(first.x,first.y);ctx.quadraticCurveTo((first.x+last.x)/2,(first.y+last.y)/2+13,last.x,last.y);ctx.stroke();
  for(let x=55;x<=585;x+=44){
   const point=P.project(x,15,76),drop=12*Math.sin(Math.PI*(x-35)/570),y=point.y+drop;
   ctx.fillStyle='#fff1bd';ctx.beginPath();ctx.arc(point.x,y,3.4,0,Math.PI*2);ctx.fill();
   ctx.strokeStyle='#d6955f';ctx.lineWidth=1;ctx.stroke();
  }
  ctx.restore();
  // Bridge the taller back wall and side-wall caps without leaving open corners.
  for(const x of [10,620]){
   const top=P.project(x,15,80),bottom=P.project(x+10,15,48);
   ctx.fillStyle='#714c4b';ctx.fillRect(top.x,top.y,10,bottom.y-top.y+4);
  }
  for(const x of [10,620]){
   const top=P.project(x,450),bottom=P.project(x+10,510);
   ctx.fillStyle='#e4bea6';ctx.fillRect(top.x,top.y,bottom.x-top.x,bottom.y-top.y);
   ctx.fillStyle='#714c4b';ctx.fillRect(top.x,top.y,Math.max(2,(bottom.x-top.x)*.35),bottom.y-top.y);
  }
 }
 window.CampusClassroomAssets={paint,paintFloor,paintAreaGuide,drawActor,paintWallCorners};
})();
