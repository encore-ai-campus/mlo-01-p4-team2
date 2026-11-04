/* 11.11 Chocolate Atelier: cached scenery + restrained moving light. */
(() => {
  'use strict';
  // The classic map must not initialize event renderers or fetch event art.
  if(window.CAMPUS_SEASON!=='pepero')return;
  const motion = matchMedia('(prefers-reduced-motion: reduce)');
  const art = {};
  for (const key of ['monument','cart','gifts','stall','workbench','flowerbox','hero','gate','pedestal','cafe','refill-chest','recipe-note','timer-station-east','typing-station-east','pairing-garden-east']) {
    const image = new Image(); image.decoding = 'async';
    image.onload = () => window.dispatchEvent(new CustomEvent('campus-seasonal-assets-ready',{detail:{key}}));
    image.src = key==='pairing-garden-east'?'assets/minigames/pairing-garden-east-v1.png':['timer-station-east','typing-station-east'].includes(key)?`assets/minigames/${key}-v1.webp`:`assets/seasonal/pepero/${['monument','cart','gifts','refill-chest','recipe-note'].includes(key)?'':'reference/'}${key}.png`; art[key] = image;
  }
  function drawAsset(g,image,q,w,h) {
    if (!image.complete || !image.naturalWidth) return;
    const s=Math.min(w/image.naturalWidth,h/image.naturalHeight),sw=image.naturalWidth*s,sh=image.naturalHeight*s;
    g.save();g.imageSmoothingEnabled=true;g.imageSmoothingQuality='high';
    g.drawImage(image,q.x-sw/2,q.y-sh*.88,sw,sh);g.restore();
  }
  // Draw names after the world geometry so walls cannot cover the game labels.
  function paintGameLabel(o,{g,p},base=0){
    if(o.action!=='minigame'||!o.seasonalArt)return;
    const image=art[o.seasonalArt];if(!image?.complete||!image.naturalWidth)return;
    const scale=Math.min(o.artWidth/image.naturalWidth,o.artHeight/image.naturalHeight),h=image.naturalHeight*scale;
    const q=p(o.x+o.w/2+(o.artGroundOffsetX||0),o.y+o.h/2,base),top=q.y-h*(o.artAnchor??.92)-28;
    g.save();g.fillStyle='#51333ff2';g.strokeStyle='#dfbc83';g.lineWidth=1;
    g.beginPath();g.roundRect(q.x-54,top,108,20,6);g.fill();g.stroke();
    g.textAlign='center';g.textBaseline='middle';g.fillStyle='#fff0cf';g.font='600 10px sans-serif';
    g.fillText(({timer:'11.11초 타이머',typing:'코드 타이핑',term_match:'페어링 정원'})[o.gameCode],q.x,top+10);g.restore();
  }
  function heart(g,x,y,s,color) {
    g.save();g.translate(x,y);g.scale(s,s);g.beginPath();g.moveTo(0,.75);
    g.bezierCurveTo(-1.8,-.3,-.7,-1.5,0,-.65);g.bezierCurveTo(.7,-1.5,1.8,-.3,0,.75);
    g.fillStyle=color;g.fill();g.restore();
  }
  function paint(o,{g,p,box,line,ellipse,poly,verticalPanel},base=0) {
    if(window.CAMPUS_SEASON!=='pepero')return false;
    if(o.seasonalArt){
      const image=art[o.seasonalArt];if(!image?.complete||!image.naturalWidth)return o.action!=='minigame';
      const q=p(o.x+o.w/2+(o.artGroundOffsetX||0),o.y+o.h/2,base),s=Math.min(o.artWidth/image.naturalWidth,o.artHeight/image.naturalHeight),w=image.naturalWidth*s,h=image.naturalHeight*s;
      if(o.action==='minigame'&&Number.isFinite(o.wallContactX)){
        // A small contact shadow joins the furniture back to the inner wall face.
        const x=o.wallContactX,y=o.y+7,end=o.y+o.h-7;
        const corners=[p(x,y,base+2),p(x,end,base+2),p(x,end,base+30),p(x,y,base+30)];
        g.save();g.fillStyle='#412a3623';g.beginPath();corners.forEach((v,i)=>i?g.lineTo(v.x,v.y):g.moveTo(v.x,v.y));g.closePath();g.fill();g.restore();
      }
      g.save();g.translate(q.x,q.y);if(o.flip)g.scale(-1,1);g.imageSmoothingEnabled=true;g.imageSmoothingQuality='high';
      g.drawImage(image,-w/2,-h*(o.artAnchor??.92),w,h);
      if(o.refillBox){
        const top=-h*(o.artAnchor??.92)-24;
        g.fillStyle='#4c2b38ed';g.strokeStyle='#f3cf90';g.lineWidth=1.4;
        g.beginPath();g.roundRect(-57,top,114,23,7);g.fill();g.stroke();
        g.fillStyle='#f4cf8b';g.beginPath();g.roundRect(-52,top+3,18,17,4);g.fill();
        g.textAlign='center';g.textBaseline='middle';g.fillStyle='#4c2b38';g.font='800 11px sans-serif';g.fillText('E',-43,top+11.5);
        g.fillStyle='#fff3d9';g.font='700 11px sans-serif';g.fillText('빼빼로 리필',10,top+11.5);
      }
      g.restore();return true;
    }
    if(o.referenceBoard||o.type==='pepero-reference-sign'){
      const isBoard=o.referenceBoard,high=o.wallPanel,h=isBoard?145:high?185:47,w=o.w,z=isBoard?170:high?365-base:112;
      const q=p(o.x,o.y,base+z);g.save();g.transform(Math.SQRT1_2,Math.SQRT1_2*.58,0,1,q.x,q.y);
      const grad=g.createLinearGradient(0,0,0,h);grad.addColorStop(0,'#623c2c');grad.addColorStop(.5,'#3b211d');grad.addColorStop(1,'#5e352a');
      g.fillStyle=grad;g.fillRect(0,0,w,h);g.strokeStyle='#dda558';g.lineWidth=2;g.strokeRect(5,5,w-10,h-10);g.strokeStyle='#ffdf9544';g.strokeRect(9,9,w-18,h-18);
      g.textAlign='center';g.fillStyle='#ffe3a3';g.shadowColor='#f4b947';g.shadowBlur=3;
      if(isBoard){g.font='40px Georgia';g.fillText('11.11',w/2,53);g.font='14px Georgia';g.fillText('A LITTLE SWEETER',w/2,83);g.fillText('TOGETHER',w/2,104);}
      else{g.font=`600 ${high?36:Math.min(19,w/(o.label.length*.6))}px Georgia`;g.fillText(o.label,w/2,high?95:30);if(high){g.font='19px Georgia';g.fillText('♥    11.11    ♥',w/2,135);}}
      g.shadowBlur=0;for(const x of [21,w-21])heart(g,x,h/2,high?11:6,'#ef9aaf');
      for(let x=15;x<w;x+=42){g.fillStyle='#fff5bb';g.shadowColor='#ffad38';g.shadowBlur=12;g.beginPath();g.arc(x,8,2.4,0,Math.PI*2);g.fill();}g.restore();return true;
    }
    if(o.type==='pepero-heart-panel'){
      const q=p(o.x,o.y,base+(o.low?54:84));g.save();g.transform(Math.SQRT1_2,Math.SQRT1_2*.58,0,1,q.x,q.y);
      g.fillStyle='#fff0cc';g.fillRect(0,0,o.w,60);g.fillStyle='#eda4b0';g.fillRect(7,7,o.w-14,46);g.strokeStyle='#cc9755';g.strokeRect(3,3,o.w-6,54);heart(g,o.w/2,31,18,'#cf597d');g.restore();return true;
    }
    if(o.type==='pepero-ribbon-lamp'){
      const {x,y,w,h}=o;box(x-3,y-3,w+6,h+6,base,8,'#ffe2a1','#bc8241','#89542d');
      box(x+4,y+4,4,4,base+8,95,'#ffdc86','#c49347','#89572e');
      const q=p(x+6,y+6,base+108);g.save();g.shadowColor='#ffb540';g.shadowBlur=26;ellipse(q.x,q.y,10,14,'#fff4b4');g.shadowBlur=0;g.strokeStyle='#e6b85f';g.lineWidth=2;g.strokeRect(q.x-9,q.y-14,18,27);g.restore();
      const b=p(x+6,y+6,base+68);heart(g,b.x-4,b.y,6,'#ee7b9d');heart(g,b.x+4,b.y,6,'#ee7b9d');return true;
    }
    if(o.type==='pepero-light-rail'){
      const alongY=o.axis==='y',span=alongY?o.h:o.w,z=o.height||62;
      box(o.x,o.y,o.w,o.h,base+z,3,'#ffda8c','#b98245','#8c532d');
      for(let a=0;a<span;a+=50){const x=o.x+(alongY?0:a),y=o.y+(alongY?a:0);box(x,y,4,4,base,z,'#ffe2a4','#bd854b','#81532f');const q=p(x+2,y+2,base+z+4);g.save();g.shadowColor='#ffc866';g.shadowBlur=15;ellipse(q.x,q.y,3,3,'#fff6bf');g.restore();}return true;
    }
    if(o.type==='pepero-chocolate-column'){
      const {x,y,w,h}=o,z=o.height-base;box(x,y,w,h,base,z,'#83502c','#5c3023','#3b201e');
      for(const zz of [0,12,z-18,z-6])box(x-3,y-3,w+6,h+6,base+zz,4,'#ffe09c','#c78d43','#95612c');
      box(x+5,y+5,5,5,base+30,z-60,'#ffe6ad','#b87637','#754223');
      const q=p(x+w/2,y+h/2,base+z-8);g.save();g.shadowBlur=20;g.shadowColor='#ffb43d';ellipse(q.x,q.y,5,6,'#fff5bf');g.restore();return true;
    }

    if(o.id==='class3-4-noticeboard') {
      const v={x:Math.SQRT1_2,y:Math.SQRT1_2*.58},q=p(o.x,o.y,base+156);
      g.save();g.transform(v.x,v.y,0,1,q.x,q.y);
      const gradient=g.createLinearGradient(0,0,o.w,130);gradient.addColorStop(0,'#492b2c');gradient.addColorStop(1,'#241a20');
      g.fillStyle=gradient;g.fillRect(0,0,o.w,132);g.strokeStyle='#c9a567';g.lineWidth=2;g.strokeRect(6,6,o.w-12,120);
      g.strokeStyle='#c9a56750';g.strokeRect(11,11,o.w-22,110);
      g.textAlign='center';g.fillStyle='#ebd1a0';g.font='500 12px Georgia, serif';g.fillText('PLAYDATA  •  NOVEMBER COLLECTION',o.w/2,29);
      g.font='500 47px Georgia, serif';g.fillStyle='#fff0d7';g.fillText('11.11',o.w/2,77);
      g.font='600 17px Georgia, serif';g.fillStyle='#dfb777';g.fillText('PEPERO ATELIER',o.w/2,106);
      for(const x of [36,o.w-36])heart(g,x,68,9,'#b9707d');g.restore();return true;
    }
    if(o.id==='roof-ad-board'&&!o.imageUrl){
      const q=p(o.x+o.w/2,o.y+o.h/2,base+180);g.save();g.transform(Math.SQRT1_2,-Math.SQRT1_2*.58,0,1,q.x,q.y);
      g.fillStyle='#51303b';g.fillRect(-150,0,300,160);g.strokeStyle='#d8b77f';g.lineWidth=3;g.strokeRect(-143,7,286,146);
      g.textAlign='center';g.fillStyle='#ffe6bd';g.font='42px Georgia';g.fillText('11.11',0,65);g.font='18px Georgia';g.fillText('SWEET FESTIVAL',0,102);g.font='11px Georgia';g.fillStyle='#ddb1b7';g.fillText('PLAYDATA · MADE TO SHARE',0,131);g.restore();return true;
    }
    if(o.type==='pepero-wall-panel'){

      const q=p(o.x,o.y,base+360);g.save();g.transform(Math.SQRT1_2,Math.SQRT1_2*.58,0,1,q.x,q.y);
      g.fillStyle='#67404a';g.fillRect(0,0,o.w,200);g.strokeStyle='#d5b177';g.lineWidth=3;g.strokeRect(10,10,o.w-20,180);g.strokeRect(17,17,o.w-34,166);
      g.textAlign='center';g.fillStyle='#e1bc83';g.font='14px Georgia';g.fillText('PLAYDATA • CHOCOLATE TERRACE',o.w/2,60);
      g.font='42px Georgia';g.fillStyle='#ffe4b8';g.fillText(o.label,o.w/2,117);g.font='14px Georgia';g.fillStyle='#d7a3af';g.fillText('A LITTLE SWEETER, TOGETHER',o.w/2,155);g.restore();return true;
    }
    // Shared isometric event furniture: geometry follows the same projection as the map.
    if(['pepero-display','pepero-market','pepero-photo','pepero-sign','pepero-tower'].includes(o.type)||o.seasonalMarket){
      const {x,y,w,h}=o;
      const cream=['#f7e6c8','#d8bc96','#b29370'],cocoa=['#714735','#51302e','#39242b'],rose=['#ce93a1','#a96c7d','#7e485e'];
      const block=(xx,yy,ww,hh,z,hhz,c)=>box(xx,yy,ww,hh,base+z,hhz,...c);
      const sign=(xx,yy,width,z,title,sub='')=>{
        const a=p(xx,yy,base+z);g.save();g.transform(Math.SQRT1_2,Math.SQRT1_2*.58,0,1,a.x,a.y);
        g.fillStyle='#492d36';g.fillRect(0,-52,width,52);g.strokeStyle='#d9b575';g.lineWidth=1.5;g.strokeRect(4,-48,width-8,44);
        g.fillStyle='#ffe9bd';g.textAlign='center';g.font=`600 ${Math.min(17,width/Math.max(title.length*.63,1))}px Georgia`;
        g.fillText(title,width/2,-26);g.font='9px Georgia';g.fillStyle='#dcafae';g.fillText(sub,width/2,-10);g.restore();
      };
      const bonbon=(xx,yy,z,index)=>{
        const c=index%3===0?rose:index%3===1?cocoa:cream;
        block(xx,yy,12,16,z,10,c);block(xx+4,yy,3,16,z+10,1,cream);
      };
      if(o.type==='pepero-display'){
        block(x,y,w,h,0,8,cocoa);block(x+3,y+3,w-6,h-6,8,32,rose);block(x-2,y-2,w+4,h+4,40,5,cream);
        for(let i=0;i<Math.floor((w-14)/20);i++)bonbon(x+8+i*20,y+h*.4,45,i);
        // Glass edge with a fine brass rim, kept transparent so the chocolates stay readable.
        poly([p(x,y+h,base+46),p(x+w,y+h,base+46),p(x+w,y+h,base+72),p(x,y+h,base+72)],'#fff5e52b','#edc98e');
        sign(x+5,y+4,w-10,85,o.label||'CHOCOLATE','11.11');
      }else if(o.type==='pepero-market'||o.seasonalMarket){
        const height=o.seasonalMarket?150:130;
        block(x,y,w,8,0,height,cocoa);
        for(const xx of [x+3,x+w-11])block(xx,y+4,8,h-8,0,5,cream);
        for(const xx of [x+3,x+w-11])block(xx,y+h-12,8,8,0,height,cream);
        for(let i=0;i<10;i++)block(x-5+i*(w+10)/10,y-6,(w+10)/10,h+18,height,8,i%2?rose:cream);
        for(let i=0;i<10;i++)block(x-5+i*(w+10)/10,y+h+6,(w+10)/10,6,height-13,13,i%2?rose:cream);
        if(!o.seasonalMarket){
          block(x+12,y+h-34,w-24,30,0,43,rose);block(x+9,y+h-37,w-18,36,43,5,cream);
          for(let i=0;i<Math.floor((w-35)/25);i++)bonbon(x+20+i*25,y+h-29,48,i);
        }
        sign(x+12,y+10,w-24,height-15,o.label||'SWEET MARKET','THE NOVEMBER COLLECTION');
      }else if(o.type==='pepero-sign'){
        for(const xx of [x+12,x+w-20])block(xx,y,8,8,0,90,cream);
        sign(x,y,w,120,o.label,o.sub);
      }else if(o.type==='pepero-photo'){
        for(const xx of [x,x+w-18]){
          block(xx-4,y-4,26,32,0,8,cream);block(xx,y,18,22,8,150,cocoa);
          for(let z=30;z<150;z+=24)block(xx-1,y-1,20,24,z,3,cream);
        }
        block(x-5,y-2,w+10,26,158,15,rose);sign(x+16,y+2,w-32,163,o.label||'WITH LOVE','11.11');
        const a=p(x+w/2,y+10,base+94);g.save();g.shadowColor='#efb3b3';g.shadowBlur=14;g.strokeStyle='#e9bc79';g.lineWidth=3;
        heart(g,a.x,a.y,29,'#b87587');g.restore();
      }else{
        // The existing fountain footprint becomes a tiered bonbon monument.
        for(let tier=0;tier<3;tier++){
          const inset=tier*32;block(x+inset,y+inset,w-inset*2,h-inset*2,tier*30,30,tier%2?rose:cocoa);
          block(x+inset-2,y+inset-2,w-inset*2+4,h-inset*2+4,tier*30+28,3,cream);
        }
        drawAsset(g,art.monument,p(x+w/2,y+h/2,base+96),310,260);
        sign(x+45,y+h-6,w-90,65,'11.11','SWEETER TOGETHER');
      }
      return true;
    }

    if(!o.type.startsWith('pepero-'))return false;
    const q=p(o.x+o.w/2+(o.artGroundOffsetX||0),o.y+o.h/2,base);
    if(o.type==='pepero-monument'||o.type==='pepero-cart'||o.type==='pepero-gift-art') {
      drawAsset(g,art[o.type==='pepero-monument'?'monument':o.type==='pepero-cart'?'cart':'gifts'],q,o.artWidth,o.artHeight);
    } else if(o.type==='pepero-table-gift') {
      box(o.x,o.y,o.w,o.h,base+28,3,'#fff0d6','#d5b995','#aa876a');
      box(o.x+o.w*.42,o.y,o.w*.13,o.h,base+31,.7,'#ae677a','#905163','#71394d');
      for(let i=0;i<3;i++){
        box(o.x+2+i*2.8,o.y+1,1.6,o.h-2,base+32,1.5,'#684238','#4d302a','#3e2527');
      }
    } else if(o.type==='pepero-gifts') {
      const palette=[['#f6e5cc','#ddc3a1','#b99c7d'],['#b87485','#925568','#713f52'],['#604037','#472d2b','#342125']];
      for(let i=0;i<3;i++){
        const x=o.x+i*o.w*.27,y=o.y+(i%2)*o.h*.34,w=o.w*.37,h=o.h*.62,z=base+(i===1?5:0),height=17+(i%2)*14,c=palette[i];
        box(x,y,w,h,z,height,...c);box(x-1,y-1,w+2,h+2,z+height,3,c[0],c[1],c[2]);
        box(x+w*.43,y-1,w*.13,h+2,z+height+3,.7,'#d5ad69','#b99050','#ba965c');
        const a=p(x+w*.5,y+h*.5,z+height+5);g.save();g.strokeStyle='#e6c58b';g.lineWidth=2;
        for(const d of [-1,1]){g.beginPath();g.ellipse(a.x+d*4,a.y-2,5,2.5,d*.4,0,Math.PI*2);g.stroke();}g.restore();
      }
    } else if(o.type==='pepero-stick-planter') {
      box(o.x,o.y,o.w,o.h,base,16,'#efe0c5','#d8bc91','#b4926b');
      box(o.x-1,o.y-1,o.w+2,o.h+2,base+16,3,'#f7e9cf','#c8a773','#a78050');
      for(let i=0;i<7;i++){
        const x=o.x+3+(i%4)*(o.w-8)/3,y=o.y+3+Math.floor(i/4)*(o.h-7),height=40+(i*17)%30;
        box(x,y,4,4,base+17,height,'#d9b882','#af8955','#997344');
        box(x-.6,y-.6,5.2,5.2,base+29,height-10,...(i%3===0?['#efb6bc','#cb8b98','#ad6c7d']:i%3===1?['#745041','#50312c','#3b2425']:['#fff0ce','#d9c298','#bda279']));
      }
    } else if(o.type==='pepero-garland') {
      const alongY=o.axis==='y',span=alongY?o.h:o.w,n=Math.max(3,Math.floor(span/38)),pts=[];
      for(let i=0;i<=n*4;i++){const t=i/(n*4);pts.push(p(o.x+(alongY?0:span*t),o.y+(alongY?span*t:0),base+o.height-7*Math.sin(t*Math.PI)));}
      for(let i=1;i<pts.length;i++)line(pts[i-1],pts[i],'#a78756',1.2);
      for(let i=0;i<=n;i++){
        const t=i/n,a=p(o.x+(alongY?0:span*t),o.y+(alongY?span*t:0),base+o.height-7*Math.sin(t*Math.PI));
        g.save();g.shadowColor='#ffd68d';g.shadowBlur=9;ellipse(a.x,a.y+3,2.2,3.1,'#fff1c7');g.restore();
        if(i%3===1){line({x:a.x,y:a.y+4},{x:a.x,y:a.y+14},'#c7a777',.7);heart(g,a.x,a.y+19,5,'#bd7685');}
      }
    } else if(o.type==='pepero-arch') {
      const height=o.height||120;
      for(const x of [o.x,o.x+o.w-9]) {
        box(x-4,o.y-4,17,17,base,8,'#e0c49c','#ac805c','#835b47');
        box(x,o.y,9,9,base+8,height,'#f0d7a5','#b78d52','#926a3d');
        box(x-1,o.y-1,11,11,base+35,height-26,'#654237','#45282a','#301d23');
        for(let z=44;z<height;z+=20)box(x-1.5,o.y-1.5,12,12,base+z,2,'#e2bc77','#ba9254','#a57b40');
      }
      box(o.x-3,o.y-1,o.w+6,11,base+height+8,10,'#e9c992','#bc9459','#92673e');
      const c=p(o.x+o.w/2,o.y+5,base+height+13);heart(g,c.x,c.y,17,'#b66d7d');
    } else if(o.type==='pepero-lamp') {
      box(o.x,o.y,o.w,o.h,base,6,'#d7b67d','#a07b49','#77562f');
      box(o.x+o.w*.4,o.y+o.h*.4,o.w*.2,o.h*.2,base+6,55,'#d7b67d','#a07b49','#77562f');
      const a=p(o.x+o.w/2,o.y+o.h/2,base+70);g.save();g.shadowColor='#ffd799';g.shadowBlur=20;
      const light=g.createRadialGradient(a.x-3,a.y-4,1,a.x,a.y,12);light.addColorStop(0,'#fffdf0');light.addColorStop(1,'#edc47b');ellipse(a.x,a.y,11,13,light);g.restore();
    }
    return true;
  }
  function paintFloor(data,{g,p,plane,line}) {
    if(window.CAMPUS_SEASON!=='pepero')return;
    if(data.seasonalReference){
      // Warm pools are painted once into the floor cache.
      for(const o of data.objects.filter(o=>o.type==='pepero-ribbon-lamp'||o.seasonalArt==='pedestal'||o.seasonalArt==='stall')){
        const q=p(o.x+o.w/2,o.y+o.h/2,(data.elevationAt?.(o.x+o.w/2,o.y+o.h/2)||0)+1);
        g.save();g.translate(q.x,q.y);g.scale(1,.58);const glow=g.createRadialGradient(0,0,0,0,0,60);glow.addColorStop(0,'#ffcd7870');glow.addColorStop(1,'#ffd88b00');g.fillStyle=glow;g.beginPath();g.arc(0,0,60,0,Math.PI*2);g.fill();g.restore();
      }
      const zAt=(x,y)=>(data.elevationAt?.(x,y)||0)+.8;
      const floorHeart=(x,y,size,color)=>{const q=p(x,y,zAt(x,y));g.save();g.transform(Math.SQRT1_2,.41012,-Math.SQRT1_2,.41012,q.x,q.y);heart(g,0,0,size,color);g.restore();};
      if(data.id==='campus'){
        for(const r of data.rooms.filter(r=>r.floor==='class')){
          for(let y=r.y+30;y<r.y+r.h-20;y+=30)for(let x=r.x+30;x<r.x+r.w-20;x+=30)line(p(x,y,.5),p(x+12,y,.5),'#f9c5bd55',.6);
          const yy=r.y+r.h-10;plane(r.x+12,yy,r.w-24,3,.9,'#f2bd7855',null);
        }
        for(const [x,y] of [[660,350],[1980,550],[1940,850],[2000,960]])floorHeart(x,y,24,'#e78ca3');
      }else{
        for(const lane of data.terrace.lanes)for(let x=1050;x<1400;x+=45)for(const y of [lane[0]+15,lane[1]-15]){
          const q=p(x,y,zAt(x,y)+2);g.save();g.shadowBlur=15;g.shadowColor='#ffbf52';g.fillStyle='#fff5bc';g.fillRect(q.x-5,q.y-2,10,4);g.restore();
        }
        for(const [x,y] of [[1680,1260],[2040,1550],[820,620]])floorHeart(x,y,30,'#e6a0ac');
      }
      return;
    }

    const main=data.rooms.find(r=>r.id==='class3-4');
    for(const r of data.rooms.filter(r=>['class3-4','corridor','lounge1','lounge2'].includes(r.id))) {
      const margin=8;
      for(const [x,y,w,h] of [[r.x+margin,r.y+margin,r.w-2*margin,1.4],[r.x+margin,r.y+r.h-margin,r.w-2*margin,1.4],[r.x+margin,r.y+margin,1.4,r.h-2*margin],[r.x+r.w-margin,r.y+margin,1.4,r.h-2*margin]])plane(x,y,w,h,.3,'#d9b779aa',null);
    }
    if(main){
      const r=main,tile=48;
      for(let y=r.y+12,j=0;y<r.y+r.h-12;y+=tile,j++)for(let x=r.x+12,i=0;x<r.x+r.w-12;x+=tile,i++)
        if((i+j)%2===0)plane(x,y,Math.min(tile,r.x+r.w-12-x),Math.min(tile,r.y+r.h-12-y),.2,'#cfb49636',null);
      const cx=r.x+r.w*.5,cy=r.y+r.h*.60;
      const points=radius=>Array.from({length:80},(_,i)=>p(cx+Math.cos(i*Math.PI/40)*radius,cy+Math.sin(i*Math.PI/40)*radius*.66,.5));
      for(const [radius,color] of [[155,'#5c333c'],[148,'#dab77b'],[144,'#784754'],[131,'#673b46']]){
        g.beginPath();points(radius).forEach((a,i)=>i?g.lineTo(a.x,a.y):g.moveTo(a.x,a.y));g.closePath();g.fillStyle=color;g.fill();
      }
      for(let i=0;i<24;i++){const a=i*Math.PI/12;const q=p(cx+Math.cos(a)*138,cy+Math.sin(a)*138*.66,1);g.fillStyle='#e0bc7c';g.fillRect(q.x-1,q.y-1,2,2);}
    }
  }
  function drawAtmosphere(ctx,{projection:P,t=0}) {
    if(window.CAMPUS_SEASON!=='pepero')return;
    ctx.save();
    const sec=motion.matches?0:t/1000;
    // Small particles only: the expensive lighting and scenery are cached in sprites.
    for(let i=0;i<24;i++){
      const x=P.width*.18+((i*193.7+sec*(2+i%3))%(P.width*.68));
      const y=P.height*.17+((i*73.3-sec*3+P.height)%(P.height*.58));
      ctx.globalAlpha=.18+.2*Math.sin(sec*.7+i)**2;
      ctx.fillStyle=i%3?'#f6dfae':'#dca0ac';ctx.beginPath();ctx.arc(x,y,i%3===0?1.7:.9,0,Math.PI*2);ctx.fill();
    }
    ctx.restore();
  }
  window.CampusSeasonal={paint,paintGameLabel,paintFloor,drawAtmosphere,art};
})();
