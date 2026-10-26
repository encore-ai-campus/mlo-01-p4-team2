/* Four server-authorized event destinations; opening the menu does not send traffic. */
(()=>{
 'use strict';
 const destinations=[
  {id:'campus-gallery',floor:'campus',label:'스위트 갤러리',detail:'18층 · 5·6·7 통합 아틀리에 앞'},
  {id:'campus-market',floor:'campus',label:'기프트 마켓',detail:'18층 · 라운지 1'},
  {id:'roof-workshop',floor:'rooftop',label:'초콜릿 워크숍',detail:'옥상 · 왼쪽 부스 구역'},
  {id:'roof-plaza',floor:'rooftop',label:'초콜릿 광장',detail:'옥상 · 중앙 조형물 앞'},
 ];
 window.CampusSpaceGuide={create({onSelect=()=>{},onOpen=()=>{},isBlocked=()=>false,focusMap=()=>{}}={}){
  const toggle=document.getElementById('campusNavToggle'),panel=document.getElementById('campusSpaceGuide'),nav=document.getElementById('roomNav');
  let floor='campus';
  function close({focus=false}={}){panel.hidden=true;toggle.setAttribute('aria-expanded','false');toggle.title='빠른 이동 열기';if(focus)toggle.focus({preventScroll:true});}
  function open(){if(isBlocked())return;onOpen();panel.hidden=false;toggle.setAttribute('aria-expanded','true');toggle.title='빠른 이동 닫기';nav.querySelector('button')?.focus({preventScroll:true});}
  function flip(){if(panel.hidden)open();else close({focus:true});}
  function setRoom(){for(const button of nav.querySelectorAll('[data-destination]'))button.classList.toggle('on-floor',button.dataset.floor===floor);}
  function setWorld(data){floor=data.id;setRoom();}
  for(const [name,groupFloor] of [['18층','campus'],['옥상','rooftop']]){
   const group=document.createElement('div');group.className='nav-group-label';group.textContent=name;nav.append(group);
   for(const destination of destinations.filter(item=>item.floor===groupFloor)){
    const button=document.createElement('button');button.type='button';button.className='room-nav-button quick-travel-button';button.dataset.destination=destination.id;button.dataset.floor=destination.floor;
    const icon=document.createElement('span');icon.className='nav-icon';icon.setAttribute('aria-hidden','true');icon.textContent=groupFloor==='campus'?'18':'RF';
    const text=document.createElement('span');text.className='quick-travel-copy';const label=document.createElement('strong');label.textContent=destination.label;const detail=document.createElement('small');detail.textContent=destination.detail;text.append(label,detail);
    button.append(icon,text);button.addEventListener('click',()=>{if(isBlocked())return;close();onSelect(destination);});nav.append(button);
   }
  }
  toggle.addEventListener('click',flip);
  document.addEventListener('pointerdown',event=>{if(!panel.hidden&&!panel.contains(event.target)&&!toggle.contains(event.target))close();});
  document.addEventListener('keydown',event=>{if(panel.hidden||isBlocked())return;if(event.key==='Escape'){event.preventDefault();event.stopImmediatePropagation();close();focusMap();}else if(['ArrowDown','ArrowUp'].includes(event.key)&&nav.contains(document.activeElement)){event.preventDefault();event.stopImmediatePropagation();const buttons=[...nav.querySelectorAll('button')],index=buttons.indexOf(document.activeElement);buttons[(index+(event.key==='ArrowDown'?1:-1)+buttons.length)%buttons.length]?.focus({preventScroll:true});}},true);
  window.addEventListener('campus:guide-open',()=>close());
  setRoom();
  return {setWorld,setRoom,close,toggle:flip,get open(){return !panel.hidden;}};
 }};
})();
