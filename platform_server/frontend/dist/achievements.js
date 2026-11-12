/* Achievement state comes only from the authenticated API and personal socket. */
(() => {
  'use strict';
  const names=['수상한 친화력','참을성 테스트','뱀을 길들이는 자','데이터의 비밀문','찰나의 기적','손가락에 불났다','부서지기 직전','컴퓨터의 속마음','꾸왕이의 비밀 레시피'];
  function create({isBlocked=()=>false,onOpen=()=>{},onClose=()=>{}}={}){
    const $=id=>document.getElementById(id),button=$('achievementsOpen'),dialog=$('achievementsDialog');
    const list=$('achievementsList'),status=$('achievementsStatus'),retry=$('achievementsRetry');
    let items=null,pending=null,version=0,mainBadge=null;
    const discoveries=new Map();
    function rememberDiscoveries(rows){
      if(!Array.isArray(rows))return;
      for(const row of rows){
        if(typeof row?.event_uuid==='string'&&row.discovered===true&&typeof row.first_discoverer?.nickname==='string')
          discoveries.set(row.event_uuid,row.first_discoverer);
      }
    }
    window.addEventListener('campus:achievement-discoveries',event=>{
      rememberDiscoveries(event.detail);if(items)render();
    });
    const notice=$('achievementNotice'),noticeQueue=[],announced=new Set();
    let noticeActive=false,noticeTimer=null;
    function showNotice(){
      const item=noticeQueue.shift();
      if(!item){noticeActive=false;return;}
      $('achievementNoticeImage').src=item.image_url;
      $('achievementNoticeName').textContent=item.name;
      notice.hidden=false;
      (document.querySelector?.('dialog[open]')||document.body)?.append(notice);
      if(notice.showPopover)notice.showPopover();
      noticeTimer=setTimeout(dismissNotice,6000);
    }
    function dismissNotice(){
      clearTimeout(noticeTimer);noticeTimer=null;
      if(notice.hidePopover&&notice.matches(':popover-open'))notice.hidePopover();
      notice.hidden=true;showNotice();
    }
    $('achievementNoticeClose').addEventListener('click',dismissNotice);
    function enqueueNotices(ids){
      if(!Array.isArray(ids)||!items)return;
      for(const id of ids){
        const item=items.find(row=>row.event_uuid===id&&row.achieved&&row.completed_at);
        if(!item||announced.has(id))continue;
        announced.add(id);noticeQueue.push(item);
      }
      // Show above the NPC dialog after its synchronous open has finished.
      if(!noticeActive&&noticeQueue.length){noticeActive=true;setTimeout(showNotice,0);}
    }
    window.addEventListener('campus:achievement-earned',event=>enqueueNotices(event.detail?.newly_completed));
    function accept(rows){
      if(!Array.isArray(rows))throw new Error('Invalid achievements');
      const expected=[...names];
      if(rows.some(row=>row?.name==='시선 강탈 사건'))expected.push('시선 강탈 사건');
      if(rows.some(row=>row?.name==='기억력 도둑'))expected.push('기억력 도둑');
      const next=expected.map(name=>{
        const item=rows.find(row=>row?.name===name);
        if(!item||typeof item.event_uuid!=='string'||!item.event_uuid||typeof item.achieved!=='boolean'||
          !(item.completed_at===null||typeof item.completed_at==='string')||typeof item.image_url!=='string')throw new Error('Invalid achievement');
        const image=new URL(item.image_url,document.baseURI);
        if(image.origin!==window.location.origin||!image.pathname.startsWith('/static/campus/assets/achievements/'))throw new Error('Invalid achievement image');
        return {...item,image_url:image.href};
      });
      const newly=items?next.filter(row=>row.achieved&&items.some(old=>old.event_uuid===row.event_uuid&&!old.achieved)).map(row=>row.event_uuid):[];
      rememberDiscoveries(next);items=next;version++;render();enqueueNotices(newly);
    }
    function renderMain(){
      if(!mainBadge||!items)return;
      const row=document.createElement('li'),image=document.createElement('img'),name=document.createElement('strong'),state=document.createElement('span'),note=document.createElement('small'),replay=document.createElement('button');
      row.className='achievement-item main-mission-badge';row.dataset.achieved=String(mainBadge.achieved);row.dataset.discovered=String(mainBadge.achieved);
      image.src=mainBadge.image_url;image.alt='';image.width=144;image.height=144;name.textContent=mainBadge.name;
      state.textContent=mainBadge.achieved?'나의 메인 미션 달성 완료':'메인 미션 최종 전달 후 획득';note.className='first-discoverer';note.textContent='개인 배지 · 완료한 모든 이용자 획득';
      replay.type='button';replay.className='main-badge-replay';replay.textContent=mainBadge.achieved?'완료 연출 다시 보기':'관리자 연출 미리보기';replay.hidden=!mainBadge.achieved&&!mainBadge.can_preview;replay.onclick=()=>window.CampusMainCompletion?.show(mainBadge);
      row.append(image,name,state,note,replay);list.append(row);
    }
    window.addEventListener('campus:main-achievement',event=>{mainBadge=event.detail;if(items)render();});
    function render(){
      list.replaceChildren();list.hidden=items===null;
      if(!items)return;
      const hasAchievement=items.some(item=>item.achieved);
      for(const item of items){
        const row=document.createElement('li'),image=document.createElement('img'),name=document.createElement('strong'),state=document.createElement('span');
        row.className='achievement-item';row.dataset.achieved=String(item.achieved);
        const first=discoveries.get(item.event_uuid);
        row.dataset.discovered=String(Boolean(first)||item.achieved);
        const discovery=document.createElement('small');discovery.className='first-discoverer';
        discovery.textContent=first?'최초 발견 · '+first.nickname:'최초 발견자를 기다리는 중';
        image.src=item.image_url;image.alt='';image.width=144;image.height=144;image.loading='lazy';
        name.textContent=item.name;state.textContent=item.achieved?'나의 달성 완료':first?'달성 마감':hasAchievement?'추가 달성 불가 · 계정당 1개':'도전 가능';
        row.append(image,name,state,discovery);list.append(row);
      }
      renderMain();
      status.textContent='';retry.hidden=true;dialog.removeAttribute('aria-busy');
    }
    function refresh(){
      if(pending)return pending;
      const started=version,controller=new AbortController(),timer=setTimeout(()=>controller.abort(),10000);
      status.textContent='수사 기록을 불러오는 중이에요.';retry.hidden=true;dialog.setAttribute('aria-busy','true');
      pending=(async()=>{
        try{
          const response=await fetch('/api/event-story/achievements/',{credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json'},signal:controller.signal});
          if(!response.ok||response.redirected||!response.headers.get('content-type')?.includes('application/json'))throw new Error('Achievements unavailable');
          const data=await response.json();
          if(version===started){mainBadge=data.main_achievement||mainBadge;accept(data.achievements);}
        }catch{
          if(version===started){status.textContent='수사 기록을 불러오지 못했어요. 다시 시도해 주세요.';retry.hidden=false;}
        }finally{clearTimeout(timer);pending=null;dialog.removeAttribute('aria-busy');}
      })();
      return pending;
    }
    button.addEventListener('click',()=>{
      if(dialog.open||isBlocked())return;
      onOpen();dialog.showModal();$('achievementsClose').focus({preventScroll:true});refresh();
    });
    $('achievementsClose').addEventListener('click',()=>dialog.close());
    dialog.addEventListener('close',()=>{onClose();button.focus({preventScroll:true});});
    retry.addEventListener('click',refresh);
    window.addEventListener('campus:achievements',event=>{try{accept(event.detail);}catch{refresh();}});
    refresh();
    return {refresh,get blocking(){return dialog.open;}};
  }
  window.CampusAchievements={create};
})();
