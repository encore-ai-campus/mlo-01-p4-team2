import { GAME_CONFIG } from './config.js';
import { MemoryGame, RULE_KEYS } from './engine.js';
import { TERMS } from './terms.js';
import { GameBridge, resolveParentOrigin } from './bridge.js';

const $ = id => document.getElementById(id);
const embedded = window.parent !== window;
const demoHost = !!window.minigameChannel;
let demoSessionId = null;
const game = new MemoryGame(GAME_CONFIG);
let parentOrigin = null;
try { parentOrigin = resolveParentOrigin(GAME_CONFIG, new URLSearchParams(location.search).get('parentOrigin'), location.origin); }
catch (error) { $('settings-error').textContent = error.message; }
const bridge = new GameBridge({ origin: parentOrigin, parent: window.parent, ownWindow: window,
  configure: patch => applyConfig(patch), start: id => { game.start(id); renderBoard(); render(); },
  onDelivery: status => {
    $('start-note').textContent = status === 'acknowledged' ? '메타버스에 결과를 전달했어요.' : status === 'unconfirmed' ? '결과는 확정됐어요. 메타버스 수신 확인 대기 중이에요.' : '메타버스에 결과를 전달하고 있어요.';
  },
});
let renderedRound = null, renderedPhase = null, describedTerm = null;
const heldConfirmKeys = new Set();
function confirmNextRound() {
  if (!game.continueRound()) return;
  $('round-dialog').close();
  describedTerm = null;
  renderBoard(); render();
  document.querySelector('.board-scroll').focus({ preventScroll: true });
}
function showRoundResult(round, wins) {
  if ($('info-dialog').open) $('info-dialog').close();
  $('round-result-icon').textContent = round.success ? '✓' : '!';
  $('round-dialog').classList.toggle('round-failed', !round.success);
  $('round-result-title').textContent = `${round.round}라운드 ${round.success ? '성공!' : '실패'}`;
  $('round-result-description').textContent = round.success ? '모든 짝을 찾았어요. 결과를 확인하고 다음 도전을 준비해요.' : round.reason === 'timeout' ? '제한 시간이 끝났어요. 잠깐 쉬고 다음 라운드에 도전해요.' : '오답 기회를 모두 사용했어요. 다음 라운드에서 다시 도전해요.';
  $('round-result-pairs').textContent = `${round.matchedPairs} / ${round.totalPairs}`;
  $('round-result-mistakes').textContent = `${round.mistakes}회`;
  $('round-result-time').textContent = `${(round.elapsedMs / 1000).toFixed(1)}초`;
  $('round-result-progress').textContent = `${game.config.totalRounds}라운드 중 ${round.round}회 완료 · 현재 성공 ${wins}회`;
  $('round-result-term').hidden = !game.lastMatch;
  if (game.lastMatch) {
    $('round-result-term-label').textContent = `${game.lastMatch.category} · ${game.lastMatch.label}`;
    $('round-result-term-description').textContent = game.lastMatch.description;
  }
  $('round-dialog').showModal();
  $('round-confirm').focus({ preventScroll: true });
}
// Capture before native button activation. A held key that completed a round
// must be released before a fresh Enter/Space can confirm the result.
window.addEventListener('keydown', event => {
  if (event.key !== 'Enter' && event.key !== ' ') return;
  const alreadyHeld = heldConfirmKeys.has(event.key);
  heldConfirmKeys.add(event.key);
  if (game.phase !== 'transition' || !$('round-dialog').open) return;
  event.preventDefault(); event.stopPropagation();
  if (event.repeat || alreadyHeld || event.isComposing || event.altKey || event.ctrlKey || event.metaKey) return;
  confirmNextRound();
}, true);
window.addEventListener('keyup', event => {
  heldConfirmKeys.delete(event.key);
  if ((event.key === 'Enter' || event.key === ' ') && game.phase === 'transition') event.preventDefault();
}, true);
window.addEventListener('blur', () => heldConfirmKeys.clear());
$('round-confirm').addEventListener('click', event => { if (event.detail < 2) confirmNextRound(); });
$('round-dialog').addEventListener('cancel', event => event.preventDefault());
function applyConfig(patch) {
  const unknown = Object.keys(patch).filter(key => !RULE_KEYS.includes(key) && key !== 'deck');
  if (unknown.length) throw new Error('허용되지 않은 게임 설정 항목이 있습니다.');
  game.configure({ ...game.config, ...(game.serverDeck ? {deck: game.serverDeck} : {}), ...patch });
  renderedRound = null; renderedPhase = null; describedTerm = null;
  syncSettings(); renderBoard(); render();
}
function syncSettings() {
  const c = game.config;
  for (const [key, value] of Object.entries({ time: c.timeLimitMs / 1000, rounds: c.totalRounds, wins: c.requiredWins, mistakes: c.maxMistakes, columns: c.columns, topic: c.topic, preview: c.previewMs / 1000 })) $('setting-' + key).value = String(value);
  $('topic-tag').textContent = c.topic === 'mixed' ? 'Python + SQL' : c.topic === 'python' ? 'Python' : 'SQL';
  $('grid-label').textContent = `4 × ${c.columns}`;
  $('goal-text').textContent = `${c.totalRounds}라운드 중 ${c.requiredWins}회 성공`;
  document.documentElement.style.setProperty('--flip-duration', `${c.flipDurationMs}ms`);
}
function renderBoard() {
  const board = $('board'); board.replaceChildren(); board.style.setProperty('--columns', game.config.columns);
  const cards = game.cards.length ? game.cards : Array.from({ length: 4 * game.config.columns }, (_, i) => ({ id: `placeholder-${i}`, term: null }));
  cards.forEach((card, index) => {
    const button = document.createElement('button'); button.className = 'card'; button.type = 'button';
    button.innerHTML = '<span class="card-inner"><span class="card-back"><span class="card-number"></span><span class="card-flower"><i></i><i></i><i></i></span><span class="card-corner">PAIR</span></span><span class="card-front"><span class="term-category"></span><span class="term-label"></span><span class="match-mark">✓</span></span></span>';
    button.querySelector('.card-number').textContent = String(index + 1).padStart(2, '0');
    button.querySelector('.term-category').textContent = card.term?.category ?? '';
    button.querySelector('.term-label').textContent = card.term?.label ?? '';
    button.addEventListener('click', () => {
      const selection = game.choose(index);
      if (selection.type === 'mismatch' && game.phase === 'playing') say('한 번 더!', '괜찮아요, 위치를 기억해봐요.', '방금 본 두 용어의 자리를 기억하면 다음 짝은 더 쉽게 찾을 수 있어요.');
      render();
    });
    board.append(button);
  });
  renderedRound = game.phase === 'idle' ? null : game.roundIndex;
}
function say(tag, term, description) { $('buddy-tag').textContent = tag; $('buddy-term').textContent = term; $('buddy-description').textContent = description; }
function render(at = performance.now()) {
  if (game.phase !== 'idle' && renderedRound !== game.roundIndex) { renderBoard(); describedTerm = null; say('새로운 라운드', '카드의 위치를 기억해요.', '모든 용어를 잠깐 보여드릴게요. 곧 카드가 하나씩 뒤집혀요.'); }
  const c = game.config, phase = game.phase;
  const wins = game.rounds.filter(r => r.success).length;
  const sec = Math.ceil(game.remainingMs(at) / 1000);
  $('time-value').innerHTML = `${(game.remainingMs(at) / 1000).toFixed(2)}<small>s</small>`;
  $('time-progress').style.width = `${Math.min(100, game.remainingMs(at) / c.timeLimitMs * 100)}%`;
  $('pairs-value').innerHTML = `${game.matched.length} <small>/ ${c.rows * c.columns / 2}</small>`;
  document.querySelector('.game-panel').classList.toggle('low-time', phase === 'playing' && sec <= 10);
  const remaining = c.maxMistakes - game.mistakes;
  const lives = $('lives');
  if (lives.dataset.value !== `${remaining}/${c.maxMistakes}`) {
    lives.replaceChildren(); lives.dataset.value = `${remaining}/${c.maxMistakes}`;
    for (let i = 0; i < Math.min(5, c.maxMistakes); i++) { const heart = document.createElement('span'); heart.textContent = '♥'; heart.className = i >= Math.min(remaining, 5) ? 'spent' : ''; heart.setAttribute('aria-hidden', 'true'); lives.append(heart); }
    if (c.maxMistakes > 5) { const more = document.createElement('span'); more.className = 'more'; more.textContent = `×${remaining}`; lives.append(more); }
    lives.setAttribute('aria-label', `남은 오답 기회 ${remaining}회, 전체 ${c.maxMistakes}회`);
  }
  $('mistake-hint').textContent = `오답 ${game.mistakes} / ${c.maxMistakes}회`;
  $('round-label').innerHTML = `ROUND ${String(game.roundIndex ?? 1).padStart(2, '0')} <span>/ ${String(c.totalRounds).padStart(2, '0')}</span>`;
  $('challenge-count').textContent = `${game.rounds.length} / ${c.totalRounds}`;
  const dotsKey = `${phase}/${game.roundIndex}/${game.rounds.map(r => Number(r.success)).join('')}/${c.totalRounds}`;
  if ($('round-dots').dataset.key !== dotsKey) {
    $('round-dots').replaceChildren(); $('round-dots').dataset.key = dotsKey;
    for (let i = 0; i < c.totalRounds; i++) {
      const dot = document.createElement('span'), round = game.rounds[i];
      dot.className = `round-dot ${round ? round.success ? 'win' : 'loss' : i + 1 === (game.roundIndex ?? 1) ? 'current' : ''}`;
      dot.textContent = round ? round.success ? '✓' : '×' : String(i + 1).padStart(2, '0');
      dot.setAttribute('aria-label', `${i + 1}라운드 ${round ? round.success ? '성공' : '실패' : '대기'}`); $('round-dots').append(dot);
    }
  }
  const controlsLocked = phase !== 'idle' && phase !== 'complete';
  document.querySelectorAll('#settings-form input, #settings-form select, #settings-form button').forEach(el => { el.disabled = controlsLocked; });
  [...$('board').children].forEach((button, i) => {
    const matched = game.matched.includes(game.cards[i]?.term.id), up = game.faceUp(i, at);
    button.classList.toggle('face-up', up); button.classList.toggle('matched', matched);
    button.classList.toggle('mismatch', game.pendingUntil !== null && game.pendingUntil !== undefined && game.selected.includes(i));
    button.disabled = phase !== 'playing' || matched || game.selected.includes(i) || game.pendingUntil != null;
    const label = up && game.cards[i] ? `${game.cards[i].term.category} ${game.cards[i].term.label}${matched ? ', 짝 찾음' : ''}` : '뒷면';
    button.setAttribute('aria-label', `카드 ${i + 1}, ${label}`);
    button.querySelector('.card-front').setAttribute('aria-hidden', String(!up)); button.querySelector('.card-back').setAttribute('aria-hidden', String(up));
  });
  if (game.lastMatch && game.lastMatch.id !== describedTerm) { describedTerm = game.lastMatch.id; say(`${game.lastMatch.category} · 짝을 찾았어요!`, game.lastMatch.label, game.lastMatch.description); }
  if (phase !== renderedPhase) {
    renderedPhase = phase;
    if (phase === 'idle') {
      $('overlay-title').textContent = '잠깐의 기억, 작은 배움.';
      $('overlay-description').textContent = '처음에 카드를 모두 보여드릴게요.\n용어의 위치를 기억하고 같은 짝을 찾아보세요.';
      $('start-button').textContent = embedded && !demoHost ? '메타버스 연결 대기 중' : '게임 시작하기 ↗'; $('start-button').disabled = embedded && (!demoHost || !demoSessionId);
      $('start-note').textContent = embedded && !demoHost ? parentOrigin ? '메타버스에서 설정을 보내면 시작해요.' : '허용된 메타버스 origin 설정이 필요해요.' : `미리보기 ${c.previewMs / 1000}초 · 총 ${c.totalRounds}라운드`;
      say('반가워요!', '우리, 한 쌍씩 찾아볼까요?', '짝을 맞추면 그 용어가 어떤 뜻인지 제가 짧게 알려드릴게요.');
    }
    if (phase === 'preview') say('새로운 라운드', '카드의 위치를 기억해요.', '모든 용어를 잠깐 보여드릴게요. 곧 카드가 하나씩 뒤집혀요.');
    if (phase === 'playing') $('phase-label').textContent = '같은 용어 두 장을 뒤집어 짝을 찾아주세요.';
    if (phase === 'transition') {
      const last = game.rounds.at(-1);
      $('phase-label').textContent = '라운드 결과 확인 대기 중 · 확인 버튼이나 Enter / Space로 다음 라운드를 시작하세요.';
      showRoundResult(last, wins);
    }
    if (phase === 'complete') {
      $('overlay-title').textContent = game.result.success ? '챌린지 통과! 잘했어요.' : '좋은 도전이었어요!';
      $('overlay-description').textContent = `${c.totalRounds}라운드 중 ${wins}회 성공\n통과 기준 ${c.requiredWins}회 · 최종 ${game.result.success ? '통과' : '실패'}`;
      $('start-button').textContent = '한 번 더 도전하기 ↗'; $('start-button').disabled = embedded && !demoHost;
      if (embedded && !demoHost) $('start-button').textContent = '메타버스에서 결과를 확인하세요';
      $('start-note').textContent = '모든 라운드의 판정이 확정되었어요.';
      $('phase-label').textContent = `게임 종료 · 최종 ${game.result.success ? '통과' : '실패'}`;
      $('journey-description').textContent = `${wins}회 성공, ${c.totalRounds - wins}회 실패 · 최종 ${game.result.success ? '통과' : '실패'}`;
      // Consumers in the same document can subscribe without iframe messaging.
      window.dispatchEvent(new CustomEvent('card-game:result', { detail: game.result }));
    }
    if (phase === 'idle') { $('phase-label').textContent = '준비됐나요? 카드 속 용어를 만나보세요.'; $('journey-description').textContent = '모든 라운드를 마치면 결과를 알려드려요.'; }
  }
  if (phase === 'preview') {
    const seconds = Math.max(0, Math.ceil((game.previewStarted + c.previewMs - at) / 1000));
    $('phase-label').textContent = seconds > 0 ? `카드 위치를 기억해요! ${seconds}초 뒤에 뒤집혀요.` : '카드를 하나씩 덮고 있어요. 잠시만 기다려주세요.';
  }
  $('board-overlay').hidden = !['idle', 'complete'].includes(phase);
  $('start-button').hidden = phase === 'transition';
  $('board-bottom-note').textContent = phase === 'preview' ? '아직 제한 시간은 줄어들지 않아요.' : '같은 용어 두 장을 찾아주세요.';
  if (game.result) {
    if (window.minigameChannel) window.minigameChannel.complete({status:game.result.success?'SUCCESS':'FAILED',duration_ms:game.result.rounds[0].elapsedMs,picks:game.result.picks});
    else bridge.publish(game.result, at);
  }
}
$('start-button').addEventListener('click', () => {
  if (demoHost && game.phase === 'complete') { $('start-button').disabled = true; window.minigameChannel.restart(); return; }
  if (demoHost && demoSessionId && game.phase === 'idle' && game.start(demoSessionId)) { describedTerm = null; renderBoard(); render(); }
  else if (!embedded && game.start()) { describedTerm = null; renderBoard(); render(); }
});
$('settings-form').addEventListener('submit', event => {
  event.preventDefault();
  try {
    applyConfig({ timeLimitMs: Math.round($('setting-time').valueAsNumber * 1000), totalRounds: $('setting-rounds').valueAsNumber, requiredWins: $('setting-wins').valueAsNumber, maxMistakes: $('setting-mistakes').valueAsNumber, columns: Number($('setting-columns').value), topic: $('setting-topic').value, previewMs: Math.round($('setting-preview').valueAsNumber * 1000) });
    $('settings-error').textContent = '새 설정을 적용했어요.';
  } catch (error) { $('settings-error').textContent = error.message; }
});
function openDialog(mode) {
  const playing = ['preview', 'playing', 'transition'].includes(game.phase);
  $('dialog-title').textContent = mode === 'terms' ? '용어 모아보기' : '플레이 방법';
  $('dialog-eyebrow').textContent = mode === 'terms' ? 'LITTLE THINGS TO LEARN' : 'HOW TO PLAY';
  $('dialog-content').replaceChildren();
  if (mode === 'how') {
    const list = document.createElement('ol'); list.className = 'how-list';
    for (const text of [`처음 ${game.config.previewMs / 1000}초 동안 모든 용어의 위치를 기억해요. 카드가 순서대로 덮히면 시작!`, `카드를 한 장씩 골라 같은 용어 두 장을 찾아요. 짝을 맞추면 모아가 용어를 설명해 줘요.`, `제한 시간 ${game.config.timeLimitMs / 1000}초 안에 모든 짝을 찾으면 라운드 성공! 오답 ${game.config.maxMistakes}회 또는 시간 초과 시 실패예요.`, `라운드 결과 팝업에서 확인 버튼 또는 Enter / Space를 눌러야 다음 라운드로 넘어가요. 기다리는 동안 다음 라운드 시간은 줄어들지 않아요.`, `총 ${game.config.totalRounds}라운드를 모두 진행해요. 성공 ${game.config.requiredWins}회 이상이면 최종 통과예요.`]) { const li = document.createElement('li'); li.textContent = text; list.append(li); }
    $('dialog-content').append(list);
  } else if (playing) {
    const note = document.createElement('p'); note.className = 'dialog-note'; note.textContent = '게임이 진행 중이에요. 용어 모아보기는 게임이 끝난 뒤에 열 수 있어요.'; $('dialog-content').append(note);
  } else {
    const filter = document.createElement('div'); filter.className = 'term-filter';
    const list = document.createElement('div'); list.className = 'term-list';
    const show = category => {
      list.replaceChildren();
      for (const term of TERMS.filter(t => category === 'all' || t.category === category)) {
        const entry = document.createElement('article'); entry.className = 'term-entry';
        const heading = document.createElement('h3'), badge = document.createElement('span'), p = document.createElement('p');
        badge.textContent = term.category; heading.append(badge, document.createTextNode(term.label)); p.textContent = term.description; entry.append(heading, p); list.append(entry);
      }
      [...filter.children].forEach(b => b.setAttribute('aria-pressed', String(b.dataset.category === category)));
    };
    for (const [category, label] of [['all', '전체 48'], ['Python', 'Python 24'], ['SQL', 'SQL 24']]) { const button = document.createElement('button'); button.textContent = label; button.dataset.category = category; button.addEventListener('click', () => show(category)); filter.append(button); }
    $('dialog-content').append(filter, list); show('all');
  }
  const note = document.createElement('p'); note.className = 'dialog-note'; note.textContent = playing ? '안내 창을 열거나 탭을 벗어나도 제한 시간은 계속 흘러가요.' : '서로 다른 카드를 두 장 고른 경우만 오답 1회로 계산해요. 오답 한도는 라운드마다 초기화돼요.'; $('dialog-content').append(note);
  $('info-dialog').showModal();
}
$('how-button').addEventListener('click', () => openDialog('how'));
$('terms-button').addEventListener('click', () => openDialog('terms'));
$('dialog-close').addEventListener('click', () => $('info-dialog').close());
$('info-dialog').addEventListener('click', event => { if (event.target === $('info-dialog')) { const rect = event.target.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) $('info-dialog').close(); } });
window.addEventListener('message', event => bridge.receive(event));
$('settings').hidden = (embedded && !demoHost) || !GAME_CONFIG.showSettings;
document.addEventListener('visibilitychange', () => { game.tick(); render(); });
// Snapshot is a structured clone: callers cannot mutate the active engine/result.
window.cardGame = Object.freeze({
  configure: config => { if (embedded) throw new Error('iframe 설정은 부모의 init 메시지를 사용하세요.'); applyConfig(config); },
  getState: () => structuredClone({ phase: game.phase, config: game.config, round: game.roundIndex ?? 1, rounds: game.rounds, result: game.result }),
});
syncSettings(); renderBoard(); render(); bridge.ready();
if (window.minigameChannel) window.minigameChannel.start((config, sessionId) => {
  demoSessionId = sessionId;
  applyConfig(config);
  renderBoard(); render();
});
setInterval(() => { game.tick(); render(); }, 50);
