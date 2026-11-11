/* Server-backed lecture pause. Shared DB state is authoritative. */
(() => {
  'use strict';
  const url='/api/campus/freeze/';
  let state={paused:false,revision:0,message:'강의 중입니다. 쉬는 시간에 다시 이용할 수 있어요.'},known=false,canManage=false,busy=false,polling=false;
  const held=new Set(),suppressed=new Set();
  const style=document.createElement('style');style.textContent=`
    #campusFreezeDialog{border:1px solid #d8e2df;border-radius:20px;padding:30px;max-width:min(470px,calc(100vw - 32px));color:#25443e;background:#fffdf7;box-shadow:0 18px 80px #102b3440}
    #campusFreezeDialog::backdrop{background:#102b34aa;backdrop-filter:blur(3px)}
    #campusFreezeDialog h2{font:700 24px/1.4 system-ui;margin:0 0 14px}#campusFreezeDialog p{white-space:pre-wrap;line-height:1.7}
    #campusFreezeDialog button,#campusFreezeAdmin button{font:inherit;padding:10px 14px;border-radius:8px;border:1px solid #bacfc7;background:#edf6ef;color:#23483b;cursor:pointer}
    #campusFreezeAdmin{position:fixed;top:12px;left:50%;transform:translateX(-50%);z-index:3000;background:#fffdf7;color:#25443e;padding:8px 12px;border:1px solid #bacfc7;border-radius:10px;max-width:calc(100vw - 24px);font:14px/1.5 system-ui}
    #campusFreezeAdmin summary{cursor:pointer}#campusFreezeAdmin textarea{display:block;box-sizing:border-box;width:300px;max-width:100%;margin:10px 0;padding:8px;font:inherit}#campusFreezeAdmin p{margin:7px 0}
    #campusFreezeAdmin button:disabled,#campusFreezeDialog button:disabled{opacity:.5}#campusFreezeDialog small{display:block;color:#58716a}#campusFreezeError{color:#9c3434}
  `;document.head.append(style);
  const dialog=document.createElement('dialog');dialog.id='campusFreezeDialog';dialog.setAttribute('aria-labelledby','campusFreezeTitle');
  dialog.innerHTML='<h2 id="campusFreezeTitle">캠퍼스 이용 일시정지</h2><p id="campusFreezeMessage"></p><small>저장된 진행 상황은 유지됩니다. 관리자가 재개하면 다시 이용할 수 있어요.</small><p id="campusFreezeError" role="status"></p><button type="button" id="campusFreezeResume" hidden>캠퍼스 이용 재개</button> <button type="button" id="campusFreezeLogout">로그아웃</button>';
  document.body.append(dialog);dialog.addEventListener('cancel',e=>e.preventDefault());
  const panel=document.createElement('details');panel.id='campusFreezeAdmin';panel.hidden=true;panel.innerHTML='<summary>관리자 운영 제어</summary><p id="campusFreezeState"></p><label>정지 안내 문구<textarea id="campusFreezeReason" maxlength="160" rows="2"></textarea></label><button type="button" id="campusFreezeToggle"></button><p id="campusFreezeAdminError" role="status"></p>';
  document.body.append(panel);
  const $=id=>document.getElementById(id);
  function emit(){window.dispatchEvent(new CustomEvent('campus:freeze',{detail:{...state,blocking:!known||state.paused}}));}
  function accept(next){if(!next||typeof next.paused!=='boolean'||!Number.isInteger(next.revision)||next.revision<0)return;
    if(known&&next.revision<state.revision)return;
    const changed=!known||state.paused!==next.paused||state.revision!==next.revision;
    if(typeof next.canManage==='boolean')canManage=next.canManage;
    state={paused:next.paused,revision:next.revision,message:typeof next.message==='string'?next.message:state.message,unavailable:next.unavailable===true};known=true;
    if(next.session)window.CampusSession?.receive(next.session);
    panel.hidden=!canManage;$('campusFreezeState').textContent=state.paused?'현재 캠퍼스 이용이 정지되어 있습니다.':'현재 캠퍼스 이용이 가능합니다.';
    if(document.activeElement!==$('campusFreezeReason'))$('campusFreezeReason').value=state.message;
    $('campusFreezeToggle').textContent=state.paused?'이용 재개':'강의 중 이용 일시정지';$('campusFreezeToggle').disabled=busy||state.unavailable;
    $('campusFreezeMessage').textContent=state.message;$('campusFreezeResume').hidden=!canManage;$('campusFreezeResume').disabled=busy||state.unavailable;
    if(changed){for(const code of held)suppressed.add(code);emit();}
    if(state.paused&&!dialog.open)dialog.showModal();else if(!state.paused&&dialog.open)dialog.close();
  }
  async function refresh(){if(polling)return;polling=true;try{const response=await fetch(url,{credentials:'same-origin',cache:'no-store'});if(response.status===401){window.CampusSession?.verify?.();return;}const data=await response.json();if(response.ok||response.status===503)accept(data);}catch{if(!known)accept({paused:true,revision:0,message:'이용 상태를 확인하고 있어요. 연결을 확인해 주세요.',unavailable:true});}finally{polling=false;}}
  function csrf(){const config=document.getElementById('campusSessionPolicyConfig');try{const token=JSON.parse(config?.textContent||'{}').csrfToken;if(token)return token;}catch{}return document.querySelector('[name=csrfmiddlewaretoken]')?.value||document.cookie.split('; ').find(x=>x.startsWith('csrftoken='))?.slice(10)||'';}
  async function toggle(){if(busy||!canManage||!known)return;busy=true;accept(state);$('campusFreezeError').textContent=$('campusFreezeAdminError').textContent='';
    try{const response=await fetch(url,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRFToken':csrf()},body:JSON.stringify({paused:!state.paused,revision:state.revision,message:$('campusFreezeReason').value})});const data=await response.json();if(!response.ok){if(response.status===409)accept(data);throw new Error(data.error||'상태를 변경하지 못했습니다.');}accept(data);}
    catch(error){$('campusFreezeError').textContent=$('campusFreezeAdminError').textContent=error.message;}
    finally{busy=false;accept(state);}
  }
  $('campusFreezeLogout').onclick=()=>document.getElementById('campusLogoutForm')?.requestSubmit();
  $('campusFreezeToggle').onclick=toggle;$('campusFreezeResume').onclick=toggle;
  window.CampusFreeze={get blocking(){return !known||state.paused;},get paused(){return state.paused;},get revision(){return state.revision;},accept,refresh};
  document.addEventListener('keydown',e=>{held.add(e.code);const adminInput=panel.contains(e.target)&&e.target.closest?.('input,textarea,select');const navigation=(dialog.contains(e.target)||panel.contains(e.target))&&['Tab','Enter','Space','Escape','ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.code);if((!known||state.paused)&&!adminInput&&!navigation||suppressed.has(e.code)){e.preventDefault();e.stopImmediatePropagation();}},true);
  document.addEventListener('keyup',e=>{held.delete(e.code);suppressed.delete(e.code);},true);
  window.addEventListener('blur',()=>held.clear());
  window.addEventListener('campus:ready',emit);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});
  new MutationObserver(records=>{if(state.paused&&dialog.open&&records.some(r=>r.target!==dialog&&r.target instanceof HTMLDialogElement&&r.target.open)){dialog.close();dialog.showModal();}}).observe(document.body,{subtree:true,attributes:true,attributeFilter:['open']});
  refresh();setInterval(refresh,15000);
})();
