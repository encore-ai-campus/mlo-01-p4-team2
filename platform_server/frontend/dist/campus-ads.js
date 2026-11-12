/* 서버 회차와 이동 승인을 사용하며 광고 실패가 게임 이동을 막지 않게 한다. */
(() => {
  'use strict';
  const {ContinuousExposure,visibleRatio}=window.CampusAdVisibility;
  const detector=new ContinuousExposure();
  let roof=null,nextRoof=0,roofLoading=false,portalVersion=0,portalAds=[];
  // CSS 화면 픽셀 기준. 진입과 이탈 여유를 달리해 경계의 작은 이동에는 재요청하지 않는다.
  const PREFETCH_MARGIN=256,EXIT_MARGIN=384;
  let roofZone=null,roofScheduleView=null;
  const reporting=new Map();
  const csrf=()=>document.querySelector('[name=csrfmiddlewaretoken]')?.value||decodeURIComponent(document.cookie.match(/(?:^|; )(?:campus_csrftoken|csrftoken)=([^;]*)/)?.[1]||'');
  async function post(path,body={},timeout=5000){
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),timeout);
    try{
      const response=await fetch('/api/ads/'+path,{method:'POST',credentials:'same-origin',signal:controller.signal,
        headers:{'Content-Type':'application/json','X-CSRFToken':csrf()},body:JSON.stringify(body)});
      if(!response.ok)throw Error('ad_unavailable');
      return await response.json();
    }finally{clearTimeout(timer);}
  }
  function safeUrl(raw){const url=new URL(raw,location.href);if(!['https:','http:'].includes(url.protocol))throw Error('ad_url');return url.href;}
  function clickForm(entry){
    const form=document.createElement('form');
    form.method='POST';form.action='/api/ads/decisions/'+encodeURIComponent(entry.decision_id)+'/click/';
    // POST의 Origin을 유지해야 서버의 CSRF 검사를 통과한다.
    form.target='_blank';form.rel='noopener';
    for(const [name,value] of [['csrfmiddlewaretoken',csrf()],['token',entry.ad.click_token]]){
      const input=document.createElement('input');input.type='hidden';input.name=name;input.value=value;form.append(input);
    }
    return form;
  }
  function image(ad,cors=false){return new Promise((resolve,reject)=>{
    const img=new Image(),timer=setTimeout(()=>{img.onload=null;img.onerror=null;reject(Error('image_timeout'));},4000);if(cors)img.crossOrigin='anonymous';
    img.onload=()=>{clearTimeout(timer);resolve(img);};img.onerror=()=>{clearTimeout(timer);reject(Error('image_failed'));};
    try{img.src=safeUrl(ad.image_url);}catch(error){clearTimeout(timer);reject(error);}
  });}
  function report(decision){
    if(reporting.has(decision))return;
    reporting.set(decision,true);
    // 접수 응답 유실 시 같은 결정을 한 번 재전송한다. 백엔드가 최초 반응 ID를 재사용한다.
    const send=()=>post('decisions/'+decision+'/impression/');
    send().catch(()=>setTimeout(()=>send().catch(()=>{}),1000));
  }
  function stopRoof(){
    if(roofZone&&roofLoading)nextRoof=0;
    roofZone=null;
  }
  async function refreshRoof(zone){
    const view=zone.view,current=()=>roofZone===zone&&!document.hidden;
    roofLoading=true;roofScheduleView=view;
    try{
      const started=performance.now(),batch=await post('placements/rooftop/');
      if(!current())return;
      const remaining=Date.parse(batch.expires_at)-Date.parse(batch.server_time)-(performance.now()-started);
      if(!Number.isFinite(remaining))throw Error('ad_expiry');
      if(remaining<=0){nextRoof=performance.now()+100;return;}
      if(batch.state==='PENDING'){nextRoof=performance.now()+250;return;}
      // 서버 회차는 다음 조회 시각으로만 사용하고, 표시 중인 광고를 먼저 지우지 않는다.
      const deadline=performance.now()+remaining;nextRoof=deadline;
      const entry=batch.ads[0];
      if(batch.state!=='READY'||!entry?.ad){zone.settled=true;return;}
      const candidate={...entry,ad:{...entry.ad,click_url:safeUrl(entry.ad.click_url)},view};
      const img=await image(candidate.ad,true);
      // 로딩 중 회차가 끝난 후보는 버리되, 기존 이미지·링크·노출 측정은 유지한다.
      if(!current()||performance.now()>=deadline)return;
      if(view.setDisplayImage('roof-ad-board',img)){
        // 실제 이미지 교체가 성공한 순간에만 클릭 대상과 새 광고의 노출 측정을 바꾼다.
        if(roof?.decision_id!==candidate.decision_id)detector.reset('roof');
        roof=candidate;zone.settled=true;
      }
    }catch{if(current())nextRoof=performance.now()+5000;}
    finally{roofLoading=false;}
  }
  function roofGeometry(renderer,camera,canvas,width,height){
    const bounds=canvas.getBoundingClientRect();
    if(!(width>0&&height>0&&bounds.width>0&&bounds.height>0&&camera.zoom>0))return null;
    const rect={left:Math.max(0,bounds.left),top:Math.max(0,bounds.top),right:Math.min(innerWidth,bounds.right),bottom:Math.min(innerHeight,bounds.bottom)};
    if(rect.right<=rect.left||rect.bottom<=rect.top)return null;
    const project=polygon=>polygon?.map(p=>({x:bounds.left+(p.x*camera.zoom+camera.x)*bounds.width/width,y:bounds.top+(p.y*camera.zoom+camera.y)*bounds.height/height}));
    const points=project(renderer.displayPolygon('roof-ad-board',{placeholder:true}));
    if(!points?.length||!points.every(p=>Number.isFinite(p.x)&&Number.isFinite(p.y)))return null;
    return {points,rect,project};
  }
  function tick({now,floor,renderer,camera,canvas,width,height,blocked}){
    const adFloor=floor==='rooftop'||(floor==='campus'&&renderer.hasDisplay?.('roof-ad-board'));
    const geometry=adFloor&&!blocked&&!document.hidden?roofGeometry(renderer,camera,canvas,width,height):null;
    const margin=roofZone?.view===renderer?EXIT_MARGIN:PREFETCH_MARGIN;
    const near=geometry&&visibleRatio(geometry.points,{left:geometry.rect.left-margin,top:geometry.rect.top-margin,right:geometry.rect.right+margin,bottom:geometry.rect.bottom+margin})>0;
    if(roofZone&&(!near||roofZone.view!==renderer))stopRoof();
    if(near){
      if(!roofZone){
        roofZone={view:renderer,settled:false};
        if(roofScheduleView!==renderer)nextRoof=0;
      }
      // 화면 밖에서는 접근당 한 번 미리 받는다. 표시 중에만 다음 회차를 계속 조회한다.
      const visible=visibleRatio(geometry.points,geometry.rect)>0;
      if(!roofLoading&&now>=nextRoof&&(!roofZone.settled||visible))refreshRoof(roofZone);
    }
    let ratio=0;
    if(roof&&roof.view===renderer&&geometry&&document.hasFocus()){
      const points=geometry.project(renderer.displayPolygon('roof-ad-board'));
      if(points)ratio=visibleRatio(points,geometry.rect);
    }
    if(detector.observe('roof',roof?.decision_id,ratio,now))report(roof.decision_id);
  }
  function stopPortal(){
    portalVersion++;portalAds=[];detector.reset();
    const slot=document.getElementById('floorTransitionAdSlot');slot.replaceChildren();slot.hidden=true;
  }
  async function startPortal(token){
    stopPortal();const version=portalVersion;if(!token)return;
    try{
      const batch=await post('placements/portal/',{token},2500);
      if(version!==portalVersion||!document.getElementById('floorTransitionDialog').open||batch.state!=='READY')return;
      const slot=document.getElementById('floorTransitionAdSlot');
      await Promise.all(batch.ads.map(async(entry,index)=>{
        if(!entry.ad)return;
        try{
          const img=await image(entry.ad);
          if(version!==portalVersion||!document.getElementById('floorTransitionDialog').open)return;
          safeUrl(entry.ad.click_url);
          const form=clickForm(entry),button=document.createElement('button');
          form.className='portal-ad portal-ad-'+index;button.type='submit';button.className='portal-ad-button';
          img.alt=entry.ad.title;button.append(img);form.append(button);slot.append(form);slot.hidden=false;
          portalAds.push({entry,img,index});
        }catch{ /* 로딩하지 못한 이미지는 표시·노출 보고하지 않는다. */ }
      }));
    }catch{ /* 광고 실패에도 3초 이동 화면은 계속 진행한다. */ }
  }
  function portalTick(now){
    for(const {entry,img,index} of portalAds){
      const r=img.getBoundingClientRect(),points=[{x:r.left,y:r.top},{x:r.right,y:r.top},{x:r.right,y:r.bottom},{x:r.left,y:r.bottom}];
      const active=!document.hidden&&document.hasFocus()&&document.getElementById('floorTransitionDialog').open;
      const ratio=active?visibleRatio(points,{left:0,top:0,right:innerWidth,bottom:innerHeight}):0;
      if(detector.observe('portal-'+index,entry.decision_id,ratio,now,active))report(entry.decision_id);
    }
    requestAnimationFrame(portalTick);
  }
  document.addEventListener('visibilitychange',()=>{detector.reset();if(document.hidden)stopRoof();});
  window.addEventListener('blur',()=>detector.reset());
  window.addEventListener('campus:floor-transition-end',stopPortal);
  requestAnimationFrame(portalTick);
  window.CampusAds={tick,startPortal,stopPortal,clickRoof(){
    if(!roof)return false;
    const form=clickForm(roof);form.hidden=true;document.body.append(form);form.submit();form.remove();return true;
  }};
})();
