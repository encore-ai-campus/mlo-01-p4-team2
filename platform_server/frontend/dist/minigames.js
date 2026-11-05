(() => {
 'use strict';
 function create({start,isBlocked,onOpen,onClose}){
  const $=id=>document.getElementById(id),dialog=$('minigameDialog'),status=$('minigameStatus'),retry=$('minigameRetry'),slot=$('minigameFrame');
  let active=null,starting=false;
  function csrf(){return document.cookie.split('; ').find(s=>s.startsWith('campus_csrftoken='))?.split('=')[1]||'';}
  async function save(payload,run=active){
   if(!run)return;
   status.textContent='결과를 저장하는 중이에요.';retry.hidden=true;
   try{
    const response=await fetch('/api/event-story/plays/'+run.play_uuid+'/result/',{method:'POST',credentials:'same-origin',cache:'no-store',keepalive:true,signal:AbortSignal.timeout(10000),
     headers:{'Content-Type':'application/json','X-CSRFToken':decodeURIComponent(csrf())},body:JSON.stringify(payload)});
    const data=await response.json();
    if(!response.ok){if([401,404,409].includes(response.status))run.terminal=true;throw new Error(data.error||'결과를 저장하지 못했어요.');}
    if(active!==run)return;
    run.saved=true;status.textContent=data.status==='INTERRUPTED'?'중단 기록을 저장했어요.':'결과를 저장했어요.';
    if(Array.isArray(data.achievements))window.dispatchEvent(new CustomEvent('campus:achievements',{detail:data.achievements}));
    if(Array.isArray(data.newly_completed)&&data.newly_completed.length)window.dispatchEvent(new CustomEvent('campus:achievement-earned',{detail:data}));
    if(run.restartRequested)await open({id:run.objectId},run);
   }catch(error){if(active===run){status.textContent=error.message;retry.hidden=!!run.terminal;}}
  }
  async function close(){
   if(!active){dialog.close();return;}
   active.restartRequested=false;
   if(!active.saved&&!active.terminal){
    active.payload??={duration_ms:null,status:'INTERRUPTED'};
    await save(active.payload);
    if(!active?.saved&&!active?.terminal)return;
   }
   clearTimeout(active.loadTimer);slot.replaceChildren();active=null;dialog.close();onClose();
  }
  retry.onclick=()=>active?.saved&&active.restartRequested?open({id:active.objectId},active):active?.payload?save(active.payload):close();
  $('minigameClose').onclick=close;
  dialog.addEventListener('cancel',event=>{event.preventDefault();close();});
  window.addEventListener('message',event=>{
   const run=active,d=event.data;
   if(!run||event.origin!==location.origin||event.source!==run.frame.contentWindow||!d||d.playId!==run.play_uuid||d.gameId!==run.game_code||d.version!==1)return;
   if(d.type==='minigame:ready'&&!run.initialized){
    run.initialized=true;clearTimeout(run.loadTimer);
    run.frame.contentWindow.postMessage({type:'minigame:init',version:1,gameId:run.game_code,playId:run.play_uuid,config:run.config},location.origin);
    status.textContent='게임을 진행해 주세요.';run.frame.focus();
   }else if(d.type==='minigame:result'&&run.initialized&&!run.payload){
    const r=d.result;
    const cards=run.game_code==='term_match';
    if(!r||!['SUCCESS','FAILED'].includes(r.status)||!Number.isSafeInteger(r.duration_ms)||r.duration_ms<0||Object.keys(r).length!==(cards?3:2)||!Object.hasOwn(r,'status')||!Object.hasOwn(r,'duration_ms'))return;
    if(cards&&(!Array.isArray(r.picks)||r.picks.length>64||r.picks.some(p=>!Array.isArray(p)||p.length!==3||p.some(v=>!Number.isSafeInteger(v)))))return;
    run.payload={duration_ms:r.duration_ms,status:r.status,...(cards?{picks:r.picks.map(p=>[...p])}:{})};save(run.payload);
   }else if(d.type==='minigame:restart'&&run.initialized&&run.payload&&!run.terminal){
    run.restartRequested=true;if(run.saved)open({id:run.objectId},run);
   }else if(d.type==='minigame:exit')close();
   else if(d.type==='minigame:error'){status.textContent='게임을 준비하지 못했어요. 닫은 뒤 다시 시작해 주세요.';retry.hidden=false;}
  });
  window.addEventListener('campus:freeze',event=>{if(event.detail?.blocking&&active){active.terminal=true;close();}});
  window.addEventListener('pagehide',()=>{
   if(active&&!active.saved){active.payload??={duration_ms:null,status:'INTERRUPTED'};save(active.payload);}
  });
  async function open(item,previous=null){
   if(starting||(previous?active!==previous||!previous.saved:active||isBlocked()))return;starting=true;if(!previous)onOpen();
   try{
    const data=await start(item.id);
    if(!['timer','typing','term_match'].includes(data.game_code)||typeof data.play_uuid!=='string')throw new Error('게임 정보를 확인해 주세요.');
    if(previous&&active!==previous){await save({duration_ms:null,status:'INTERRUPTED'},data);return;}
    if(previous)clearTimeout(previous.loadTimer);
    active={...data,objectId:item.id,initialized:false,saved:false,payload:null};
    const run=active,frame=document.createElement('iframe');run.frame=frame;frame.title=data.game_code==='term_match'?'페어링 정원':data.game_name;frame.dataset.sessionId=data.play_uuid;
    $('minigameTitle').textContent=frame.title;status.textContent='게임을 불러오는 중이에요.';retry.hidden=true;
    frame.src='/static/campus/minigames/'+data.game_code+'.html?v=20261105-typing-replay-v1';slot.replaceChildren(frame);dialog.showModal();
    run.loadTimer=setTimeout(()=>{if(active===run&&!run.initialized){status.textContent='게임을 불러오지 못했어요. 닫은 뒤 다시 시도해 주세요.';retry.hidden=!!run.terminal;}},15000);
   }catch(error){if(active){status.textContent=error.message;retry.hidden=false;}else{onClose();throw error;}}
   finally{starting=false;}
  }
  return {open,get blocking(){return starting||active!==null;}};
 }
 window.CampusMinigames={create};
})();
