const frame = document.getElementById('game-frame');
const gameUrl = new URL('./index.html', location.href);
gameUrl.searchParams.set('parentOrigin', location.origin);
const received = new Set();

window.addEventListener('message', (event) => {
  if (event.origin !== gameUrl.origin || event.source !== frame.contentWindow) return;
  const data = event.data;
  if (!data || data.type !== 'timer-game:result' || data.version !== 1
    || typeof data.roundId !== 'string' || !data.roundId || typeof data.success !== 'boolean') return;
  if (received.has(data.roundId)) return;
  received.add(data.roundId);
  document.getElementById('message-preview').textContent = JSON.stringify(data, null, 2);
  document.getElementById('received-count').textContent = `수신 ${received.size}건`;
  const row = document.createElement('li');
  row.textContent = `#${received.size} · ${data.success ? '성공' : '실패'} · ${data.roundId.slice(0, 8)}`;
  document.getElementById('host-log').prepend(row);
  // 이 위치에서 메타버스의 완료 처리 함수를 호출하세요.
  // handleMinigameResult({ roundId: data.roundId, success: data.success });
});

// 리스너 등록 후 iframe 로딩을 시작합니다.
frame.src = gameUrl.href;
