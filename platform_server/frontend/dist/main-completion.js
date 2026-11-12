/* Personal presentation only; completion is supplied by the authenticated server. */
(()=>{'use strict';
const image='/static/campus/assets/achievements/kkuwang-honorary-assistant-v1.webp';
const dialog=document.createElement('dialog');dialog.id='mainCompletion';
dialog.setAttribute('aria-labelledby','mainCompletionTitle');
dialog.innerHTML='<div class="completion-rays" aria-hidden="true"></div><div class="completion-confetti" aria-hidden="true"></div><section class="completion-card"><p class="completion-caption">MAIN QUEST COMPLETE</p><h2 id="mainCompletionTitle">사건 해결!</h2><div class="completion-emblem"><img width="240" height="240" alt="탐정 꾸왕이와 돋보기가 담긴 금빛 배지"></div><p class="completion-awarded">메인 미션 달성 · 배지 획득</p><h3>꾸왕이의 명예 조수</h3><p class="completion-copy">사라진 선물을 찾고 새로운 빼빼로를 완성했어요.<br>꾸왕이가 당신을 명예 조수로 임명합니다!</p><p class="completion-reward"><strong>메인 미션을 달성한 당신께<br>빼빼로를 드립니다!</strong><span>빼빼로 보상은 11월 12일에 받을 수 있어요!</span></p><section class="completion-share" aria-label="사건 해결 이미지 공유"><label for="completionShareLink">사건 해결 이미지 공유</label><div class="completion-share-row"><input id="completionShareLink" readonly aria-label="공유할 사건 해결 이미지 링크"><button type="button" class="completion-share-copy">링크 복사</button></div><p class="completion-share-status" role="status" aria-live="polite">친구에게 사건 해결 이미지를 공유해 보세요.</p></section><button type="button" class="completion-dismiss">배지 받고 캠퍼스로</button><small>수사수첩에서 언제든 다시 확인할 수 있어요.</small></section>';
dialog.querySelector('img').src=image;document.body.append(dialog);
for(let i=0;i<32;i++){const piece=document.createElement('i');piece.style.setProperty('--i',i);piece.style.setProperty('--x',((i*37)%100)+'%');piece.style.setProperty('--drift',((i%2?1:-1)*(30+i*3))+'px');dialog.querySelector('.completion-confetti').append(piece);}
function close(){dialog.close();window.CampusApp?.presence.setInput([]);const other=[...document.querySelectorAll('dialog[open]')].pop();(other?.querySelector('button')||document.getElementById('mapCanvas'))?.focus({preventScroll:true});}
dialog.querySelector('.completion-dismiss').onclick=close;
const shareInput=dialog.querySelector('#completionShareLink');
const shareUrl=new URL('/static/campus/assets/achievements/main-mission-complete-share-v2.png',window.location.origin).href;
shareInput.value=shareUrl;
shareInput.addEventListener('click',()=>shareInput.select());
dialog.querySelector('.completion-share-copy').onclick=async()=>{
 const button=dialog.querySelector('.completion-share-copy'),status=dialog.querySelector('.completion-share-status');button.disabled=true;
 try{
  let copied=false;
  try{if(navigator.clipboard?.writeText){await navigator.clipboard.writeText(shareUrl);copied=true;}}catch{}
  if(!copied){shareInput.focus();shareInput.select();try{copied=document.execCommand('copy');}catch{}}
  status.textContent=copied?'이미지 링크를 복사했어요. 원하는 곳에 붙여넣으세요.':'링크를 선택했어요. Ctrl+C 또는 길게 눌러 복사해 주세요.';
 }finally{button.disabled=false;}
};
dialog.addEventListener('cancel',e=>{e.preventDefault();close();});
dialog.addEventListener('keydown',e=>e.stopPropagation());
function show(badge){
 if(badge?.kind!=='MAIN_MISSION'||!(badge.achieved&&badge.completed_at||badge.can_preview))return false;
 const preview=!(badge.achieved&&badge.completed_at);
 dialog.querySelector('.completion-caption').textContent=preview?'ADMIN PREVIEW':'MAIN QUEST COMPLETE';
 dialog.querySelector('.completion-awarded').textContent=preview?'관리자 연출 미리보기 · 달성 기록에 반영되지 않음':'메인 미션 달성 · 배지 획득';
 dialog.querySelector('.completion-dismiss').textContent=preview?'미리보기 닫기':'배지 받고 캠퍼스로';
 dialog.querySelector('.completion-share-status').textContent='친구에게 사건 해결 이미지를 공유해 보세요.';
 if(dialog.open)return true;
 window.dispatchEvent(new Event('campus:guide-open'));window.CampusApp?.presence.setInput([]);window.CampusApp?.poke?.stop(true);
 if(window.CampusApp)window.CampusApp.player.moving=false;
 dialog.showModal();dialog.querySelector('.completion-dismiss').focus({preventScroll:true});return true;
}
window.addEventListener('campus:main-mission-completed',e=>show(e.detail));
window.CampusMainCompletion={show,get blocking(){return dialog.open;}};
})();
