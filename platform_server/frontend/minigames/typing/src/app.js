import { problems, pickBeginnerProblem } from './problems.js';
import { validateProblems } from './core/normalize.js';
import { AttemptController, newId } from './core/attemptController.js';
import { formatDuration } from './core/metrics.js';
import { remainingErrors } from './core/typingEngine.js';
import { keyboardRows, keyboardGuide, aggregateKeyMistakes, fingerNames } from './core/keyboard.js';
import { pythonCharacterClasses } from './adapters/pythonHighlight.js';
import { attachTypingInput } from './adapters/nativeTypingInput.js';
const $=id=>document.getElementById(id);
const all=validateProblems(problems);
const channel=window.minigameChannel;
let controller, detach, clock, whitespace=false, minimumAccuracy=95, sessionId=newId(), locked=false;
let spans=[], keys=new Map(), fingers=new Map(), previousCursor=-1;
const visible=c=>c==='\n'?'Enter ↵':c===' '?'Space ␣':c;
function createKeyboard() {
  for(const row of keyboardRows) {
    const el=document.createElement('div'); el.className='key-row';
    for(const key of row) {
      const elKey=document.createElement('span'); elKey.className='key'; elKey.dataset.key=key.id; elKey.style.setProperty('--key-width',key.width??1);
      if(key.shifted) {const small=document.createElement('small');small.textContent=key.shifted;elKey.append(small);}
      const label=document.createElement('span');label.textContent=key.label;elKey.append(label);
      elKey.title=`${key.label} · ${fingerNames[key.finger]}`; elKey.setAttribute('role','img');elKey.setAttribute('aria-label',elKey.title);
      keys.set(key.id,elKey);el.append(elKey);
    } $('keyboard').append(el);
  }
  let svg='<svg class="hands" viewBox="0 0 390 210" role="img" aria-label="手 손가락 가이드"><title>입력할 손가락을 강조합니다.</title>';
  for(const right of [false,true]) {
    svg+=`<g transform="${right?'translate(375 0) scale(-1 1)':'translate(15 0)'}">`;
    const ids=right?[10,9,8,7,6]:[1,2,3,4,5];
    [{x:9,y:62,h:81},{x:40,y:33,h:110},{x:71,y:20,h:123},{x:102,y:40,h:103},{x:135,y:103,h:65}].forEach((f,i)=>{svg+=`<rect class="finger" data-finger="${ids[i]}" x="${f.x}" y="${f.y}" width="26" height="${f.h}" rx="13" ${i===4?'transform="rotate(-30 148 135)"':''}><title>${fingerNames[ids[i]]}</title></rect>`;});
    svg+='<path class="palm" d="M9 129 Q70 116 128 129 L147 146 Q156 166 137 183 Q123 193 118 204 H48 Q41 194 25 180 Q9 165 9 146Z"/><path class="palm-line" d="M30 156 Q74 142 113 158 M40 172 Q70 166 96 176"/>';
    svg+=`<text x="69" y="201" class="hand-label" ${right?'transform="translate(138 0) scale(-1 1)"':''}>${right?'오른손':'왼손'}</text></g>`;
  }
  $('hands').innerHTML=svg+'</svg>';
  for(const el of $('hands').querySelectorAll('[data-finger]')) fingers.set(Number(el.dataset.finger),el);
}
function updateGuide(state) {
  const guide=keyboardGuide(controller.problem.code[state.cursor],state.activeErrorIndex!==null);
  $('expected').textContent=state.activeErrorIndex!==null?'Backspace ⌫':visible(controller.problem.code[state.cursor]??'');
  $('finger-description').textContent=guide.description;
  for(const [id,el] of keys) {el.toggleAttribute('data-active',id===guide.target?.id);el.toggleAttribute('data-shift',id===guide.shift?.id);}
  for(const [id,el] of fingers) {el.toggleAttribute('data-active',id===guide.target?.finger);el.toggleAttribute('data-shift',id===guide.shift?.finger);}
}
function metrics() {
  const m=controller.metrics();
  $('metric-time').textContent=formatDuration(m.elapsed_ms);
  $('metric-wpm').textContent=m.wpm;
  $('metric-accuracy').textContent=m.accuracy_pct===null?'—':`${m.accuracy_pct.toFixed(1)}%`;
  $('metric-progress').textContent=`${Math.floor(m.progress_pct)}%`;
  $('progress').setAttribute('aria-valuenow',Math.floor(m.progress_pct));
  $('progress').firstElementChild.style.width=`${m.progress_pct}%`;
}
function renderCode(code) {
  const classes=pythonCharacterClasses(code);
  $('code').replaceChildren();spans=[];let offset=0;
  code.split('\n').forEach((line,n,lines)=>{
    const el=document.createElement('span');el.className='code-line';
    const number=document.createElement('span');number.className='line-number';number.textContent=n+1;number.setAttribute('aria-hidden','true');el.append(number);
    const chars=document.createElement('span');
    for(const char of line+(n<lines.length-1?'\n':'')) {
      const span=document.createElement('span');span.className='character '+classes[offset];span.dataset.char=char;
      span.textContent=char==='\n'?'↵':char===' '&&whitespace?'·':char;
      if(char==='\n') span.classList.add('newline');
      spans.push(span);chars.append(span);offset++;
    } el.append(chars);$('code').append(el);
  });
}
function paint() {
  const state=controller.getSnapshot();
  $('minimum-accuracy').disabled=!!channel||state.status==='running'||locked;
  for(let i=0;i<spans.length;i++) {
    const status=state.slots[i]?.status;
    if(status!==spans[i].dataset.typedStatus) {if(status)spans[i].dataset.typedStatus=status;else delete spans[i].dataset.typedStatus;}
    spans[i].toggleAttribute('data-current',i===state.cursor&&state.status!=='completed');
  }
  const line=controller.problem.code.slice(0,state.cursor).split('\n').length;
  const column=state.cursor-controller.problem.code.lastIndexOf('\n',state.cursor-1);
  $('position').textContent=`Ln ${line}, Col ${column}`;
  $('feedback').textContent=state.activeErrorIndex!==null?'Backspace로 오류 위치까지 돌아가 주세요.':state.status==='idle'?'첫 글자를 입력하면 시작해요.':`다음 문자 ${visible(controller.problem.code[state.cursor]??'')} · 실수 ${state.mistakeCount}회`;
  metrics();updateGuide(state);
  if(state.status==='running'&&!clock) clock=setInterval(metrics,100);
  if(state.status==='completed') {clearInterval(clock);clock=null;showResult(state);}
  if(previousCursor!==state.cursor) {
    previousCursor=state.cursor;
    const caret=spans[state.cursor],box=$('reference-code');
    if(caret) requestAnimationFrame(()=>{
      const rect=caret.getBoundingClientRect(),view=box.getBoundingClientRect();
      if(rect.bottom>view.bottom)box.scrollTop+=rect.bottom-view.bottom;
      if(rect.top<view.top)box.scrollTop-=view.top-rect.top;
      if(rect.right>view.right)box.scrollLeft+=rect.right-view.right;
      if(rect.left<view.left)box.scrollLeft-=view.left-rect.left;
    });
  }
}
function showResult(state) {
  const m=controller.metrics(),success=m.accuracy_pct!==null&&m.accuracy_pct>=minimumAccuracy;
  $('editor-panel').hidden=true;$('result').hidden=false;
  $('result-title').textContent=success?'멋진 연습이었어요!':'한 번 더, 더 정확하게.';
  $('result-description').textContent=`${formatDuration(m.elapsed_ms)} · ${m.wpm} WPM · 정확도 ${m.accuracy_pct?.toFixed(1)}% · ${success?'성공':'실패'}`;
  $('result-errors').textContent=`미수정 오류 ${remainingErrors(state)}개 · 총 실수 ${state.mistakeCount}회`;
  $('feedback').textContent=channel?'최종 결과를 캠퍼스에 전달했어요.':'완료했어요. 다른 문제도 연습해 보세요.';
  $('guide-title').textContent='어떤 키가 어려웠나요?';$('hands').hidden=true;$('guide-body').querySelector('.instruction').hidden=true;
  for(const el of keys.values()){el.removeAttribute('data-active');el.removeAttribute('data-shift');}
  const mistakes=aggregateKeyMistakes(state.mistakeByExpectedChar);
  for(const [id,entry] of mistakes) {
    const el=keys.get(id);el.dataset.heat=Math.min(5,entry.count);el.tabIndex=0;
    el.title=`${entry.key.label}: ${entry.count}회`;el.setAttribute('aria-label',el.title);
    const badge=document.createElement('b');badge.textContent=entry.count;el.append(badge);
  }
  $('mistake-rank').hidden=false;
  $('mistake-rank').textContent=[...mistakes.values()].sort((a,b)=>b.count-a.count).slice(0,5).map(e=>`${e.key.label} ${e.count}회`).join(' · ')||'모든 키를 정확하게 입력했어요!';
  if(channel){locked=true;channel.complete({duration_ms:Math.round(m.elapsed_ms),status:success?'SUCCESS':'FAILED'});for(const id of ['mode','minimum-accuracy','restart'])$(id).disabled=true;}
}
function start(problem) {
  if(locked)return;
  controller?.abandon('restart');detach?.();clearInterval(clock);clock=null;
  controller=new AttemptController(problem,$('mode').value,{emit(){}},{},sessionId);
  $('input').value='';$('editor-panel').hidden=false;$('result').hidden=true;
  $('guide-title').textContent='키보드 & 손가락 가이드';$('hands').hidden=false;$('guide-body').querySelector('.instruction').hidden=false;$('mistake-rank').hidden=true;
  for(const el of keys.values()){delete el.dataset.heat;el.querySelector('b')?.remove();el.removeAttribute('tabindex');}
  $('problem-title').textContent=problem.title;$('problem-description').textContent=problem.description;
  const group=all.filter(p=>p.difficulty==='beginner'),index=group.findIndex(p=>p.id===problem.id)+1;
  $('lesson-number').textContent=String(index).padStart(2,'0');$('lesson-position').textContent=`${index} / ${group.length} 문제`;
  $('char-count').textContent=`${problem.code.length} characters · Python · Spaces: 4`;
  $('mode-help').textContent=$('mode').value==='natural'?'Natural · 오타가 있어도 계속 입력할 수 있어요. Backspace로 수정해 보세요.':'Forced Correction · 오타 뒤 최대 5칸까지 진행해요. Backspace로 오류 위치까지 돌아가 수정하세요.';
  renderCode(problem.code);previousCursor=-1;
  controller.subscribe(paint);
  detach=attachTypingInput($('input'),action=>controller.input(action),()=>controller.getSnapshot().slots.map(s=>s.typed).join(''),()=>$('restart').focus());
  paint();
  $('input').focus({preventScroll:true});
}
createKeyboard();
$('mode').addEventListener('change',()=>start(controller.problem));
$('minimum-accuracy').addEventListener('change',()=>{
  if(!$('minimum-accuracy').checkValidity()){$('minimum-accuracy').value=minimumAccuracy;return;}
  minimumAccuracy=Number($('minimum-accuracy').value);
  $('pass-rule').textContent=`정확도 ${minimumAccuracy}% 이상으로 끝까지 입력하면 성공해요. 코드는 실행하지 않습니다.`;
  start(controller.problem);
});
for(const id of ['restart','repeat'])$(id).addEventListener('click',()=>{start(controller.problem);$('input').focus();});
$('next').addEventListener('click',()=>{start(pickBeginnerProblem(all));$('input').focus();});
$('exit').addEventListener('click',()=>{controller?.abandon('exit');clearInterval(clock);clock=null;if(channel)channel.exit();else{start(controller.problem);$('feedback').textContent='좋은 연습이었어요. 준비되면 다시 시작해요.';}});
$('typing-surface').addEventListener('click',()=>$('input').focus({preventScroll:true}));
$('whitespace').addEventListener('click',()=>{whitespace=!whitespace;$('whitespace').setAttribute('aria-pressed',whitespace);for(const span of spans)if(span.dataset.char===' ')span.textContent=whitespace?'·':' ';});
$('toggle-guide').addEventListener('click',()=>{const hidden=!$('guide-body').hidden;$('guide-body').hidden=hidden;$('toggle-guide').setAttribute('aria-expanded',!hidden);$('toggle-guide').textContent=hidden?'가이드 보기 +':'가이드 숨기기 −';});
window.addEventListener('pagehide',()=>{controller?.abandon('pagehide');clearInterval(clock);detach?.();});
if(channel){
  $('input').disabled=true;$('repeat-actions').hidden=true;
  channel.start((config,id)=>{
    if(!Number.isFinite(config.minimumAccuracy)||config.minimumAccuracy<0||config.minimumAccuracy>100)throw new Error('정확도 설정 오류');
    minimumAccuracy=config.minimumAccuracy;$('minimum-accuracy').value=minimumAccuracy;sessionId=id;
    $('pass-rule').textContent=`정확도 ${minimumAccuracy}% 이상으로 끝까지 입력하면 성공해요. 코드는 실행하지 않습니다.`;
    $('input').disabled=false;start(pickBeginnerProblem(all));
  });
}else start(pickBeginnerProblem(all));
