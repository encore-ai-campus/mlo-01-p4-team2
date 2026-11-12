/* Physical key preferences are resolved to stable game actions before networking. */
(() => {
  'use strict';
  const defaults=Object.freeze({up:'KeyW',left:'KeyA',down:'KeyS',right:'KeyD',run:'ShiftLeft',guard:'ControlLeft',interact:'KeyE',poke:'KeyZ',jump:'Space'});
  const actions=Object.keys(defaults),tokens={up:'w',left:'a',down:'s',right:'d',run:'shift',guard:'control',interact:'e',poke:'z',jump:' '};
  const labels={up:'위로 이동',left:'왼쪽 이동',down:'아래로 이동',right:'오른쪽 이동',run:'달리기',guard:'방어',interact:'상호작용',poke:'찌르기',jump:'점프'};
  const config=document.getElementById('campusPresenceConfig');
  let identity='preview';try{identity=JSON.parse(config?.textContent||'{}').preferencesKey||identity;}catch{}
  const storageKey=`campus-keys-v1:${identity}`;
  const isAllowed=code=>/^Key[A-Z]$/.test(code)||/^Digit[0-9]$/.test(code)||/^Numpad[0-9]$/.test(code)||/^Arrow(Up|Down|Left|Right)$/.test(code)||['Space','ShiftLeft','ShiftRight','ControlLeft','ControlRight','Backquote','Minus','Equal','BracketLeft','BracketRight','Backslash','Semicolon','Quote','Comma','Period','Slash'].includes(code);
  let bindings={...defaults};
  try{const saved=JSON.parse(localStorage.getItem(storageKey)||'null');if(saved&&actions.every(a=>isAllowed(saved[a]))&&new Set(actions.map(a=>saved[a])).size===actions.length)bindings={...saved};}catch{}
  const keyName=code=>/^Control(Left|Right)?$/.test(code)?'Ctrl':code==='Space'?'Space':code.startsWith('Key')?code.slice(3):code.startsWith('Digit')?code.slice(5):code.startsWith('Numpad')?'Num '+code.slice(6):code.startsWith('Arrow')?'↑↓←→'[['ArrowUp','ArrowDown','ArrowLeft','ArrowRight'].indexOf(code)]:code.replace(/(Left|Right)$/,'');
  function physical(event){
    const code=event.code||'';
    if(code)return code;
    const value=String(event.key||'');return value.length===1&&/[a-z]/i.test(value)?'Key'+value.toUpperCase():value===' '?'Space':value;
  }
  function key(event){
    const code=physical(event);
    const action=actions.find(a=>bindings[a]===code);
    if(action)return tokens[action];
    if(code==='ShiftRight'&&bindings.run==='ShiftLeft')return 'shift';
    if(code==='ControlRight'&&bindings.guard==='ControlLeft')return 'control';
    const arrows={ArrowUp:'arrowup',ArrowDown:'arrowdown',ArrowLeft:'arrowleft',ArrowRight:'arrowright'};
    if(arrows[code])return arrows[code];
    if(isAllowed(code))return '';
    return String(event.key||'').toLowerCase();
  }
  function updateLabels(){
    for(const node of (document.querySelectorAll?.('[data-key-action]')||[]))node.textContent=keyName(bindings[node.dataset.keyAction]);
    const hint=document.getElementById('movementKeyHint');if(hint)hint.textContent=['up','left','down','right'].map(a=>keyName(bindings[a])).join('·');
    for(const node of (document.querySelectorAll?.('[data-key-bind]')||[]))node.textContent=keyName(bindings[node.dataset.keyBind]);
    const jump=document.getElementById('campusJumpButton');if(jump)jump.setAttribute('aria-label',`점프, ${keyName(bindings.jump)}`);
  }
  function save(action,code){
    if(!Object.hasOwn(defaults,action)||!isAllowed(code))return false;
    const previous=bindings[action],other=actions.find(a=>a!==action&&bindings[a]===code);
    if(other)bindings[other]=previous;
    bindings[action]=code;
    try{localStorage.setItem(storageKey,JSON.stringify(bindings));}catch{}
    window.dispatchEvent?.(new Event('campus-keybindings-changed'));
    updateLabels();return true;
  }
  function reset(){bindings={...defaults};try{localStorage.removeItem(storageKey);}catch{}window.dispatchEvent?.(new Event('campus-keybindings-changed'));updateLabels();}
  let capture=null;
  document.addEventListener('keydown',event=>{
    if(!capture)return;
    event.preventDefault();event.stopImmediatePropagation();
    if(event.code==='Escape'){capture=null;document.getElementById('keybindStatus').textContent='키 변경을 취소했어요.';return;}
    if(event.repeat)return;
    const code=physical(event),status=document.getElementById('keybindStatus');
    if(event.altKey||event.metaKey||!isAllowed(code)){status.textContent='이 키는 지정할 수 없어요. 다른 키를 눌러 주세요.';return;}
    const target=capture;capture=null;save(target,code);status.textContent=`${labels[target]}: ${keyName(code)}로 설정했어요. 겹친 키는 서로 바뀌었어요.`;
  },true);
  document.addEventListener('click',event=>{
    const button=event.target.closest?.('[data-key-bind]');if(!button)return;
    capture=button.dataset.keyBind;
    document.getElementById('keybindStatus').textContent=`${labels[capture]}에 사용할 키를 눌러 주세요. Esc는 취소예요.`;
  });
  document.getElementById('keybindReset')?.addEventListener('click',()=>{capture=null;reset();document.getElementById('keybindStatus').textContent='기본 조작키로 되돌렸어요.';});
  document.getElementById('settingsDialog')?.addEventListener('close',()=>{capture=null;});
  window.CampusKeyboard=Object.freeze({key,isGameKey:event=>Boolean(actions.find(a=>bindings[a]===physical(event)))||['ArrowUp','ArrowDown','ArrowLeft','ArrowRight'].includes(physical(event))||(physical(event)==='ShiftRight'&&bindings.run==='ShiftLeft')||(physical(event)==='ControlRight'&&bindings.guard==='ControlLeft'),get bindings(){return {...bindings};},save,reset,label:action=>keyName(bindings[action])});
  updateLabels();
})();
