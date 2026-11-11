/* Personal board notices. Existing post-read logs remain the single source of truth. */
(() => {
  'use strict';
  const button=document.getElementById('noticeNotificationsOpen'),badge=document.getElementById('noticeUnreadBadge');
  if(!button||!badge)return;
  const dialog=document.createElement('dialog');dialog.id='noticeNotificationsDialog';dialog.className='notice-dialog';dialog.setAttribute('aria-labelledby','noticeNotificationsTitle');
  dialog.innerHTML='<header><div><span class="notice-eyebrow">CAMPUS NOTICE</span><h2 id="noticeNotificationsTitle">공지사항</h2></div><button type="button" class="notice-close" aria-label="공지 알림 닫기">×</button></header><div class="notice-status" role="status" hidden></div><div class="notice-content"></div><footer>제목을 눌러 내용을 확인하면 읽음으로 표시돼요.</footer>';
  document.body.append(dialog);
  const content=dialog.querySelector('.notice-content'),status=dialog.querySelector('.notice-status');
  let currentPage=1,generation=0,summaryPending=null,summaryAgain=false,lastCheck=0,authorized=true;
  const node=(tag,cls,text)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e;};
  const date=value=>new Intl.DateTimeFormat('ko-KR',{timeZone:'Asia/Seoul',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(value));
  function count(value){if(!Number.isInteger(value)||value<0)return;badge.hidden=value===0;badge.textContent=value>99?'99+':String(value);button.setAttribute('aria-label',value?`공지사항 알림 · 안 읽은 공지 ${value}개`:'공지사항 알림 · 안 읽은 공지 없음');button.title=value?`안 읽은 공지 ${value}개`:'공지사항 알림';}
  async function api(path){const r=await fetch('/api/community/'+path,{credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json'},signal:AbortSignal.timeout(15000)});if(r.status===401){authorized=false;throw Error('로그인이 만료됐어요. 다시 로그인해 주세요.');}if(!r.ok)throw Error(r.status===404?'공지가 삭제되었거나 더 이상 볼 수 없어요.':'공지를 불러오지 못했어요. 다시 시도해 주세요.');return r.json();}
  async function refreshCount(){
    if(!authorized)return;if(summaryPending){summaryAgain=true;return summaryPending;}
    summaryPending=api('notices/?summary=1').then(data=>{lastCheck=Date.now();count(data.unreadCount);}).catch(()=>{}).finally(()=>{summaryPending=null;if(summaryAgain){summaryAgain=false;void refreshCount();}});return summaryPending;
  }
  function message(text){status.hidden=!text;status.textContent=text||'';}
  function action(text,handler,cls=''){const e=node('button',cls,text);e.type='button';e.addEventListener('click',handler);return e;}
  function failure(error,retry){message(error.message);content.replaceChildren(action('다시 불러오기',retry,'notice-retry'));}
  async function loadList(page=1){
    const version=++generation;message('공지를 불러오는 중이에요.');content.replaceChildren();
    try{const data=await api('notices/?page='+page);if(version!==generation||!dialog.open)return;currentPage=data.pagination.page;count(data.unreadCount);message();
      if(!data.notices.length){content.append(node('div','notice-empty','아직 등록된 공지사항이 없어요.'));return;}
      const list=node('ul','notice-list');
      for(const item of data.notices){const row=node('li'),link=action('',()=>read(item.id),'notice-item');link.dataset.read=String(item.isRead);link.append(node('span','notice-item-state',item.isRead?'읽음':'안 읽음'),node('strong','',item.title),node('time','',date(item.createdAt)));row.append(link);list.append(row);}content.append(list);
      if(data.pagination.pages>1){const nav=node('nav','notice-pages');nav.setAttribute('aria-label','공지 페이지');const prev=action('이전',()=>loadList(currentPage-1)),next=action('다음',()=>loadList(currentPage+1));prev.disabled=currentPage===1;next.disabled=currentPage===data.pagination.pages;nav.append(prev,node('span','',`${currentPage} / ${data.pagination.pages}`),next);content.append(nav);}
    }catch(error){if(version===generation&&dialog.open)failure(error,()=>loadList(page));}
  }
  async function read(id){
    const version=++generation;message('공지 내용을 불러오는 중이에요.');content.replaceChildren();
    try{const data=await api('posts/'+id+'/');void refreshCount();if(version!==generation||!dialog.open)return;message();const post=data.post,article=node('article','notice-article'),back=action('‹ 공지 목록',()=>loadList(currentPage),'notice-back');article.append(node('h3','',post.title),node('p','notice-meta',`${post.author.nickname} · ${date(post.createdAt)}`),node('p','notice-body',post.body));content.append(back,article);}
    catch(error){void refreshCount();if(version===generation&&dialog.open)failure(error,()=>loadList(currentPage));}
  }
  button.addEventListener('click',()=>{if(dialog.open){dialog.close();return;}if(document.querySelector('dialog[open]'))return;dialog.showModal();button.setAttribute('aria-expanded','true');window.dispatchEvent(new Event('campus:notices-open'));void loadList();});
  dialog.querySelector('.notice-close').addEventListener('click',()=>dialog.close());
  dialog.addEventListener('close',()=>{generation++;button.setAttribute('aria-expanded','false');window.dispatchEvent(new Event('campus:notices-close'));button.focus({preventScroll:true});});
  window.addEventListener('campus:notices-changed',()=>{void refreshCount();});
  window.addEventListener('focus',()=>{if(!document.hidden&&Date.now()-lastCheck>5000)void refreshCount();});
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)void refreshCount();});
  setInterval(()=>{if(!document.hidden)void refreshCount();},30000);
  const footer=document.querySelector('.map-shell>.map-footer');if(footer&&typeof ResizeObserver!=='undefined'){const sync=()=>document.documentElement.style.setProperty('--campus-help-height',Math.ceil(footer.getBoundingClientRect().height)+'px');new ResizeObserver(sync).observe(footer);sync();}
  window.CampusNotices={get blocking(){return dialog.open;},refresh:refreshCount};
  void refreshCount();
})();
