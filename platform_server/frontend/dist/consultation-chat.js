/* LMS-owned counseling launcher. No third-party chat SDK or external navigation. */
(() => {
  'use strict';
  const button=document.getElementById('consultationChatOpen');if(!button)return;
  button.setAttribute('aria-label','LMS 상담톡 열기');button.setAttribute('aria-expanded','false');button.setAttribute('role','button');
  const greeting=document.createElement('aside');greeting.className='consultation-greeting';greeting.setAttribute('aria-label','상담톡 안내');
  const greetingOpen=document.createElement('button');greetingOpen.type='button';greetingOpen.className='consultation-greeting-open';
  const mark=document.createElement('span');mark.className='consultation-greeting-mark';const logo=document.createElement('img');logo.src=new URL('assets/logo.png',document.baseURI).href;logo.alt='';mark.append(logo);
  const copy=document.createElement('span');copy.className='consultation-greeting-copy';const title=document.createElement('strong');title.textContent='상담톡';const pending=document.createElement('small');pending.className='consultation-pending-badge';pending.textContent='준비 중';title.append(pending);const detail=document.createElement('span');detail.className='consultation-greeting-detail';detail.textContent='몇 분 내 답변 받으실 수 있어요';copy.append(title,detail);greetingOpen.append(mark,copy);
  const closeGreeting=document.createElement('button');closeGreeting.type='button';closeGreeting.className='consultation-greeting-close';closeGreeting.setAttribute('aria-label','상담 안내 말풍선 닫기');closeGreeting.textContent='×';greeting.append(greetingOpen,closeGreeting);document.body.append(greeting);
  closeGreeting.addEventListener('click',()=>{greeting.hidden=true;});greetingOpen.addEventListener('click',()=>button.click());
  button.removeAttribute('href');button.removeAttribute('target');button.removeAttribute('rel');button.setAttribute('aria-label','LMS 상담톡 열기');button.title='LMS 상담톡';
  detail.textContent='상담 서비스를 준비하고 있어요';const launcherPending=document.createElement('span');launcherPending.className='consultation-launcher-pending';launcherPending.textContent='준비 중';button.append(launcherPending);button.setAttribute('aria-label','LMS 상담톡 · 준비 중');button.title='LMS 상담톡 · 준비 중';
  button.addEventListener('click',event=>{event.preventDefault();greeting.hidden=true;window.dispatchEvent(new Event('campus:consultation-open'));});
  button.addEventListener('keydown',event=>{if(event.key===' '||event.key==='Enter'){event.preventDefault();button.click();}});
})();
