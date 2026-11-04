/* Timed chat display, personal settings, and ten-second speech bubbles. */
(() => {
  'use strict';
  window.CampusSocial = {
    create({config, onFocus, onSend, getSelfId}) {
      const $ = id => document.getElementById(id);
      const dialog = $('settingsDialog'), frame = $('chatPanel'), input = $('chatInput');
      const log = $('chatMessages'), send = $('chatSend'), status = $('chatStatus');
      const form = $('chatForm'), meta = frame.querySelector('.chat-composer-meta'), open = $('chatOpen');
      const layer = $('speechBubbles'), shell = document.querySelector('.map-shell');
      // Rich composer keeps the existing text protocol, but shows sticker images while editing.
      const editor=document.createElement('div');editor.className='chat-rich-input';editor.id='chatRichInput';editor.setAttribute('role','textbox');editor.setAttribute('aria-label','채팅 메시지');editor.setAttribute('aria-describedby','chatStatus chatCount');editor.dataset.placeholder=input.placeholder;editor.contentEditable='false';
      input.hidden=true;input.tabIndex=-1;input.before(editor);
      let editorRange=null,editorDisabled=true,editorReadOnly=false;
      function readComposer(node){
        if(node.nodeType===Node.TEXT_NODE)return node.textContent;
        if(node.nodeType===Node.ELEMENT_NODE&&node.dataset.stickerToken)return node.dataset.stickerToken;
        return Array.from(node.childNodes).map(readComposer).join('');
      }
      function keepEditorRange(){const selection=window.getSelection();if(selection.rangeCount&&editor.contains(selection.anchorNode)&&editor.contains(selection.focusNode))editorRange=selection.getRangeAt(0).cloneRange();}
      document.addEventListener('selectionchange',keepEditorRange);
      function selectionOffset(end){if(!editorRange)return readComposer(editor).length;const range=editorRange.cloneRange();range.selectNodeContents(editor);if(end)range.setEnd(editorRange.endContainer,editorRange.endOffset);else range.setEnd(editorRange.startContainer,editorRange.startOffset);return readComposer(range.cloneContents()).length;}
      function setEditorEditable(){editor.contentEditable=String(!editorDisabled&&!editorReadOnly);editor.setAttribute('aria-disabled',String(editorDisabled));editor.setAttribute('aria-readonly',String(editorReadOnly));}
      Object.defineProperties(input,{
        value:{get:()=>readComposer(editor),set:value=>{messageContent(editor,String(value));editorRange=null;}},
        disabled:{get:()=>editorDisabled,set:value=>{editorDisabled=Boolean(value);setEditorEditable();}},
        readOnly:{get:()=>editorReadOnly,set:value=>{editorReadOnly=Boolean(value);setEditorEditable();}},
        selectionStart:{get:()=>selectionOffset(false)},selectionEnd:{get:()=>selectionOffset(true)}
      });
      input.focus=options=>editor.focus(options);
      input.setRangeText=token=>{
        const range=editorRange?.cloneRange()||document.createRange();if(!editorRange){range.selectNodeContents(editor);range.collapse(false);}
        range.deleteContents();const holder=document.createElement('span');messageContent(holder,token);const fragment=document.createDocumentFragment();while(holder.firstChild)fragment.append(holder.firstChild);const last=fragment.lastChild;range.insertNode(fragment);if(last)range.setStartAfter(last);range.collapse(true);editorRange=range.cloneRange();const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);
      };
      for(const type of ['focus','input','compositionstart','compositionend'])editor.addEventListener(type,event=>input.dispatchEvent(type==='input'?new InputEvent(type,{bubbles:true,isComposing:event.isComposing}):new Event(type,{bubbles:false})));
      editor.addEventListener('keydown',event=>{if(event.key==='Escape'&&editing){event.preventDefault();event.stopPropagation();leaveEditor();return;}const forwarded=new KeyboardEvent('keydown',{key:event.key,code:event.code,repeat:event.repeat,shiftKey:event.shiftKey,ctrlKey:event.ctrlKey,altKey:event.altKey,metaKey:event.metaKey,isComposing:event.isComposing,cancelable:true});input.dispatchEvent(forwarded);if(forwarded.defaultPrevented)event.preventDefault();event.stopPropagation();});
      editor.addEventListener('paste',event=>{event.preventDefault();if(!editorDisabled&&!editorReadOnly)document.execCommand('insertText',false,event.clipboardData.getData('text/plain'));});
      const preferences = {nicknames:true, chat:true, bubbles:true};
      const controls = {nicknames:$('settingNicknames'), chat:$('settingChat'), bubbles:$('settingBubbles')};
      const storageKey = config.preferencesKey || 'campus-display-v1:preview';
      const bubbles = new Map(), seen = new Set();
      const CHAT_IDLE_MS = 30000, FADE_MS = 400;
      let connected = false, pending = false, pendingTimer = null, editing = false, composing = false;
      let visibleUntil = 0, idleTimer = null, fadeTimer = null;
      let entryShown=false,entryVisibleUntil=0;
      try {
        const stored = JSON.parse(localStorage.getItem(storageKey) || '{}');
        for (const key of Object.keys(preferences)) if (typeof stored?.[key] === 'boolean') preferences[key] = stored[key];
      } catch { /* Default controls remain usable when browser storage is unavailable. */ }

      function clearDisplayTimers() {
        clearTimeout(idleTimer);clearTimeout(fadeTimer);idleTimer = fadeTimer = null;
      }
      function hideChat() {
        clearDisplayTimers();frame.hidden = true;frame.classList.remove('is-fading');
        shell.dataset.chatVisible = 'false';
      }
      function scheduleHide() {
        clearTimeout(idleTimer);idleTimer = null;
        if (frame.hidden || !preferences.chat || editing || pending) return;
        const remaining = visibleUntil-performance.now();
        if (remaining > 0) {idleTimer = setTimeout(scheduleHide,remaining);return;}
        if (fadeTimer !== null) return;
        frame.classList.add('is-fading');
        const duration = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 0 : FADE_MS;
        fadeTimer = setTimeout(hideChat,duration);
      }
      function revealChat() {
        if (!preferences.chat) return;
        clearDisplayTimers();frame.hidden = false;frame.classList.remove('is-fading');
        shell.dataset.chatVisible = 'true';visibleUntil = Math.max(entryVisibleUntil,performance.now()+CHAT_IDLE_MS);
        scheduleHide();
      }
      function leaveEditor({focusMap = true} = {}) {
        editing = false;shell.dataset.chatEditing = 'false';open.setAttribute('aria-expanded','false');
        if (frame.contains(document.activeElement)) document.activeElement.blur();
        form.hidden = true;meta.hidden = true;onFocus();scheduleHide();
        if (focusMap) $('mapCanvas').focus({preventScroll:true});
      }
      function focusChat() {
        if (!preferences.chat || !connected) return false;
        editing = true;shell.dataset.chatEditing = 'true';open.setAttribute('aria-expanded','true');
        revealChat();form.hidden = false;meta.hidden = false;onFocus();input.focus({preventScroll:true});
        log.scrollTop = log.scrollHeight;return true;
      }
      hideChat();form.hidden = true;meta.hidden = true;shell.dataset.chatEditing = 'false';
      function applyPreferences() {
        for (const [key, control] of Object.entries(controls)) {
          control.checked = preferences[key];
          control.closest('label').querySelector('.setting-state').textContent = preferences[key] ? '켬' : '끔';
        }
        open.disabled = !preferences.chat || !connected;
        if (!preferences.chat) {leaveEditor({focusMap:false});hideChat();}
        else scheduleHide();
        if (preferences.chat) log.scrollTop = log.scrollHeight;
      }
      for (const [key, control] of Object.entries(controls)) control.addEventListener('change', () => {
        preferences[key] = control.checked;
        try {localStorage.setItem(storageKey, JSON.stringify(preferences));} catch {}
        applyPreferences();
      });
      applyPreferences();
      $('settingsAccount').hidden = !$('settingsAccount').childElementCount;
      function openSettings() {
        if (dialog.open) return;
        onFocus();dialog.showModal();$('closeSettings').focus();
      }
      $('settingsBtn').addEventListener('click', openSettings);
      $('closeSettings').addEventListener('click', () => dialog.close());
      dialog.addEventListener('close', () => {onFocus();$('mapCanvas').focus({preventScroll:true});});
      dialog.addEventListener('click', event => {
        if (event.target !== dialog) return;
        const r = dialog.getBoundingClientRect();
        if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) dialog.close();
      });
      input.addEventListener('focus', onFocus);
      frame.addEventListener('focusout', event => {
        if (editing && !frame.contains(event.relatedTarget)) leaveEditor({focusMap:false});
      });
      frame.addEventListener('keydown', event => {
        if (event.key === 'Escape' && editing) {event.preventDefault();event.stopPropagation();leaveEditor();}
      });
      input.addEventListener('keydown', event => {
        if (event.key !== 'Enter' || composing || event.isComposing || event.keyCode === 229) return;
        event.preventDefault();event.stopPropagation();
        if (event.repeat || pending) return;
        if (!input.value.trim()) {leaveEditor();return;}
        form.requestSubmit();
      });
      function countInput() {
        const letters = Array.from(input.value);
        if (letters.length > 200) input.value = letters.slice(0,200).join('');
        $('chatCount').textContent = `${Math.min(letters.length,200)}/200`;
      }
      input.addEventListener('input', event => {if (!event.isComposing) countInput();});
      input.addEventListener('compositionstart', () => {composing = true;});
      input.addEventListener('compositionend', () => {composing = false;countInput();});
      function finishPending() {
        pending = false;clearTimeout(pendingTimer);pendingTimer = null;
        input.readOnly = false;send.disabled = !connected;
      }
      function reportError(message) {
        const wasPending = pending;finishPending();status.textContent = message;status.dataset.state = 'error';
        if (wasPending && connected && preferences.chat) focusChat();
        else {revealChat();meta.hidden = !preferences.chat;scheduleHide();}
      }
      const stickerButton=document.createElement('button');stickerButton.type='button';stickerButton.className='chat-kkuwang-button';stickerButton.title='꾸왕이 이모티콘';stickerButton.setAttribute('aria-label','꾸왕이 이모티콘 선택');stickerButton.setAttribute('aria-expanded','false');stickerButton.setAttribute('aria-controls','chatKkuwangPicker');
      const stickerImage=document.createElement('img');stickerImage.src=new URL('assets/kkuwang-chat-happy.png',document.baseURI).href;stickerImage.alt='';stickerButton.append(stickerImage);send.after(stickerButton);
      const stickerPicker=document.createElement('section');stickerPicker.id='chatKkuwangPicker';stickerPicker.className='chat-kkuwang-picker';stickerPicker.hidden=true;stickerPicker.setAttribute('aria-label','꾸왕이 이모티콘');
      const pickerHeading=document.createElement('div');pickerHeading.className='chat-kkuwang-picker-heading';const pickerTitle=document.createElement('strong');pickerTitle.textContent='꾸왕이 이모티콘';
      const pickerClose=document.createElement('button');pickerClose.type='button';pickerClose.textContent='×';pickerClose.setAttribute('aria-label','이모티콘 선택 닫기');pickerHeading.append(pickerTitle,pickerClose);stickerPicker.append(pickerHeading);
      const stickerDefinitions=[
        ['기본',35,510,170,166],['웃음',215,510,175,166],['윙크',402,510,175,166],
        ['놀람',580,503,175,173],['기쁨',765,503,185,173],['장난',955,510,175,166],['잠',1138,538,165,138],
        ['공부하기',865,738,210,220],['인사하기',1290,738,210,220]
      ];
      const stickerByToken=new Map(stickerDefinitions.map(def=>[`:꾸왕이-${def[0]}:`,def]));
      function createSticker(def){
        const [label,x,y,w,h]=def,icon=document.createElement('span');icon.className='chat-kkuwang-sprite';icon.setAttribute('role','img');icon.setAttribute('aria-label',`꾸왕이 ${label}`);icon.contentEditable='false';icon.dataset.stickerToken=`:꾸왕이-${label}:`;
        icon.style.backgroundImage=`url("${new URL('assets/kkuwang-chat-sheet.png',document.baseURI).href}")`;
        icon.style.backgroundSize=`${1536/w*100}% ${1024/h*100}%`;icon.style.backgroundPosition=`${x/(1536-w)*100}% ${y/(1024-h)*100}%`;return icon;
      }
      const stickerGrid=document.createElement('div');stickerGrid.className='chat-kkuwang-grid';
      for(const def of stickerDefinitions){
        const choice=document.createElement('button');choice.type='button';choice.className='chat-kkuwang-choice';choice.setAttribute('aria-label',`꾸왕이 ${def[0]} 넣기`);
        const label=document.createElement('span');label.textContent=def[0];choice.append(createSticker(def),label);stickerGrid.append(choice);
        choice.addEventListener('click',()=>insertSticker(`:꾸왕이-${def[0]}:`));
      }
      stickerPicker.append(stickerGrid);form.append(stickerPicker);
      function closeStickerPicker(focus=false){stickerPicker.hidden=true;stickerButton.setAttribute('aria-expanded','false');if(focus)stickerButton.focus();}
      stickerButton.addEventListener('click',()=>{
        if(!connected||pending)return;const show=stickerPicker.hidden;stickerPicker.hidden=!show;stickerButton.setAttribute('aria-expanded',String(show));if(show)stickerGrid.querySelector('button').focus();
      });
      pickerClose.addEventListener('click',()=>closeStickerPicker(true));
      stickerPicker.addEventListener('keydown',event=>{event.stopPropagation();if(event.key==='Escape'){event.preventDefault();closeStickerPicker(true);}});
      document.addEventListener('pointerdown',event=>{if(!stickerPicker.hidden&&!stickerPicker.contains(event.target)&&!stickerButton.contains(event.target))closeStickerPicker();});
      function insertSticker(token){
        if(!connected||pending)return;const start=input.selectionStart??input.value.length,end=input.selectionEnd??input.value.length;
        if(Array.from(input.value.slice(0,start)+token+input.value.slice(end)).length>200){status.textContent='이모티콘을 넣을 공간이 부족해요.';return;}
        input.setRangeText(token,start,end,'end');input.dispatchEvent(new Event('input',{bubbles:true}));closeStickerPicker();input.focus();
      }
      function messageContent(target,text){
        target.replaceChildren();for(const part of text.split(/(:꾸왕이(?:-[^:\s]+)?:)/g)){
          if(stickerByToken.has(part)){const icon=createSticker(stickerByToken.get(part));icon.classList.add('chat-kkuwang-sticker');target.append(icon);}
          else if(part===':꾸왕이:'){const img=document.createElement('img');img.src=stickerImage.src;img.alt='웃는 꾸왕이';img.className='chat-kkuwang-sticker';img.contentEditable='false';img.dataset.stickerToken=':꾸왕이:';target.append(img);}
          else if(part)target.append(document.createTextNode(part));
        }
      }
      function setConnected(value) {
        connected = value;stickerButton.disabled=!value;if(!value)closeStickerPicker();input.disabled = !value;send.disabled = !value || pending;
        open.disabled = !value || !preferences.chat;
        if(value&&!entryShown){entryShown=true;entryVisibleUntil=performance.now()+60000;revealChat();if(!editing&&!document.querySelector('dialog[open]')&&!window.CampusGuide?.blocking&&!window.CampusGuide?.pending)$('mapCanvas').focus({preventScroll:true});}
        status.dataset.state = value ? 'ready' : 'waiting';
        status.textContent = value ? 'Enter로 채팅 · Esc로 이동' : (config.chatSocketPath ? '연결 중에는 메시지를 보낼 수 없어요.' : '로그인 후 채팅을 사용할 수 있어요.');
        if (!value) {
          finishPending();
          if (editing) leaveEditor();
          else scheduleHide();
          for (const bubble of bubbles.values()) bubble.element.remove();
          bubbles.clear();
        }
      }
      setConnected(false);
      form.addEventListener('submit', event => {
        event.preventDefault();
        const text = input.value.trim();
        if (!text || !connected || pending || composing) return;
        if (!onSend(text)) {reportError('연결을 확인한 후 다시 보내 주세요.');return;}
        pending = true;input.readOnly = true;send.disabled = true;
        clearDisplayTimers();
        status.textContent = '보내는 중…';status.dataset.state = 'waiting';
        pendingTimer = setTimeout(() => reportError('전송을 확인하지 못했어요. 연결 후 다시 보내 주세요.'),5000);
      });
      function receive(message) {
        if (seen.has(message.id)) return;
        seen.add(message.id);
        const self = message.playerId === getSelfId();
        const follow = self || log.scrollHeight - log.scrollTop - log.clientHeight < 30;
        const entry = document.createElement('li');entry.className = 'chat-message';entry.dataset.messageId = message.id;entry.dataset.self = String(self);
        const heading = document.createElement('div');heading.className = 'chat-message-heading';
        const name = document.createElement('strong');name.textContent = self ? `${message.nickname} (나)` : message.nickname;
        const floor = document.createElement('span');floor.className = 'chat-floor';floor.textContent = message.floor === 'rooftop' ? '옥상' : '18F';
        const time = document.createElement('time');time.dateTime = message.sentAt;time.textContent = new Intl.DateTimeFormat('ko-KR',{hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(message.sentAt));
        const text = document.createElement('p');messageContent(text,message.text);
        heading.append(name,floor,time);entry.append(heading,text);log.append(entry);
        while (log.children.length > 100) {seen.delete(log.firstElementChild.dataset.messageId);log.firstElementChild.remove();}
        $('chatEmpty').hidden = true;
        if (self && pending) {
          const returnToMap = editing;finishPending();input.value = '';countInput();
          status.textContent = 'Enter로 채팅 · Esc로 이동';status.dataset.state = 'ready';
          if (returnToMap) leaveEditor();
        }
        revealChat();
        if (follow) log.scrollTop = log.scrollHeight;
        let bubble = bubbles.get(message.playerId);
        if (!bubble) {
          const element = document.createElement('div');element.className = 'speech-bubble';element.dataset.playerId = message.playerId;layer.append(element);
          bubble = {element,shown:false};bubbles.set(message.playerId,bubble);
        }
        Object.assign(bubble,{floor:message.floor,expiresAt:performance.now()+10000});
        messageContent(bubble.element,message.text);bubble.element.dataset.self = String(self);
      }
      return {
        preferences, receive, setConnected, reportError, openSettings,
        get isTyping() {return editing;},
        focusChat, leaveEditor,
        render(t,{actors,camera,projectActor,selfId,floor,avatarSize}) {
          // 말풍선은 사람마다 하나를 두고 지우지 않는다(그 사람이 이 층을 떠난 뒤에만): 보이기·숨기기는 투명도, 새 메시지는 글자만 바꾼다.
          // 위치는 transform 이라 레이아웃·다시 그리기 없이 합성만 한다(사람 많을 때 GPU 프로세스 CPU 36→23%, 계획 B)
          const byId = new Map(actors.map(actor => [actor.id,actor]));
          for (const [id,bubble] of bubbles) {
            const actor = id === selfId ? actors[0] : byId.get(id);
            if (!actor && t >= bubble.expiresAt) {bubble.element.remove();bubbles.delete(id);continue;}
            const show = t < bubble.expiresAt && preferences.bubbles && bubble.floor === floor && Boolean(actor);
            if (show !== bubble.shown) {bubble.element.style.opacity = show ? '1' : '0';bubble.element.setAttribute('aria-hidden',String(!show));bubble.shown = show;}
            if (!show) continue;
            const q = projectActor(actor), nameSpace = actor === actors[0] || preferences.nicknames ? 24*camera.zoom/Math.max(camera.zoom,.55) : 7;
            const x = Math.round(q.x*camera.zoom+camera.x), y = Math.round(q.y*camera.zoom+camera.y-avatarSize*camera.zoom-nameSpace);
            if (x !== bubble.x || y !== bubble.y) {bubble.element.style.transform = `translate3d(${x}px,${y}px,0) translate(-50%,-100%)`;bubble.x = x;bubble.y = y;}
          }
        },
      };
    },
  };
})();
