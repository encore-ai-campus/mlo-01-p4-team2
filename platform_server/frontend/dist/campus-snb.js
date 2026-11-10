/* Reparent existing controls so their state, listeners and accessible IDs survive. */
(() => {
  'use strict';
  const shell=document.querySelector('.map-shell');
  if(!shell)return;
  const panel=document.createElement('aside');
  panel.id='campusSnb';panel.className='campus-snb';panel.setAttribute('aria-label','캠퍼스 사이드 메뉴');
  const content=document.createElement('div');content.className='campus-snb-content';panel.append(content);
  const toolbar=shell.querySelector('.map-toolbar'),status=shell.querySelector('.map-status-tools'),footer=shell.querySelector('.map-footer'),guide=document.getElementById('campusSpaceGuide');
  const heading=text=>{const h=document.createElement('h2');h.className='snb-section-title';h.textContent=text;content.append(h);};
  if(toolbar){
    panel.prepend(toolbar);
    const syncHeaderHeight=()=>panel.style.setProperty('--snb-header-height',`${toolbar.getBoundingClientRect().height}px`);
    new ResizeObserver(syncHeaderHeight).observe(toolbar);
  }
  if(guide)content.append(guide);
  const player=footer?.querySelector('.map-player');
  if(player){heading('나의 캐릭터');player.classList.add('snb-player');content.append(player);}
  heading('내 캠퍼스');if(status)content.append(status);
  const mailbox=panel.querySelector('#privateMessagesOpen')||document.getElementById('privateMessagesOpen');
  const wallet=panel.querySelector('.map-wallet-anchor');
  if(mailbox&&wallet)wallet.append(mailbox);
  const services=document.createElement('nav');services.className='snb-services';services.setAttribute('aria-label','게시판과 구내식당');
  for(const [key,label] of [['board','PLAYDATA 게시판'],['menu','지하1층 구내식당 & Talk']]){
    const button=document.createElement('button');button.type='button';
    const mark=document.createElement('span');mark.className='snb-service-mark';mark.setAttribute('aria-hidden','true');
    if(key==='board'){const logo=document.createElement('img');logo.src=new URL('assets/logo.png',document.baseURI).href;logo.alt='';mark.append(logo);}
    else if(key==='lunch')mark.innerHTML='<svg viewBox="0 0 24 24" width="23" height="23" fill="none" stroke="currentColor" stroke-width="1.7"><circle cx="12" cy="13" r="9"/><circle cx="12" cy="13" r="2"/><path d="M9 1h6l-3 6ZM12 4v7m-8 2h6m4 0h6m-8 2v7"/></svg>';
    else mark.innerHTML='<svg viewBox="0 0 80 48" width="30" height="24" fill="none"><rect x="2" y="2" width="76" height="44" rx="10" fill="#fffdf5"/><circle cx="40" cy="24" r="16" stroke="#a6bba1" stroke-width="2"/><circle cx="40" cy="24" r="11" fill="#d5e1c6"/><path d="M15 13v10m4-10v10m-4-2c0 5 4 5 4 0m-2 5v9m46-22v22m0-22c-5 4-5 12 0 13" stroke="#829584" stroke-width="2" stroke-linecap="round"/></svg>';
    const text=document.createElement('span');text.textContent=label;button.append(text);
    button.addEventListener('click',()=>window.dispatchEvent(new CustomEvent('campus:open-service',{detail:key})));services.append(button);
  }content.append(services);
  const studentMenu=document.createElement('section');studentMenu.className='snb-student-menu';
  const studentSummary=document.createElement('h2');studentSummary.className='snb-section-title';studentSummary.textContent='학생 메뉴';const studentPending=document.createElement('small');studentPending.className='snb-pending-badge';studentPending.textContent='준비 중';studentSummary.append(studentPending);studentMenu.append(studentSummary);
  const studentNav=document.createElement('nav');studentNav.setAttribute('aria-label','학생 메뉴 준비 중');studentNav.hidden=true;
  for(const title of ['나의 출결','시험·평가','자격증 연습실','스터디·그룹','취업·진로','회고록 제출','마일리지','공지사항']){
    const item=document.createElement('div');item.className='snb-student-placeholder';const label=document.createElement('span');label.textContent=title;const badge=document.createElement('small');badge.textContent='준비 중';item.append(label,badge);studentNav.append(item);
  }studentMenu.append(studentNav);content.append(studentMenu);
  const logoutForm=document.getElementById('campusLogoutForm');
  const settingsButton=panel.querySelector('#settingsBtn')||document.getElementById('settingsBtn');
  const accountActions=document.createElement('div');accountActions.className='snb-account-actions';
  if(settingsButton)accountActions.append(settingsButton);
  if(logoutForm){logoutForm.classList.add('snb-logout');accountActions.append(logoutForm);}
  if(accountActions.childElementCount)content.append(accountActions);
  const playerTools=footer?.querySelector('.map-player-tools');if(playerTools&&!playerTools.childElementCount)playerTools.remove();
  const toggle=document.createElement('button');toggle.type='button';toggle.className='campus-snb-toggle';toggle.id='campusSnbToggle';toggle.setAttribute('aria-controls',panel.id);
  toggle.innerHTML='<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path d="m14 6-6 6 6 6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  shell.prepend(panel,toggle);
  let open=true;
  try{const saved=localStorage.getItem('campus.snb.open');if(saved!==null)open=saved==='true';else open=innerWidth>700;}catch(_){open=innerWidth>700;}
  function setOpen(next){
    open=next;shell.dataset.snbOpen=String(open);toggle.setAttribute('aria-expanded',String(open));toggle.setAttribute('aria-label',open?'사이드 메뉴 왼쪽으로 접기':'사이드 메뉴 오른쪽으로 펼치기');toggle.title=open?'메뉴 접기 (Tab)':'메뉴 펼치기 (Tab)';
    panel.inert=!open;panel.setAttribute('aria-hidden',String(!open));
    try{localStorage.setItem('campus.snb.open',String(open));}catch(_){}
  }
  toggle.addEventListener('click',()=>setOpen(!open));
  toggle.setAttribute('aria-keyshortcuts','Tab');
  document.addEventListener('keydown',event=>{
    if(event.key!=='Tab'||event.shiftKey||event.ctrlKey||event.altKey||event.metaKey||event.defaultPrevented)return;
    const target=event.target;
    if(target instanceof Element&&(target.closest('input,textarea,select,[contenteditable="true"],[role="textbox"]')||document.querySelector('dialog[open]')))return;
    event.preventDefault();
    if(!event.repeat)setOpen(!open);
  });
  toggle.addEventListener('keydown',event=>{
    if(['ArrowLeft','ArrowRight','Enter',' '].includes(event.key))event.stopPropagation();
    if(event.key==='ArrowLeft'||event.key==='ArrowRight'){event.preventDefault();setOpen(event.key==='ArrowRight');}
  });
  setOpen(open);
})();
