/* Transport keyboard intentions and display the positions accepted by the backend. */
(() => {
  'use strict';
  window.CampusPresence = {
    create({player, width, height, floors, onAuthExpired, onRestore = () => {}, onState = () => {}, onControlEvent = () => {}, onPoke = () => {}, onJump = () => {}, onChat = () => {}, onChatError = () => {}, onConnectionChange = () => {}, onControlError = () => {}, onLogoutPending = () => {}}) {
      const configElement = document.getElementById('campusPresenceConfig');
      const status = document.getElementById('presenceStatus');
      const roster = document.getElementById('presencePeers');
      const peers = new Map();
      if (!configElement) return {peers, connected:false, chatConnected:false, selfId:null, sendChat(){return false;}, setPoking(){}, setInput() {}, command(){return Promise.reject(new Error('연결이 필요해요.'));}, tick() {}, close() {}};
      const config = JSON.parse(configElement.textContent);
      let socket = null, chatSocket = null, selfId = null, ready = false, chatReady = false, closed = false;
      let chatToken = null, chatTokenFloor = null, connectionAvailable = null;
      let inputKeys = [], sequence = 0, poking = false, pokeContext = null;
      const pending = new Map();
      const allowedKeys = new Set(['w','a','s','d','arrowup','arrowdown','arrowleft','arrowright','shift','control']);
      let retry = null, retryDelay = 1000, heartbeat = null, guardKeepalive = null, lastSent = -Infinity, lastInput = '', authChecked = false;
      let chatRetry = null, chatRetryDelay = 1000, chatHeartbeat = null, chatAuthChecked = false;
      let lastReceived = 0, lastChatReceived = 0;
      const floorBounds = floors || {campus:{width,height}};
      const colors = new Set(['#dfa34d', '#4e9b91', '#8a78bb', '#cb7773', '#648eb5']);
      const directions = new Set(['up', 'down', 'left', 'right']);
      const avatars = new Set(['male-1', 'male-2', 'female-1', 'female-2']);

      function setStatus(text, state) {
        status.hidden = false;
        status.replaceChildren();
        if(state==='connected'){
          const icon=document.createElement('span');icon.className='snb-presence-icon';icon.setAttribute('aria-hidden','true');
          icon.innerHTML='<svg viewBox="0 0 24 24" width="21" height="21" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><circle cx="9" cy="7" r="3"/><path d="M3 20v-2a6 6 0 0 1 12 0v2m1-16a3 3 0 0 1 0 6m2 3a5 5 0 0 1 3 5v2"/></svg><i></i>';
          const label=document.createElement('span');label.className='snb-presence-label';label.textContent='접속 중';
          const count=document.createElement('strong');count.className='snb-presence-count';count.textContent=String(peers.size+1);
          const unit=document.createElement('span');unit.className='snb-presence-unit';unit.textContent='명';
          status.append(icon,label,count,unit);
        }else status.textContent=text;
        status.dataset.state = state;
        notifyConnection();
      }
      function notifyConnection() {
        const available = ready && chatReady && !!chatToken;
        if (available !== connectionAvailable) {connectionAvailable = available;onConnectionChange(available);}
      }
      function acceptChatToken(message, own) {
        const floor = own.floor || 'campus';
        if (floor !== chatTokenFloor) chatToken = null;
        chatTokenFloor = floor;
        if (typeof message.chatToken === 'string' && message.chatToken) chatToken = message.chatToken;
        notifyConnection();
      }
      function updateRoster() {
        setStatus(`전체 접속 ${peers.size + 1}명`, 'connected');
        roster.replaceChildren();
        for (const peer of peers.values()) {
          const item = document.createElement('li');
          item.textContent = peer.nickname;
          roster.append(item);
        }
      }
      function validPeer(p) {
        const floor = p?.floor===undefined?'campus':p.floor;
        const bounds = typeof floor==='string'&&Object.hasOwn(floorBounds,floor)&&floorBounds[floor];
        return p && typeof p.id === 'string' && typeof p.nickname === 'string'
          && (p.floor===undefined||typeof p.floor==='string') && bounds
          && Number.isFinite(p.x) && Number.isFinite(p.y) && p.x >= 0 && p.x <= bounds.width && p.y >= 0 && p.y <= bounds.height
          && colors.has(p.color) && directions.has(p.dir) && typeof p.moving === 'boolean'
          && (p.running === undefined || typeof p.running === 'boolean')
          && (p.guarding === undefined || typeof p.guarding === 'boolean')
          && (p.inClassroom === undefined || typeof p.inClassroom === 'boolean')
          && (p.avatar === undefined || avatars.has(p.avatar));
      }
      function upsert(p) {
        if (!validPeer(p) || p.id === selfId) return false;
        p = {...p,floor:p.floor||'campus',avatar:p.avatar||'male-1',inClassroom:p.inClassroom===true,running:p.running===true,guarding:p.guarding===true};
        const now = performance.now(), peer = peers.get(p.id);
        if (!peer) {
          peers.set(p.id, {...p, targetX: p.x, targetY: p.y, receivedAt: now});
          return true;
        }
        const renamed = peer.nickname !== p.nickname || peer.floor !== p.floor;
        // Room navigation teleports instead of drawing a trip through walls.
        if (peer.inView===false&&p.inView===true||peer.floor!==p.floor||Math.hypot(peer.x - p.x, peer.y - p.y) > 180) {peer.x = p.x; peer.y = p.y;}
        Object.assign(peer, {targetX: p.x, targetY: p.y, dir: p.dir, color: p.color,
          inView:p.inView??peer.inView, peperoRemaining:p.peperoRemaining??0, moving: p.moving, running:p.running, guarding:p.guarding, nickname: p.nickname, avatar:p.avatar, floor:p.floor, inClassroom:p.inClassroom, receivedAt: now});
        return renamed;
      }
      function updatePeer(p) {
        const peer = p && peers.get(p.id);
        if (!peer) return false;
        if (!validPeer(p) && (typeof p.floor !== 'string' || typeof p.inClassroom !== 'boolean')) return false;
        // Only known avatars can reuse cached identity for a partial movement.
        return upsert({...p,
          nickname:p.nickname === undefined ? peer.nickname : p.nickname,
          avatar:p.avatar === undefined ? peer.avatar : p.avatar,
          color:p.color === undefined ? peer.color : p.color});
      }
      function publish(t, force = false) {
        if (!ready || socket?.readyState !== WebSocket.OPEN || (!force && t - lastSent < 100)) return;
        socket.send(JSON.stringify({type:'input',keys:inputKeys,...(poking?{poking:true,...(force?{pokeContext}: {})}: {})}));
        lastSent=t;
      }
      function setInput(keys) {
        const next=[...new Set(keys)].filter(key=>allowedKeys.has(key)).sort(), encoded=JSON.stringify(next);
        inputKeys=next;
        if(encoded!==lastInput){lastInput=encoded;publish(performance.now(),true);}
      }
      function setPoking(active,scope=null,keys=inputKeys) {
        const next=[...new Set(keys)].filter(key=>allowedKeys.has(key)).sort(), encoded=JSON.stringify(next);
        const value=active===true;
        if(value===poking&&encoded===lastInput&&(!value||JSON.stringify(scope)===JSON.stringify(pokeContext)))return;
        inputKeys=next;lastInput=encoded;poking=value;pokeContext=value?scope:null;
        publish(performance.now(),true);
      }
      function command(action,data={}) {
        if(!ready||socket?.readyState!==WebSocket.OPEN)return Promise.reject(new Error('연결된 뒤 이용해 주세요.'));
        const requestId=String(++sequence);
        return new Promise((resolve,reject)=>{
          const timer=setTimeout(()=>{pending.delete(requestId);reject(new Error('서버 응답을 기다리지 못했어요. 다시 시도해 주세요.'));},10000);
          pending.set(requestId,{resolve,reject,timer});
          socket.send(JSON.stringify({type:'control',requestId,action,data}));
        });
      }
      function rejectPending() {
        for(const {reject,timer} of pending.values()){clearTimeout(timer);reject(new Error('연결이 끊겼어요. 다시 연결해 주세요.'));}
        pending.clear();inputKeys=[];lastInput='';poking=false;pokeContext=null;player.moving=false;player.running=false;player.guarding=false;
      }
      function acceptSelf(p) {
        if(!validPeer(p)||p.id!==selfId)return;
        const floor=p.floor||'campus';
        applyIdentity(p);
        if(player.floor!==floor||!Number.isFinite(player.targetX)||Math.hypot(player.x-p.x,player.y-p.y)>80){player.x=p.x;player.y=p.y;}
        Object.assign(player,{peperoRemaining:p.peperoRemaining??0,floor,targetX:p.x,targetY:p.y,dir:p.dir,moving:p.moving,running:p.running===true,guarding:p.guarding===true,inClassroom:p.inClassroom===true,receivedAt:performance.now()});
      }
      function backoff() {
        // 2배씩 늘리되(최대 10초) 사람마다 0.5~1.5배로 흩뜨린다: 여럿이 함께 끊겨도 한순간에 다시 몰리지 않게(계획 6d)
        const delay = retryDelay * (0.5 + Math.random()); retryDelay = Math.min(retryDelay * 2, 10000); return delay;
      }
      function reconnectNow() {
        if(closed)return;
        const previous=socket;socket=null;
        rejectPending();ready=false;chatToken=null;notifyConnection();peers.clear();roster.replaceChildren();
        clearInterval(heartbeat);clearInterval(guardKeepalive);clearTimeout(retry);
        previous?.close(4000,'Reconnecting');
        connect();
      }
      function connect() {
        if (closed) return;
        clearTimeout(retry);
        ready = false;chatToken = null;chatTokenFloor = null;
        setStatus('연결 중…', 'connecting');
        const url = new URL(config.movementSocketPath, window.location.href);
        url.protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        url.searchParams.set('control','server');
        url.searchParams.set('moves','compact');
        url.searchParams.set('view','nearby');
        socket = new WebSocket(url.href);
        const current = socket;
        current.onmessage = event => {
          if (current !== socket || closed) return;
          let message;
          try {message = JSON.parse(event.data);} catch {return;}
          if (!message || typeof message !== 'object') return;
          lastReceived=performance.now();
          if (message.type === 'welcome' && Array.isArray(message.players)) {
            const own = message.players.find(p => p?.id === message.selfId);
            if (!validPeer(own)) return;
            window.CampusSession?.receive(message.session);
            selfId = message.selfId; peers.clear();
            acceptChatToken(message, own);
            applyIdentity(own);
            
            if(validPeer(own)){onRestore(own);acceptSelf(own);}
            for (const p of message.players) upsert(p);
            ready = true; retryDelay = 1000; lastSent = -Infinity; lastInput = ''; authChecked = false;
            updateRoster(); publish(performance.now());
            if (!chatSocket) connectChat();
            clearInterval(heartbeat);
            clearInterval(guardKeepalive);
            // Rendering may briefly stall while large dialogs/assets open. Keep
            // Ctrl defense alive independently while the physical key is held.
            guardKeepalive=setInterval(()=>{
              const now=performance.now();
              if(inputKeys.includes('control')&&now-lastSent>=140)publish(now,true);
            },100);
            heartbeat = setInterval(() => {
              if(current!==socket||closed||current.readyState!==WebSocket.OPEN)return;
              if(!document.hidden&&performance.now()-lastReceived>10000){reconnectNow();return;}
              current.send(JSON.stringify({type: 'ping'}));
            }, 5000);
          } else if (ready && message.type === 'control_state' && validPeer(message.state?.main) && message.state.main.id === selfId) {
            acceptChatToken(message, message.state.main);
            onState(message.state);acceptSelf(message.state.main);
          } else if (ready && message.type === 'control_event' && message.event) {
            onControlEvent(message.event);
          } else if (ready && message.type === 'jump') {
            onJump(message);
          } else if (ready && ['poke','poke_state','poke_hit','pepero_inventory','pepero_status'].includes(message.type)) {
            onPoke(message);
          } else if (ready && message.type === 'control_result') {
            const task=pending.get(message.requestId);
            if(task){pending.delete(message.requestId);clearTimeout(task.timer);if(message.error)task.reject(new Error(message.error));else task.resolve(message.result);}
          } else if (ready && message.type === 'control_error' && typeof message.message === 'string') {
            onControlError(message.message);
          } else if (message.type === 'session_expired') {
            close();
            window.CampusSession?.expire(message.reason);
          } else if (ready && message.type === 'identity') {
            applyIdentity(message.player);
          } else if (ready && message.type === 'peer_identity') {
            const p=message.player,peer=p&&peers.get(p.id);
            if(peer&&typeof p.nickname==='string'&&avatars.has(p.avatar)&&colors.has(p.color)){
              Object.assign(peer,{nickname:p.nickname,avatar:p.avatar,color:p.color});updateRoster();
            }
          } else if (ready && message.type === 'join') {
            if (upsert(message.player)) updateRoster();
          } else if (ready && message.type === 'move' && message.player && peers.has(message.player.id)) {
            if (upsert(message.player)) updateRoster();
          } else if (ready && message.type === 'moves' && Array.isArray(message.players)) {
            // 0.1초마다 묶여 오는 위치. 이미 아는 사람만 갱신한다(떠난 사람이 늦게 온 묶음으로 되살아나지 않게, 새 사람은 join 으로 온다)
            for(const id of message.hidden||[]){
              const peer=peers.get(id);if(!peer)continue;
              Object.assign(peer,{inView:false,moving:false,running:false,guarding:false});
              for(const key of Object.keys(peer))if(key.startsWith('_campus')||key.startsWith('_pepero'))delete peer[key];
            }
            let renamed=false;for(const p of message.players)renamed=updatePeer(p)||renamed;
            if(renamed)updateRoster();
            // 같은 묶음에 실려 오는 점프·찌르기(서버가 소켓마다 초당 10프레임으로 줄였다)
            if(Array.isArray(message.effects))for(const effect of message.effects){
              if(effect?.type==='jump')onJump(effect);else if(['poke','poke_state','poke_hit','pepero_inventory'].includes(effect?.type))onPoke(effect);
            }
          } else if (ready && message.type === 'leave') {
            if (peers.delete(message.id)) updateRoster();
          }
        };
        current.onclose = event => {
          if (current !== socket) return;
          rejectPending();ready = false; chatToken = null; notifyConnection();peers.clear(); roster.replaceChildren(); clearInterval(heartbeat);clearInterval(guardKeepalive);
          if (closed) return;
          if(event.code===4409){
            close();setStatus('새 연결로 전환됨 · 이 창에서 계속하려면 새로고침하세요','disconnected');
            return;
          }
          if (event.code === 4401 && window.CampusSession?.verify && !authChecked) {
            // 로그인이 정말 끝났는지 세션 상태로 한 번 확인한다(계획 6c): 끝났으면 CampusSession 이 로그인 화면으로 보내고, 살아 있으면 다시 연결한다.
            // 다시 붙기 전에 또 4401 이면 확인 없이 끝낸다(되풀이하지 않게)
            authChecked = true; setStatus('로그인 상태를 확인하는 중…', 'connecting');
            window.CampusSession.verify().then(alive => {
              if (closed || current !== socket) return;
              if (alive) retry = setTimeout(connect, backoff());
              else expireAuthentication();
            });
            return;
          }
          if (event.code === 4401) {
            expireAuthentication();
            return;
          }
          setStatus('연결 끊김 · 다시 연결 중…', 'disconnected');
          retry = setTimeout(connect, backoff());
        };
        // onclose performs recovery; avoid a separate retry for the same failure.
        current.onerror = () => {};
      }
      function expireAuthentication() {
        close();setStatus('로그인이 필요해요', 'disconnected');onAuthExpired();
        if(window.CampusSession)window.CampusSession.expire();else window.location.assign(config.loginUrl);
      }
      function chatBackoff() {
        const delay = chatRetryDelay * (0.5 + Math.random());
        chatRetryDelay = Math.min(chatRetryDelay * 2, 10000);return delay;
      }
      function reconnectChat() {
        if (closed) return;
        const previous = chatSocket;chatSocket = null;chatReady = false;notifyConnection();
        clearTimeout(chatRetry);clearInterval(chatHeartbeat);
        previous?.close(4000, 'Reconnecting');connectChat();
      }
      function connectChat() {
        if (closed || selfId === null) return;
        clearTimeout(chatRetry);chatReady = false;notifyConnection();
        const url = new URL(config.chatSocketPath, window.location.href);
        url.protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const current = chatSocket = new WebSocket(url.href);
        current.onmessage = event => {
          if (current !== chatSocket || closed) return;
          let message;try {message = JSON.parse(event.data);} catch {return;}
          if (!message || typeof message !== 'object') return;
          lastChatReceived = performance.now();
          if (message.type === 'chat_ready') {
            chatReady = true;chatRetryDelay = 1000;chatAuthChecked = false;notifyConnection();
            clearInterval(chatHeartbeat);
            chatHeartbeat = setInterval(() => {
              if (current !== chatSocket || closed || current.readyState !== WebSocket.OPEN) return;
              if (!document.hidden && performance.now() - lastChatReceived > 10000) {reconnectChat();return;}
              current.send(JSON.stringify({type:'ping'}));
            }, 5000);
          } else if (message.type === 'session_expired') {
            close();window.CampusSession?.expire(message.reason);
          } else if (chatReady && message.type === 'private_message' && typeof message.conversationId === 'string') {
            window.dispatchEvent(new CustomEvent('campus:private-message',{detail:{conversationId:message.conversationId}}));
          } else if (chatReady && message.type === 'chat') {
            const m = message.message;
            if (m && typeof m.id === 'string' && typeof m.playerId === 'string'
                && typeof m.nickname === 'string' && typeof m.text === 'string'
                && m.text.trim() && Array.from(m.text).length <= 200
                && typeof m.floor === 'string' && Object.hasOwn(floorBounds,m.floor)
                && typeof m.sentAt === 'string' && Number.isFinite(Date.parse(m.sentAt))) onChat(m);
          } else if (chatReady && message.type === 'chat_error' && typeof message.message === 'string') onChatError(message.message);
        };
        current.onclose = event => {
          if (current !== chatSocket) return;
          chatReady = false;notifyConnection();clearInterval(chatHeartbeat);
          if (closed) return;
          if (event.code === 4409) {close();setStatus('새 연결로 전환됨 · 이 창에서 계속하려면 새로고침하세요','disconnected');return;}
          if (event.code === 4401 && window.CampusSession?.verify && !chatAuthChecked) {
            chatAuthChecked = true;
            window.CampusSession.verify().then(alive => {
              if (closed || current !== chatSocket) return;
              if (alive) chatRetry = setTimeout(connectChat, chatBackoff());else expireAuthentication();
            });
            return;
          }
          if (event.code === 4401) {expireAuthentication();return;}
          chatRetry = setTimeout(connectChat, chatBackoff());
        };
        current.onerror = () => {};
      }
      function applyIdentity(p) {
        if (!validPeer(p) || p.id !== selfId) return;
        const avatar = p.avatar || 'male-1', changed = player.avatar !== avatar || player.nickname !== p.nickname;
        player.avatar = avatar;
        player.nickname = p.nickname;
        if (window.CampusAvatars) player.color = window.CampusAvatars.definitions[player.avatar].color;
        if (changed) window.dispatchEvent?.(new Event('campus:avatar'));  // 아바타·닉네임이 바뀌었을 때만 화면을 다시 그린다(계획 5단계)
      }
      function close() {
        if (closed) return;
        if (ready && socket?.readyState === WebSocket.OPEN) {
          socket.send(JSON.stringify({type:'input',keys:[]}));
          socket.send(JSON.stringify({type:'leave'}));
        }
        rejectPending();closed = true; ready = false; chatReady = false;chatToken = null;peers.clear();
        notifyConnection();
        clearTimeout(retry); clearInterval(heartbeat);clearInterval(guardKeepalive);clearTimeout(chatRetry);clearInterval(chatHeartbeat);
        const previous = socket, previousChat = chatSocket;
        socket = chatSocket = null;selfId = null;
        previous?.close(1000);previousChat?.close(1000);
      }
      window.addEventListener('pagehide', close);
      window.addEventListener('pageshow', event => {
        if (event.persisted) {closed = false; connect();}
      });
      const logoutForm=document.getElementById('campusLogoutForm');let logoutPending=false;
      logoutForm?.addEventListener('submit',async event=>{
        event.preventDefault();
        if(logoutPending)return;
        logoutPending=true;onLogoutPending(true);setPoking(false,null,[]);
        try{
          const result=await command('prepare_logout');
          if(result?.saved!==true)throw new Error('위치 저장을 확인하지 못했어요. 다시 시도해 주세요.');
          close();logoutForm.submit();
        }catch(error){
          logoutPending=false;onLogoutPending(false);onControlError(error.message);
        }
      });
      document.addEventListener('visibilitychange', () => {
        if (!document.hidden && !closed && (socket?.readyState === WebSocket.CLOSED||performance.now()-lastReceived>10000)) reconnectNow();
        if (!document.hidden && !closed && (chatSocket?.readyState === WebSocket.CLOSED||performance.now()-lastChatReceived>10000)) reconnectChat();
      });
      window.addEventListener('online',()=>{
        if(!closed&&(!ready||performance.now()-lastReceived>10000))reconnectNow();
        if(!closed&&(!chatReady||performance.now()-lastChatReceived>10000))reconnectChat();
      });
      connect();
      return {
        peers, close, setInput, setPoking, command,
        get connected() {return ready;},
        get chatConnected() {return chatReady;},
        get selfId() {return selfId;},
        get inputKeys() {return [...inputKeys];},
        sendChat(text) {
          if (!ready || !chatReady || !chatToken || chatSocket?.readyState !== WebSocket.OPEN || typeof text !== 'string') return false;
          text = text.trim();
          if (!text || Array.from(text).length > 200) return false;
          chatSocket.send(JSON.stringify({type:'chat',text,actorToken:chatToken}));
          return true;
        },
        tick(t, dt) {
          if(inputKeys.length||poking)publish(t); // 누르고 있는 동안만 0.1초마다 갱신한다(서버의 키 유효 시간 0.35초). 놓은 순간은 setInput 이 바로 보낸다
          const blend = 1 - Math.exp(-dt * 18);
          for (const peer of [player,...peers.values()]) {
            if(peer.inView===false||!Number.isFinite(peer.targetX))continue;
            peer.x += (peer.targetX - peer.x) * blend;
            peer.y += (peer.targetY - peer.y) * blend;
            // A quiet authoritative stream means remote animation is stale.
            // The local player is driven by the keys currently held, so clearing
            // its guard here made a held Ctrl pose disappear after 0.3 seconds.
            if(peer!==player&&t-peer.receivedAt>300){
              peer.moving=false;peer.running=false;
              // Scoped guard state lasts until the server releases it or hides the actor.
              if(peer.inView!==true)peer.guarding=false;
            }
          }
        },
      };
    },
  };
})();
