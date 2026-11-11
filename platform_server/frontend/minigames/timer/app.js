import { GAME_CONFIG } from './config.js';
import { TimerGame, resultMessage, resolveParentOrigin } from './engine.js';

const $ = (id) => document.getElementById(id);
const action = $('action-button');
const fields = [...$('settings-form').elements];
const formatSeconds = (ms) => (ms / 1000).toFixed(2);
const signedSeconds = (ms) => `${ms < 0 ? '−' : '+'}${formatSeconds(Math.abs(ms))}초`;
let game;
let parentOrigin;
try {
  game = new TimerGame(GAME_CONFIG);
  parentOrigin = resolveParentOrigin(GAME_CONFIG, new URLSearchParams(location.search).get('parentOrigin'), location.origin);
} catch (error) {
  $('result-title').textContent = '게임 설정을 확인해주세요.';
  $('result-description').textContent = error.message;
  action.disabled = true;
  fields.forEach((field) => { field.disabled = true; });
  throw error;
}

let animationFrame;
let deadlineTimer;
let roundNumber = 0;

// 눈금과 아이콘도 로컬에서 생성하므로 외부 에셋 요청이 없습니다.
for (let tick = 0; tick < 60; tick += 1) {
  const angle = (tick * 6 - 90) * Math.PI / 180;
  const major = tick % 5 === 0;
  const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
  for (const [name, value] of Object.entries({
    x1: 180 + Math.cos(angle) * (major ? 129 : 134),
    y1: 180 + Math.sin(angle) * (major ? 129 : 134),
    x2: 180 + Math.cos(angle) * 141,
    y2: 180 + Math.sin(angle) * 141,
    class: `dial-tick${major ? ' major' : ''}`,
  })) line.setAttribute(name, value);
  $('dial-ticks').append(line);
}

function updateMission() {
  const { targetMs, toleranceMs } = game.config;
  $('target-value').textContent = formatSeconds(targetMs);
  $('dial-target-value').textContent = formatSeconds(targetMs);
  $('tolerance-value').textContent = toleranceMs===0?'11.11초 일치':`± ${formatSeconds(toleranceMs)}초`;
  $('range-min').textContent = `${formatSeconds(targetMs - toleranceMs)}초`;
  $('range-max').textContent = `${formatSeconds(targetMs + toleranceMs)}초`;
  $('target-input').value = targetMs / 1000;
  $('tolerance-input').value = toleranceMs / 1000;
}

function setStatus(state, text) {
  $('game-status').dataset.state = state;
  $('status-text').textContent = text;
}

function paintTime(elapsedMs) {
  // 최대 목표(300초)와 초과 대기 시간에도 맞도록 크기를 조절합니다.
  const [seconds, fraction] = formatSeconds(elapsedMs).split('.');
  $('time-seconds').textContent = seconds.padStart(2, '0');
  $('time-fraction').textContent = fraction;
  $('time-seconds').parentElement.style.fontSize = seconds.length > 2 ? 'clamp(32px, 7vw, 45px)' : '';
  $('dial-progress').style.strokeDashoffset = 100 - Math.min(100, elapsedMs / game.config.targetMs * 100);
}

function setAction(running, replay = false) {
  action.dataset.state = running ? 'running' : 'idle';
  $('action-label').textContent = running ? '지금 멈추기' : replay ? '다시 도전하기' : '도전 시작';
  $('action-icon').innerHTML = running
    ? '<rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor"/>'
    : replay
      ? '<path d="M5 9a7 7 0 1 1-1 5M5 4v5h5" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
      : '<path d="M9 5l11 7-11 7V5Z" fill="currentColor"/>';
}

function complete(result) {
  if (!result) return;
  cancelAnimationFrame(animationFrame);
  clearTimeout(deadlineTimer);
  paintTime(result.elapsedMs);
  $('dial').dataset.outcome = result.success ? 'success' : 'failure';
  $('timer-overline').textContent = 'YOUR TIMING';
  setAction(false, true);
  fields.forEach((field) => { field.disabled = false; });
  setStatus(result.success ? 'success' : 'failure', result.success ? '멋진 타이밍!' : result.reason === 'hidden' ? '도전 중단' : '조금 아쉬워요');
  if (result.success) {
    $('result-title').textContent = '정확한 순간을 잡았어요!';
    $('result-description').textContent = `목표와의 차이 ${signedSeconds(result.errorMs)} · 성공 구간 안에 멈췄어요.`;
  } else if (result.reason === 'hidden') {
    $('result-title').textContent = '화면을 벗어나 도전이 종료됐어요.';
    $('result-description').textContent = '다시 시작하고, 이번에는 끝까지 집중해보세요.';
  } else if (result.reason === 'timeout') {
    $('result-title').textContent = '멈출 타이밍을 놓쳤어요.';
    $('result-description').textContent = '제한 시간이 지났어요. 다시 도전해보세요!';
  } else {
    $('result-title').textContent = result.errorMs < 0 ? '조금만 더 기다렸다면!' : '조금만 더 빨랐다면!';
    $('result-description').textContent = `목표보다 ${formatSeconds(Math.abs(result.errorMs))}초 ${result.errorMs < 0 ? '빨랐어요' : '늦었어요'}. 다시 도전해볼까요?`;
  }

  // 경과 시간/프레임을 보내지 않고 라운드마다 판정 결과 한 건만 전달합니다.
  const message = resultMessage(result);
  if (window.minigameChannel) {
    window.minigameChannel.complete({duration_ms:Math.round(result.elapsedMs),status:result.success?'SUCCESS':'FAILED'});
    action.disabled = true;
  } else if (parentOrigin && window.parent !== window) window.parent.postMessage(message, parentOrigin);
  window.dispatchEvent(new CustomEvent('timer-game:result', { detail: message }));
}

function frame() {
  if (game.state !== 'running') return;
  const timeout = game.checkTimeout();
  if (timeout) { complete(timeout); return; }
  paintTime(game.elapsed());
  animationFrame = requestAnimationFrame(frame);
}

function watchDeadline() {
  if (game.state !== 'running') return;
  const timeout = game.checkTimeout();
  if (timeout) { complete(timeout); return; }
  const remaining = game.config.targetMs + game.config.toleranceMs + game.config.overtimeMs - game.elapsed();
  deadlineTimer = setTimeout(watchDeadline, Math.max(1, remaining + 1));
}

function activate() {
  if (window.minigameChannel && action.disabled) return;
  if (game.state === 'running') {
    complete(game.checkTimeout() ?? game.finish());
    return;
  }
  if (!game.start()) return;
  roundNumber += 1;
  $('round-label').textContent = `ROUND ${String(roundNumber).padStart(2, '0')}`;
  $('dial').removeAttribute('data-outcome');
  $('timer-overline').textContent = 'ELAPSED TIME';
  $('result-title').textContent = '시간이 흐르고 있어요.';
  $('result-description').textContent = '목표에 맞춰, 바로 그 순간에 멈추세요.';
  $('settings-error').textContent = '';
  $('settings-details').open = false;
  fields.forEach((field) => { field.disabled = true; });
  setAction(true);
  setStatus('running', '타이밍에 집중하세요');
  paintTime(0);
  animationFrame = requestAnimationFrame(frame);
  watchDeadline();
}

action.addEventListener('click', activate);
document.addEventListener('keydown', (event) => {
  if (event.code !== 'Space' || event.ctrlKey || event.altKey || event.metaKey || event.isComposing) return;
  if (event.target.closest('input, textarea, select, summary, a, [contenteditable], button:not(#action-button)')) return;
  event.preventDefault();
  if (!event.repeat) activate();
});

// 비활성 탭에서 rAF가 멈추더라도 라운드를 일시 정지하거나 계속할 수 없습니다.
document.addEventListener('visibilitychange', () => {
  if (document.hidden && game.state === 'running') complete(game.finish('hidden'));
});

function configure(next) {
  game.configure({ ...game.config, ...next });
  $('dial').removeAttribute('data-outcome');
  $('timer-overline').textContent = 'ELAPSED TIME';
  $('result-title').textContent = '새로운 목표가 준비됐어요.';
  $('result-description').textContent = '성공 구간을 확인하고, 도전을 시작하세요.';
  $('settings-error').textContent = '';
  paintTime(0);
  setAction(false);
  setStatus('idle', '준비되셨나요?');
  updateMission();
}

$('settings-form').addEventListener('submit', (event) => {
  event.preventDefault();
  try {
    configure({ targetMs: $('target-input').valueAsNumber * 1000, toleranceMs: $('tolerance-input').valueAsNumber * 1000 });
    $('settings-details').open = false;
  } catch (error) { $('settings-error').textContent = error.message; }
});


// 같은 페이지에 직접 삽입할 때 사용할 API. 상태를 직접 변경하는 객체는 노출하지 않습니다.
window.timerGame = Object.freeze({
  start: () => { if (game.state !== 'running') activate(); },
  stop: () => { if (game.state === 'running') activate(); },
  configure,
  getState: () => Object.freeze({ state: game.state, config: game.config, result: game.result }),
});
$('settings-card').hidden = !GAME_CONFIG.showSettings;
updateMission();
if (window.minigameChannel) {
  action.disabled = true;
  window.minigameChannel.start((config) => {
    configure(config);
    action.disabled = false;
  });
}
