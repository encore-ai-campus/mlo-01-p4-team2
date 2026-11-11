/* Event-only visual layer. All hit/guard/durability decisions remain on the server. */
(() => {
 'use strict';
 const images={};for(const id of ['male-1','male-2','female-1','female-2']){const im=new Image();im.src='assets/seasonal/pepero/previews/'+(id==='male-1'?'mint':id)+'-combo-v2.png';images[id]=im;}
 const specs={ 'male-1':{roots:[207,580,940,1348,214,584,950,1355],feet:[490,490,490,490,930,930,930,930],hands:[[100,240],[597,339],[1088,363],[1266,376],[110,780],[710,715],[1085,671],[1240,674]]}, 'male-2':{roots:[207,578,949,1353,207,580,949,1353],feet:[495,495,495,495,953,953,953,953],hands:[[98,235],[598,336],[1099,356],[1255,374],[102,802],[717,728],[1117,690],[1235,674]]},'female-1':{roots:[211,587,961,1362,207,583,959,1367],feet:[480,480,480,480,930,930,930,930],hands:[[95,208],[637,323],[1117,329],[1273,352],[96,777],[732,694],[1112,655],[1251,648]]},'female-2':{roots:[212,586,961,1371,214,587,967,1376],feet:[494,494,494,494,939,939,939,939],hands:[[109,227],[629,325],[1108,341],[1289,351],[113,785],[738,713],[1118,671],[1260,666]]}};
 const directional={"male-1-front": {"roots": [208.5, 586.5, 957.5, 1344.5, 203.0, 582.5, 972.0, 1342.0], "feet": [496, 496, 497, 496, 953, 953, 953, 953], "hands": [[104, 203], [580, 327], [984, 382], [1388, 337], [114, 799], [679, 716], [1078, 654], [1242, 663]]}, "male-1-back": {"roots": [190.0, 593.0, 976.0, 1356.0, 194.0, 576.0, 959.0, 1344.5], "feet": [492, 488, 491, 492, 970, 970, 972, 972], "hands": [[308, 201], [718, 338], [882, 374], [1300, 346], [340, 742], [717, 662], [1087, 627], [1461, 714]]}, "male-2-front": {"roots": [188.5, 569.5, 949.5, 1345.5, 183.5, 571.5, 952.0, 1354.0], "feet": [475, 474, 475, 474, 967, 967, 967, 968], "hands": [[93, 213], [616, 300], [945, 354], [1334, 340], [282, 734], [466, 662], [992, 794], [1252, 702]]}, "male-2-back": {"roots": [201.0, 584.0, 968.5, 1353.5, 201.0, 584.0, 968.0, 1352.5], "feet": [498, 498, 498, 498, 977, 977, 977, 977], "hands": [[79, 213], [726, 356], [1108, 389], [1269, 342], [311, 776], [732, 661], [1100, 634], [1233, 688]]}, "female-1-front": {"roots": [196.0, 579.5, 963.0, 1347.0, 195.0, 581.5, 963.0, 1346.0], "feet": [475, 475, 475, 475, 963, 963, 963, 963], "hands": [[104, 177], [600, 295], [951, 364], [1402, 349], [245, 849], [703, 631], [989, 783], [1258, 671]]}, "female-1-back": {"roots": [201.5, 595.5, 969.0, 1371.0, 207.5, 590.5, 970.5, 1357.0], "feet": [485, 485, 485, 485, 959, 959, 959, 959], "hands": [[88, 140], [718, 346], [1109, 374], [1275, 305], [336, 639], [711, 577], [1106, 809], [1236, 600]]}, "female-2-front": {"roots": [199.0, 608.0, 988.5, 1376.0, 202.5, 597.5, 986.5, 1364.0], "feet": [494, 494, 494, 494, 966, 966, 966, 966], "hands": [[88, 190], [597, 325], [973, 345], [1407, 352], [229, 827], [708, 702], [1093, 616], [1257, 656]]}, "female-2-back": {"roots": [198.0, 592.5, 974.5, 1381.0, 204.5, 603.5, 980.5, 1369.5], "feet": [494, 494, 494, 494, 953, 953, 953, 953], "hands": [[75, 196], [732, 298], [1110, 358], [1470, 318], [335, 665], [712, 593], [1109, 701], [1239, 664]]}};
 for(const key of Object.keys(directional)){const im=new Image();im.src='assets/seasonal/pepero/previews/'+key+'-combo-v3.png';images[key]=im;}
 function sheetKey(actor,action){return actor.avatar+(action.dir==='up'?'-back':action.dir==='down'?'-front':'');}
 function specFor(actor,action){return directional[sheetKey(actor,action)]||specs[actor.avatar];}
 const hands=[[100,240],[597,339],[1088,363],[1266,376],[110,780],[710,715],[1085,671],[1240,674]];
 const angles=[-2.15,-.8,.1,.8,.8,-.2,-1.1,-2.15];
 const reduced=matchMedia('(prefers-reduced-motion: reduce)');
 function sample(action,t){const p=Math.max(0,Math.min(.999,(t-action.started)/250)),f=(action.stroke??0)%2*4+Math.floor(p*4),n=(f+1)%8;const q=Math.max(0,(p*4%1-.6)/.4);return {f,n,u:reduced.matches?0:q*q*(3-2*q),p};}
 function generated(actor,action){return !actor.guarding&&images[sheetKey(actor,action)]?.complete&&images[sheetKey(actor,action)]?.naturalWidth>0;}
 function drawBody(ctx,actor,t){const a=window.CampusPoke?.action(actor,t);if(!a||!generated(actor,a))return false;const {f,n,u}=sample(a,t),{roots,feet}=specFor(actor,a),image=images[sheetKey(actor,a)],scale=32/365;
   ctx.save();if(a.dir.includes('left'))ctx.scale(-1,1);ctx.imageSmoothingEnabled=true;
   for(const [i,alpha] of [[f,1-u],[n,u]]){if(!alpha)continue;ctx.globalAlpha*=alpha;const x=i%4*384,y=Math.floor(i/4)*512;ctx.drawImage(image,x,y,384,512,(x-roots[i])*scale,(y-feet[i])*scale,384*scale,512*scale);ctx.globalAlpha/=alpha;}ctx.restore();return true;
 }
 function drawWeapon(ctx,actor,t,P,behind,a){
   const {f,n,u,p}=sample(a,t),left=a.dir.includes('left'),up=a.dir.includes('up');if(up!==behind)return;
   const count=Math.max(0,Math.min(5,Math.ceil((actor.peperoRemaining??0)/2)));
   let hx,hy,angle;
   if(generated(actor,a)){const {roots,feet,hands}=specFor(actor,a);const s=32/365;hx=((hands[f][0]-roots[f])*(1-u)+(hands[n][0]-roots[n])*u)*s;hy=((hands[f][1]-feet[f])*(1-u)+(hands[n][1]-feet[n])*u)*s;const as=sheetKey(actor,a).endsWith('-front')?[-1.8,-.7,.7,1.2,1.2,-.6,-1.5,-1.8]:sheetKey(actor,a).endsWith('-back')?[-1,-.2,2,2.8,.2,-1,-1.2,-1]:angles;angle=as[f]+(as[n]-as[f])*u;if(left){hx=-hx;angle=Math.PI-angle;}}
   else {const dirs={up:[0,-1],down:[0,1],left:[-1,0],right:[1,0],'up-left':[-.707,-.707],'up-right':[.707,-.707],'down-left':[-.707,.707],'down-right':[.707,.707]},v=dirs[a.dir]||dirs.right;
     const theta=Math.atan2(v[1],v[0]),sweep=((a.stroke??0)%2?-1:1)*(p-.5)*2.5;angle=theta+sweep;hx=v[0]*8+Math.cos(angle)*7;hy=-15+v[1]*6+Math.sin(angle)*5;
     ctx.save();ctx.strokeStyle=actor.color||'#4e9b91';ctx.lineWidth=3;ctx.lineCap='round';ctx.beginPath();ctx.moveTo(v[0]*4,-15);ctx.lineTo(hx,hy);ctx.stroke();ctx.restore();
   }
   if(!count){if(generated(actor,a))return;ctx.save();ctx.fillStyle='#f2c3a1';ctx.beginPath();ctx.arc(hx,hy,2.5,0,Math.PI*2);ctx.fill();ctx.restore();return;}
   ctx.save();ctx.translate(hx,hy);ctx.rotate(angle);const length=7+count*7;ctx.lineCap='round';ctx.strokeStyle='#d5a65e';ctx.lineWidth=2.1;ctx.beginPath();ctx.moveTo(0,0);ctx.lineTo(length,0);ctx.stroke();ctx.strokeStyle='#603329';ctx.lineWidth=2.9;ctx.beginPath();ctx.moveTo(4,0);ctx.lineTo(length,0);ctx.stroke();ctx.fillStyle='#ffb9cf';for(let x=6;x<length;x+=3.7)ctx.fillRect(x,-.8,1,.7);ctx.restore();
   if(!reduced.matches){ctx.save();ctx.globalAlpha*=.22*Math.sin(p*Math.PI);ctx.strokeStyle='#ed9ab8';ctx.lineWidth=1.4;ctx.beginPath();ctx.arc(0,-17,22,angle-.45,angle+.15);ctx.stroke();ctx.restore();}
 }
 function drawCarry(ctx,actor,t,behind){
   const count=Math.max(0,Math.min(5,Math.ceil((actor.peperoRemaining??0)/2)));if(!count)return;
   const back=actor.dir==='up',left=actor.dir==='left';if(back!==behind)return;
   const sway=actor.moving&&!reduced.matches?Math.sin(t/(actor.running?80:120))*1.4:0;
   const x=back?-7:left?-8:8,y=-14+sway,angle=left?-2:-1.12;
   ctx.save();ctx.translate(x,y);ctx.rotate(angle);const length=7+count*7;
   ctx.lineCap='round';ctx.strokeStyle='#e6b774';ctx.lineWidth=2.6;ctx.beginPath();ctx.moveTo(-3,0);ctx.lineTo(length,0);ctx.stroke();
   ctx.strokeStyle='#603329';ctx.lineWidth=3.8;ctx.beginPath();ctx.moveTo(6,0);ctx.lineTo(length,0);ctx.stroke();
   ctx.strokeStyle='#b47a65';ctx.lineWidth=.7;ctx.beginPath();ctx.moveTo(7,-1);ctx.lineTo(length-2,-1);ctx.stroke();
   ctx.fillStyle='#ffd3df';for(let j=10;j<length;j+=5)ctx.fillRect(j,-1,1,.8);
   ctx.fillStyle='#efc1a1';ctx.beginPath();ctx.ellipse(0,0,2.6,2.2,0,0,Math.PI*2);ctx.fill();ctx.restore();
 }
 window.CampusSwing={drawBody,drawWeapon,drawCarry,sample};
})();
