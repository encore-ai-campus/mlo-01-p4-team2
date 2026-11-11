/* Achievement state comes only from the authenticated API and personal socket. */
(() => {
  'use strict';
  const names=['수상한 친화력','참을성 테스트','뱀을 길들이는 자','데이터의 비밀문','찰나의 기적','손가락에 불났다','삼각지대 탈출','컴퓨터의 속마음','꾸왕이의 비밀 레시피'];
  function create({isBlocked=()=>false,onOpen=()=>{},onClose=()=>{}}={}){
    const $=id=>document.getElementById(id),button=$('achievementsOpen'),dialog=$('achievementsDialog');
    const list=$('achievementsList'),status=$('achievementsStatus'),retry=$('achievementsRetry');
    let items=null,pending=null,version=0;
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
      const next=names.map(name=>{
        const item=rows.find(row=>row?.name===name);
        if(!item||typeof item.event_uuid!=='string'||!item.event_uuid||typeof item.achieved!=='boolean'||
          !(item.completed_at===null||typeof item.completed_at==='string')||typeof item.image_url!=='string')throw new Error('Invalid achievement');
        const image=new URL(item.image_url,document.baseURI);
        if(image.origin!==window.location.origin||!image.pathname.startsWith('/static/campus/assets/achievements/'))throw new Error('Invalid achievement image');
        return {...item,image_url:image.href};
      });
      const newly=items?next.filter(row=>row.achieved&&items.some(old=>old.event_uuid===row.event_uuid&&!old.achieved)).map(row=>row.event_uuid):[];
      items=next;version++;render();enqueueNotices(newly);
    }
    function render(){
      list.replaceChildren();list.hidden=items===null;
      if(!items)return;
      for(const item of items){
        const row=document.createElement('li'),image=document.createElement('img'),name=document.createElement('strong'),state=document.createElement('span');
        row.className='achievement-item';row.dataset.achieved=String(item.achieved);
        image.src=item.image_url;image.alt='';image.width=144;image.height=144;image.loading='lazy';
        name.textContent=item.name;state.textContent=item.achieved?'획득':'미획득';
        row.append(image,name,state);list.append(row);
      }
      status.textContent='';retry.hidden=true;dialog.removeAttribute('aria-busy');
    }
    function refresh(){
      if(pending)return pending;
      const started=version,controller=new AbortController(),timer=setTimeout(()=>controller.abort(),10000);
      status.textContent='업적을 불러오는 중이에요.';retry.hidden=true;dialog.setAttribute('aria-busy','true');
      pending=(async()=>{
        try{
          const response=await fetch('/api/event-story/achievements/',{credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json'},signal:controller.signal});
          if(!response.ok||response.redirected||!response.headers.get('content-type')?.includes('application/json'))throw new Error('Achievements unavailable');
          const data=await response.json();
          if(version===started)accept(data.achievements);
        }catch{
          if(version===started){status.textContent='업적을 불러오지 못했어요. 다시 시도해 주세요.';retry.hidden=false;}
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
