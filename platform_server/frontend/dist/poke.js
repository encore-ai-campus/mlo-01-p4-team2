/* A server-accepted, transient hand overlay; character and wallet positions stay untouched. */
(() => {
  'use strict';
  const diagonal=Math.SQRT1_2;
  const vectors={up:[0,-1],down:[0,1],left:[-1,0],right:[1,0],
    'up-left':[-diagonal,-diagonal],'up-right':[diagonal,-diagonal],
    'down-left':[-diagonal,diagonal],'down-right':[diagonal,diagonal]};
  const reducedMotion=window.matchMedia('(prefers-reduced-motion: reduce)');
  const duration=220,reactionDuration=240,interval=250;
  function directionVector(P,dir){
    const [x,y]=vectors[dir],floor=P.inverseVector(x,y),scale=1/Math.hypot(floor.x,floor.y);
    return {x:x*scale,y:y*scale};
  }
  function effect(actor,key,t,limit){
    const value=actor[key];
    if(!value)return null;
    if(t-value.started>=limit){delete actor[key];return null;}
    return value;
  }
  function targets(floor){
    return (window.CampusWorlds?.[floor]?.objects||[]).filter(o=>o.action==='guard_sandbag').map(o=>o._guardTarget||(o._guardTarget={id:o.id,x:o.x+o.w/2,y:o.y+o.h/2,guarding:true}));
  }
  function action(actor,t){
    const loop=actor._campusPokeLoop;
    if(loop){
      if(Number.isFinite(loop.lastHit)&&t-loop.lastHit>350)loop.reach=72;
      if(Number.isFinite(loop.updatedAt)&&t-loop.updatedAt>1600){loop.paused=true;return null;}
      else{loop.paused=false;const phase=Math.max(0,t-loop.started)%interval;return {...loop,started:t-phase,stroke:Math.floor(Math.max(0,t-loop.started)/interval),loop:true};}
    }
    return effect(actor,'_campusPoke',t,duration);
  }
  function pose(actor,t){
    const current=action(actor,t);
    if(!current)return actor;
    const parts=current.dir.split('-'),dir=parts.includes(actor.dir)?actor.dir:parts.at(-1);
    return {...actor,dir,moving:current.loop?actor.moving:false};
  }
  function react(ctx,actor,t,P){
    const deflect=effect(actor,'_campusPokeDeflect',t,260);
    if(deflect&&!reducedMotion.matches){const p=(t-deflect.started)/260,v=directionVector(P,deflect.dir);ctx.translate(-v.x*Math.sin(p*Math.PI)*7,Math.sin(p*Math.PI*4)*2);ctx.rotate(Math.sin(p*Math.PI)*-.12);}
    const hit=effect(actor,'_campusPokeReaction',t,reactionDuration);
    if(!hit||reducedMotion.matches)return;
    const p=(t-hit.started)/reactionDuration,fade=Math.max(0,1-p);
    const v=directionVector(P,hit.dir);
    ctx.translate(v.x*Math.sin(p*Math.PI)*3,Math.sin(p*Math.PI*5)*1.2*fade);
    ctx.rotate(Math.sin(p*Math.PI*3)*.11*fade);
  }
  function guard(ctx,actor,t){
    if(!actor.guarding&&!actor._campusGuardPulse)return;
    const pulse=actor._campusGuardPulse?Math.max(0,1-(t-actor._campusGuardPulse.started)/420):0;
    if(actor._campusGuardPulse&&pulse<=0)delete actor._campusGuardPulse;
    const moving=!reducedMotion.matches,phase=moving?t/520:0,breath=moving?Math.sin(phase)*.65:0;
    ctx.save();
    // The low, open ring and side crescents leave the face and torso readable.
    ctx.fillStyle='#6b39442c';ctx.beginPath();ctx.ellipse(0,1,27+pulse*3,5.5+pulse,0,0,Math.PI*2);ctx.fill();
    ctx.strokeStyle='#6d3a42';ctx.lineWidth=3.2;ctx.globalAlpha=.85;
    ctx.beginPath();ctx.ellipse(0,1,28+pulse*4,6+pulse,0,0,Math.PI*2);ctx.stroke();
    ctx.strokeStyle='#f4cd84';ctx.lineWidth=1.5;ctx.globalAlpha=.96;
    ctx.beginPath();ctx.ellipse(0,0,26+pulse*4,5+pulse,0,.12*Math.PI,.88*Math.PI);ctx.stroke();
    ctx.beginPath();ctx.ellipse(0,0,26+pulse*4,5+pulse,0,1.12*Math.PI,1.88*Math.PI);ctx.stroke();
    for(const side of [-1,1]){
      const x=side*(23+pulse*2),top=-32+breath;
      ctx.save();
      ctx.fillStyle='#f6dcae38';ctx.globalAlpha=.7;
      ctx.beginPath();ctx.moveTo(side*21,-7);ctx.bezierCurveTo(side*29,-13,side*31,top+12,side*18,top);ctx.lineTo(side*22,top+7);ctx.bezierCurveTo(side*29,top+14,side*27,-13,side*21,-7);ctx.fill();
      ctx.globalAlpha=.96;ctx.lineCap='round';
      ctx.strokeStyle='#6a3941';ctx.lineWidth=5.5;
      ctx.beginPath();ctx.moveTo(side*22,-7);ctx.bezierCurveTo(side*30,-16,side*30,top+9,side*18,top);ctx.stroke();
      ctx.strokeStyle='#f4c982';ctx.lineWidth=2.5;
      ctx.beginPath();ctx.moveTo(side*22,-7);ctx.bezierCurveTo(side*30,-16,side*30,top+9,side*18,top);ctx.stroke();
      ctx.strokeStyle='#fff2cf';ctx.lineWidth=.75;
      ctx.beginPath();ctx.moveTo(side*23,-10);ctx.bezierCurveTo(side*28,-17,side*28,top+9,side*19,top+2);ctx.stroke();
      // A small chocolate-and-strawberry Pepero crest anchors each edge.
      ctx.translate(x,-19+breath);ctx.rotate(side*.23);
      ctx.strokeStyle='#f2d9a5';ctx.lineWidth=5;ctx.beginPath();ctx.moveTo(0,11);ctx.lineTo(0,-9);ctx.stroke();
      ctx.strokeStyle='#543038';ctx.lineWidth=6;ctx.beginPath();ctx.moveTo(0,5);ctx.lineTo(0,-11);ctx.stroke();
      ctx.strokeStyle='#f1a6b4';ctx.lineWidth=1.5;
      for(const y of [-7,-1]){ctx.beginPath();ctx.moveTo(-2.5,y);ctx.lineTo(2.5,y-1.5);ctx.stroke();}
      ctx.fillStyle='#fff3d5';ctx.beginPath();ctx.moveTo(0,-15);ctx.lineTo(3.5,-11);ctx.lineTo(0,-7);ctx.lineTo(-3.5,-11);ctx.closePath();ctx.fill();
      ctx.restore();
    }
    if(actor.guarding){
      for(let i=0;i<4;i++){
        const a=i*Math.PI/2+phase*.22,r=29;
        ctx.fillStyle=i%2?'#f3b8c4':'#ffe8b2';ctx.globalAlpha=.65;
        ctx.beginPath();ctx.arc(Math.cos(a)*r,Math.sin(a)*7-4,1.2,0,Math.PI*2);ctx.fill();
      }
    }
    if(pulse>0){
      const spread=1-pulse;ctx.globalAlpha=pulse;
      ctx.strokeStyle='#fff1cc';ctx.lineWidth=2;
      for(const side of [-1,1]){
        ctx.beginPath();ctx.ellipse(0,-24,30+spread*19,24+spread*10,0,side<0?.65*Math.PI:-.35*Math.PI,side<0?1.35*Math.PI:.35*Math.PI);ctx.stroke();
      }
      for(let i=0;i<6;i++){
        const a=i*Math.PI/3+Math.PI/6,r=31+spread*(moving?17:5),x=Math.cos(a)*r,y=Math.sin(a)*r*.64-22;
        ctx.save();ctx.translate(x,y);ctx.rotate(Math.PI/4);ctx.fillStyle=i%2?'#f5b3bf':'#ffe5ab';ctx.fillRect(-2,-2,4,4);ctx.restore();
      }
    }
    ctx.restore();
  }

  function draw(ctx,actor,t,P,behind){
    const active=action(actor,t);
    if(active&&window.CampusSwing){window.CampusSwing.drawWeapon(ctx,actor,t,P,behind,active);return;}
    if(!active&&window.CampusSwing){window.CampusSwing.drawCarry(ctx,actor,t,behind);return;}
    const current=active||(actor.peperoRemaining>0?{dir:actor.dir,started:t}:null);
    if(!current)return;
    const v=directionVector(P,current.dir);
    if((v.y<0)!==behind)return;
    const progress=Math.max(0,(t-current.started)/duration);
    const extension=!active?0:reducedMotion.matches?1:Math.sin(Math.PI*progress);
    const size=window.CampusAvatars?.size||40,start=size*.18,maxReach=Math.max(start,(current.reach||72)-size*.68);
    const reach=start+(maxReach-start)*extension;
    ctx.save();ctx.strokeStyle=actor.color||window.CampusAvatars?.definitions[actor.avatar]?.color||'#4e9b91';ctx.lineWidth=3;ctx.lineCap='round';
    ctx.beginPath();ctx.moveTo(v.x*size*.12,-size*.32+v.y*size*.12);ctx.lineTo(v.x*(reach-3),-size*.32+v.y*(reach-3));ctx.stroke();ctx.restore();
    ctx.save();ctx.translate(v.x*reach,-size*.32+v.y*reach);ctx.rotate(Math.atan2(v.y,v.x));ctx.scale(size/100,size/100);
    const pieces=Math.max(0,Math.min(5,Math.ceil((actor.peperoRemaining??10)/2))),length=12+pieces*7;
    ctx.strokeStyle='#d8aa63';ctx.lineWidth=8;ctx.lineCap='round';ctx.beginPath();ctx.moveTo(-12,0);ctx.lineTo(length,0);ctx.stroke();
    ctx.strokeStyle='#5c302a';ctx.lineWidth=11;ctx.beginPath();ctx.moveTo(0,0);ctx.lineTo(length,0);ctx.stroke();
    for(let i=3;i<length;i+=8){ctx.fillStyle=i%3?'#ffc2ce':'#fff0c5';ctx.fillRect(i,-3,3,2);}
    ctx.fillStyle='#ffe5ce';ctx.beginPath();ctx.ellipse(-13,3,8,7,0,0,Math.PI*2);ctx.fill();
    ctx.restore();
  }
  function debris(ctx,age,count,radius){
    const motion=reducedMotion.matches?0:age;
    for(let i=0;i<count;i++){
      const a=i*2.39996,r=radius*(.2+motion)*(.55+(i%4)*.15);
      ctx.save();ctx.translate(Math.cos(a)*r,Math.sin(a)*r*.65+motion*motion*22);ctx.rotate(a+motion*4);
      ctx.fillStyle=['#603329','#e0b477','#8c4b32','#ffd0d9'][i%4];ctx.fillRect(-1.3,-1,2.6+(i%2),2.2);ctx.restore();
    }
  }
  function impact(ctx,actor,t){
    const crumbs=effect(actor,'_peperoCrumbs',t,460);
    if(crumbs){const age=(t-crumbs.started)/460;ctx.save();ctx.translate(0,-18);ctx.globalAlpha*=1-age;debris(ctx,age,14,23);ctx.restore();}
    const broken=effect(actor,'_peperoBreak',t,850);
    if(broken){
      const age=(t-broken.started)/850,motion=reducedMotion.matches?0:age,length=(7+Math.ceil(broken.before/2)*7)/2;
      ctx.save();ctx.translate(broken.x,broken.y);ctx.globalAlpha*=Math.min(1,(1-age)*2);
      for(const side of [-1,1]){
        ctx.save();ctx.translate(side*(3+motion*24),-Math.sin(motion*Math.PI)*16+motion*motion*34);ctx.rotate(side*(.45+motion*3));
        ctx.strokeStyle='#e1b471';ctx.lineWidth=3;ctx.lineCap='round';ctx.beginPath();ctx.moveTo(0,0);ctx.lineTo(side*length,0);ctx.stroke();
        ctx.strokeStyle='#603329';ctx.lineWidth=4;ctx.beginPath();ctx.moveTo(side*2,0);ctx.lineTo(side*length,0);ctx.stroke();
        ctx.fillStyle='#f5d49a';ctx.beginPath();ctx.moveTo(0,-2);ctx.lineTo(side*3,0);ctx.lineTo(0,2);ctx.fill();ctx.restore();
      }
      debris(ctx,age,10,18);ctx.restore();
    }
    const surprise=effect(actor,'_peperoSurprised',t,1100);
    if(surprise){
      const age=(t-surprise.started)/1100;ctx.save();ctx.globalAlpha*=Math.min(1,(1-age)*4);
      // A face emote remains readable for all four avatar directions.
      ctx.translate(18,-49-(reducedMotion.matches?0:Math.sin(Math.min(1,age*5)*Math.PI)*3));
      ctx.fillStyle='#fff5de';ctx.strokeStyle='#b86b70';ctx.lineWidth=1;ctx.beginPath();ctx.roundRect(-13,-11,26,22,7);ctx.fill();ctx.stroke();
      ctx.fillStyle='#653738';for(const x of [-5,4]){ctx.beginPath();ctx.ellipse(x,-2,1.8,3,0,0,Math.PI*2);ctx.fill();}
      ctx.beginPath();ctx.ellipse(0,5,2.5,3,0,0,Math.PI*2);ctx.stroke();
      ctx.fillStyle='#71bfd2';ctx.beginPath();ctx.moveTo(10,-9);ctx.quadraticCurveTo(16,-1,11,0);ctx.quadraticCurveTo(7,-1,10,-9);ctx.fill();
      ctx.fillStyle='#e47769';ctx.font='bold 13px sans-serif';ctx.fillText('!',-19,-6);ctx.restore();
      if(actor.dir!=='up'){
        ctx.save();ctx.globalAlpha*=Math.min(1,(1-age)*4);const x=actor.dir==='left'?-3:actor.dir==='right'?3:0;
        ctx.fillStyle='#fff5df';ctx.fillRect(x-4,-29,3,3);ctx.fillRect(x+1,-29,3,3);ctx.fillStyle='#513133';ctx.fillRect(x-3,-28,1,2);ctx.fillRect(x+2,-28,1,2);ctx.beginPath();ctx.ellipse(x,-23,1.5,2,0,0,Math.PI*2);ctx.fill();ctx.restore();
      }
    }
  }
  function contact(ctx,actor,t,P){
    impact(ctx,actor,t);
    const hit=effect(actor,'_campusPokeReaction',t,reactionDuration);
    if(!hit)return;
    const age=Math.max(0,(t-hit.started)/reactionDuration),v=directionVector(P,hit.dir);
    ctx.save();ctx.translate(-v.x*9,-13-v.y*6);ctx.globalAlpha*=1-age;
    ctx.strokeStyle='#d9a64e';ctx.lineWidth=1.4;ctx.lineCap='round';
    for(let i=0;i<5;i++){const a=i*Math.PI*2/5;ctx.beginPath();ctx.moveTo(Math.cos(a)*3,Math.sin(a)*3);ctx.lineTo(Math.cos(a)*6.5,Math.sin(a)*6.5);ctx.stroke();}
    ctx.restore();
  }
  function create({getPlayer,getPresence,getClassroom,getInput=()=>[],isBlocked,notify=()=>{}}){
    const button=document.getElementById('campusPokeButton'),held=new Set(),seen=new Set();
    let remaining=0;
    const meter=document.createElement('div');meter.id='peperoMeter';meter.setAttribute('role','status');meter.setAttribute('aria-live','polite');
    Object.assign(meter.style,{position:'fixed',right:'20px',top:'108px',zIndex:'25',padding:'12px 16px',border:'1px solid #e0b893',borderRadius:'16px',background:'#fff4e8ed',color:'#643647',boxShadow:'0 5px 24px #4a233322',font:'600 13px sans-serif',pointerEvents:'none',maxWidth:'240px'});
    document.body.append(meter);
    function updateMeter(){meter.textContent=remaining?`빼빼로 ${remaining}/10`:'빼빼로 0/10 · 빈손 휘두르기 가능 · 상자 앞 E로 리필';}
    updateMeter();
    let lastContext='',readyAt=0,pulseTimer=null,suppressClick=false;
    function context(){const classroom=getClassroom();return classroom.blocking?{context:'classroom',subject:classroom.subject}:{context:'world',floor:getPlayer().floor};}
    function selfActor(){return getClassroom().blocking?getClassroom().actor:getPlayer();}
    function actors(){return [getPlayer(),...getPresence().peers.values(),getClassroom().actor,...(getClassroom().residents||[])].filter(Boolean);}
    function finish(actor){
      const loop=actor?._campusPokeLoop;if(!loop)return;
      const now=performance.now(),phase=Math.max(0,now-loop.started)%interval;
      if(phase<duration&&(!Number.isFinite(actor.receivedAt)||now-actor.receivedAt<=750))actor._campusPoke={...loop,started:now-phase};
      delete actor._campusPokeLoop;
    }
    function facing(actor,t){
      const keys=new Set(getInput()),dx=Number(keys.has('d')||keys.has('arrowright'))-Number(keys.has('a')||keys.has('arrowleft')),
        dy=Number(keys.has('s')||keys.has('arrowdown'))-Number(keys.has('w')||keys.has('arrowup'));
      let direction=dx&&dy?(dy<0?'up':'down')+'-'+(dx<0?'left':'right'):dx?(dx<0?'left':'right'):dy?(dy<0?'up':'down'):null;
      if(dx&&dy)actor._campusPokeDiagonalAt=t;
      else if(direction&&actor._campusPokeFacing?.includes('-')&&t-(actor._campusPokeDiagonalAt||0)<80)direction=actor._campusPokeFacing;
      if(direction)actor._campusPokeFacing=direction;
      return actor._campusPokeFacing||actor.dir;
    }
    function predict(t){
      const actor=selfActor();
      if(!actor||actor._campusPokeLoop||t<readyAt)return;
      actor._campusPokeLoop={id:'local',started:t,updatedAt:t,dir:facing(actor,t),reach:72};
      delete actor._campusPoke;readyAt=t+interval;
    }
    function setHeld(active,source='keyboard'){
      if(active){
        if(held.has(source)||isBlocked()||document.hidden||getInput().includes('control'))return;
        if(!getPresence().connected){notify('연결된 뒤 찌르기를 이용해 주세요.');return;}
        held.add(source);
        getPresence().setPoking(true,context(),getInput());predict(performance.now());
      }else{
        held.delete(source);
        if(!held.size){getPresence().setPoking(false,null,getInput());finish(selfActor());}
      }
    }
    function stop(clearMovement=false){
      held.clear();clearTimeout(pulseTimer);pulseTimer=null;
      getPresence().setPoking(false,null,clearMovement||isBlocked()||document.hidden?[]:getInput());finish(selfActor());
    }
    function trigger(){
      if(performance.now()<readyAt)return;
      setHeld(true,'pulse');clearTimeout(pulseTimer);
      pulseTimer=setTimeout(()=>setHeld(false,'pulse'),90);
    }
    function receive(packet){
      if(packet?.type==='pepero_status'){
        if(!Number.isInteger(packet.remaining)||packet.remaining<0||packet.remaining>10)return;
        remaining=packet.remaining;getPlayer().peperoRemaining=remaining;
        if(getClassroom().actor)getClassroom().actor.peperoRemaining=remaining;
        updateMeter();return;
      }
      if(packet?.type==='pepero_inventory'){
        const view=context(),presence=getPresence();
        if(packet.context!==view.context||(view.context==='classroom'?packet.subject!==view.subject:packet.floor!==view.floor))return;
        if(!Number.isInteger(packet.remaining)||packet.remaining<0||packet.remaining>10)return;
        if(packet.actorId===presence.selfId){receive({type:'pepero_status',remaining:packet.remaining});return;}
        const peer=presence.peers.get(packet.actorId);
        if(peer)peer.peperoRemaining=packet.remaining;
        return;
      }
      if(!packet||typeof packet.id!=='string' ||typeof packet.actorId!=='string'||!Object.hasOwn(vectors,packet.dir))return;
      const view=context(),presence=getPresence(),own=packet.actorId===presence.selfId;
      if(packet.context!==view.context||(view.context==='classroom'?packet.subject!==view.subject||!own:packet.floor!==view.floor))return;
      const classroom=getClassroom(),source=view.context==='classroom'?classroom.actor:own?getPlayer():presence.peers.get(packet.actorId);
      if(!source)return;
      if(Number.isInteger(packet.remaining))source.peperoRemaining=packet.remaining;
      const now=performance.now();
      if(packet.type==='poke_state'){
        if(typeof packet.active!=='boolean')return;
        if(!packet.active){if(source._campusPokeLoop?.id===packet.id)finish(source);return;}
        if(own&&!held.size)return;
        const phase=Number.isFinite(packet.phaseMs)?Math.max(0,Math.min(interval-1,packet.phaseMs)):0;
        const old=source._campusPokeLoop;
        const same=old?.id===packet.id;
        source._campusPokeLoop={id:packet.id,dir:packet.dir,started:same?old.started:now-phase-(packet.stroke===1?interval:0),updatedAt:now,reach:same?old.reach:72,lastHit:same?old.lastHit:undefined};
        delete source._campusPoke;
        if(own){source._campusPokeFacing=packet.dir;readyAt=Math.max(readyAt,now+interval-phase);}
        return;
      }
      if(!['poke_hit','poke'].includes(packet.type)||(packet.targetId!==null&&typeof packet.targetId!=='string')||seen.has(packet.id))return;
      seen.add(packet.id);if(seen.size>128)seen.delete(seen.values().next().value);
      const target=view.context==='classroom'?classroom.residents.find(actor=>actor.id===packet.targetId):packet.targetId===presence.selfId?getPlayer():(presence.peers.get(packet.targetId)||targets(view.floor).find(a=>a.id===packet.targetId));
      if(packet.peperoBroken===true&&packet.peperoBefore>0){
        const map=window.CampusWorlds?.[view.floor],angle=map?.angle??Math.PI/4,tilt=map?.tilt??.58,dx=target?target.x-source.x:0,dy=target?target.y-source.y:0;
        const q=target?{x:Math.cos(angle)*dx-Math.sin(angle)*dy,y:(Math.sin(angle)*dx+Math.cos(angle)*dy)*tilt}:null;
        const v=vectors[packet.dir];source._peperoBreak={started:now,before:packet.peperoBefore,x:q?q.x*.65:v[0]*20,y:(q?q.y*.65:v[1]*20)-18};
        source._peperoSurprised={started:now};
      }else if(!packet.blocked&&packet.peperoBefore>0&&target){target._peperoCrumbs={started:now};}
      const distance=target?Math.hypot(target.x-source.x,target.y-source.y):72,reach=Number.isFinite(distance)?Math.min(72,distance):72;
      if(source._campusPokeLoop){source._campusPokeLoop.reach=reach;source._campusPokeLoop.lastHit=now;}
      if(packet.type==='poke')source._campusPoke={started:now,dir:packet.dir,reach};
      if(packet.blocked&&target){target._campusGuardPulse={started:now};source._campusPokeDeflect={started:now,dir:packet.dir};}
      else if(target)target._campusPokeReaction={started:now,dir:packet.dir};
    }
    function tick(t){
      const current=JSON.stringify(context());
      if(current!==lastContext){stop();for(const actor of actors()){delete actor._campusPokeLoop;delete actor._campusPoke;delete actor._campusPokeReaction;delete actor._campusPokeFacing;delete actor._campusPokeDiagonalAt;}lastContext=current;}
      if(isBlocked()||!getPresence().connected||document.hidden||getInput().includes('control'))stop();
      const actor=selfActor();if(actor){actor.peperoRemaining=remaining;facing(actor,t);if(held.size){predict(t);if(actor._campusPokeLoop){actor._campusPokeLoop.dir=actor._campusPokeFacing||actor.dir;actor._campusPokeLoop.updatedAt=t;}}}
      if(button){const disabled=!getPresence().connected||isBlocked();if(button.disabled!==disabled)button.disabled=disabled;button.setAttribute('aria-pressed',String(held.size>0));}
    }
    lastContext=JSON.stringify(context());
    document.addEventListener('keyup',event=>{
      const key=window.CampusKeyboard.key(event);
      if(key==='z')setHeld(false,'keyboard');
      if(key==='enter')setHeld(false,'button-key');
    });
    window.addEventListener('blur',()=>stop(true));window.addEventListener('pagehide',()=>stop(true));
    document.addEventListener('visibilitychange',()=>{if(document.hidden)stop();});
    document.addEventListener('focusin',event=>{if(event.target!==button&&event.target.closest?.('input,textarea,select,button,a,[contenteditable="true"]'))stop();});
    button?.addEventListener('pointerdown',event=>{if(event.button!==0)return;event.preventDefault();suppressClick=true;button.focus({preventScroll:true});button.setPointerCapture(event.pointerId);setHeld(true,'pointer');});
    for(const type of ['pointerup','pointercancel','lostpointercapture'])button?.addEventListener(type,()=>setHeld(false,'pointer'));
    button?.addEventListener('click',()=>{if(suppressClick){suppressClick=false;return;}trigger();});
    button?.addEventListener('keydown',event=>{
      if((event.isComposing&&!window.CampusKeyboard.isGameKey(event))||(event.ctrlKey&&!event.code.startsWith('Control'))||event.altKey||event.metaKey)return;
      const key=window.CampusKeyboard.key(event);
      if(key==='z'||key==='enter'){event.preventDefault();event.stopImmediatePropagation();if(!event.repeat)setHeld(true,key==='z'?'keyboard':'button-key');}
    });
    return {receive,trigger,setHeld,stop,tick,get held(){return held.size>0;}};
  }
  window.CampusPoke={create,pose,react,draw,contact,guard,action,targets};
})();
