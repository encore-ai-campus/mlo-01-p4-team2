/* RPG narrative renderer. Advancing text never calls a service or changes progress. */
(()=>{'use strict';
const sessions=new WeakMap();
function clear(dialog){sessions.get(dialog)?.();sessions.delete(dialog);dialog.classList.remove('mq-story');}
function show(dialog,{title,chapter='MAIN QUEST',setting='campus',lines,actions=[]}){
clear(dialog);dialog.classList.add('mq-story');dialog.querySelector('h2').textContent=title;
const body=dialog.querySelector('.mq-body');body.replaceChildren();
const scene=document.createElement('section');scene.className='mq-story-scene';scene.dataset.setting=setting;
scene.innerHTML='<div class="mq-story-set" aria-hidden="true"><span class="mq-story-moon"></span><span class="mq-story-window"></span><span class="mq-story-window second"></span><span class="mq-story-desk"></span><span class="mq-story-gift">11.11</span></div><div class="mq-story-chapter"></div><div class="mq-story-location"></div><div class="mq-story-portrait"><img alt=""></div><div class="mq-story-panel"><div class="mq-story-byline"><strong></strong><span></span></div><div class="mq-evidence-layout"><div class="mq-question-column"><p class="mq-story-text" aria-live="polite"></p><pre class="mq-story-evidence" hidden></pre></div><aside class="mq-table-column" hidden aria-label="SQL 문제의 원본 데이터 표"><table class="mq-evidence-table"></table></aside></div><div class="mq-story-footer"><span class="mq-story-counter"></span><span>Enter / Space · 다음 대사</span></div><div class="mq-story-controls"><button type="button" class="mq-story-prev">이전</button><div class="mq-story-choices"></div><button type="button" class="mq-story-next">다음 대사 ▸</button></div></div>';
body.append(scene);let index=0,timer=0,full='',position=0,disposed=false;
const q=s=>scene.querySelector(s),text=q('.mq-story-text'),next=q('.mq-story-next'),prev=q('.mq-story-prev'),choices=q('.mq-story-choices');
q('.mq-story-chapter').textContent=chapter;q('.mq-story-location').textContent={campus:'18F · PLAYDATA CAMPUS',rooftop:'옥상 · 숨겨진 흔적',analysis:'ROOM 01 · EVIDENCE LAB',workshop:'ROOM 02 · CHOCOLATE ATELIER'}[setting]||setting;
function reveal(){clearInterval(timer);timer=0;position=full.length;text.textContent=full;next.textContent=index===lines.length-1?'대화 마치기':'다음 대사 ▸';if(index===lines.length-1&&actions.length){const hadFocus=document.activeElement===next;next.hidden=true;choices.hidden=false;if(hadFocus)choices.querySelector("button")?.focus();}}
function paint(){clearInterval(timer);const line=lines[index];full=line.text;position=0;text.textContent='';choices.hidden=true;next.hidden=false;prev.disabled=index===0;next.textContent='대사 펼치기 ▸';q('.mq-story-counter').textContent=String(index+1).padStart(2,'0')+' / '+String(lines.length).padStart(2,'0');q('.mq-story-byline strong').textContent=line.speaker||'탐정 꾸왕이';q('.mq-story-byline span').textContent=line.role||'사라진 11번째 빼빼로';
const portrait=q('.mq-story-portrait'),img=portrait.querySelector('img');portrait.classList.toggle('is-item',line.person==='item');let src=line.art||'assets/guide-kkuwang-detective-v1.png';if(line.person==='manager')src='assets/characters/manager-yeongseok-idle-v1.png';if(line.person==='player')src='assets/characters/'+(['male-1','male-2','female-1','female-2'].includes(window.CampusApp?.player.avatar)?window.CampusApp.player.avatar:'male-1')+'-idle-v1.png';portrait.classList.toggle('is-sheet',line.person==='manager'||line.person==='player');img.src=src;img.alt=line.speaker||'탐정 꾸왕이';scene.dataset.emotion=line.emotion||'normal';const evidence=q('.mq-story-evidence');evidence.hidden=!line.evidence;evidence.textContent=line.evidence||'';
const tableData=line.evidenceTable,tableSide=q('.mq-table-column'),table=q('.mq-evidence-table');
scene.classList.toggle('has-evidence-table',Boolean(tableData));tableSide.hidden=!tableData;table.replaceChildren();
if(tableData){
 const caption=document.createElement('caption');caption.textContent=tableData.name;
 const head=document.createElement('thead'),headRow=document.createElement('tr');
 for(const label of tableData.columns){const cell=document.createElement('th');cell.scope='col';cell.textContent=label;headRow.append(cell);}head.append(headRow);
 const body=document.createElement('tbody');for(const values of tableData.rows){const row=document.createElement('tr');values.forEach((value,index)=>{const cell=document.createElement(index===0?'th':'td');if(index===0)cell.scope='row';cell.textContent=String(value);row.append(cell);});body.append(row);}table.append(caption,head,body);
}
if(matchMedia('(prefers-reduced-motion: reduce)').matches)reveal();else timer=setInterval(()=>{if(disposed)return;position=Math.min(full.length,position+2);text.textContent=full.slice(0,position);if(position===full.length)reveal();},24);
}
function advance(){if(position<full.length){reveal();return;}if(index<lines.length-1){index++;paint();}else if(actions.length){choices.querySelector('button')?.focus();}else dialog.close();}
for(const [label,fn] of actions){const button=document.createElement('button');button.type='button';button.textContent=label;button.onclick=fn;choices.append(button);}
next.onclick=advance;prev.onclick=()=>{if(index>0){index--;paint();}};
function key(e){if(!['Enter',' '].includes(e.key))return;if(e.repeat){e.preventDefault();return;}if(e.target.closest('button'))return;e.preventDefault();advance();}
const cleanup=()=>{disposed=true;clearInterval(timer);dialog.removeEventListener('keydown',key);dialog.removeEventListener('close',closed);};
const closed=()=>clear(dialog);dialog.addEventListener('keydown',key);dialog.addEventListener('close',closed);sessions.set(dialog,cleanup);paint();if(!dialog.open)dialog.showModal();next.focus();
}
window.CampusQuestDialogue={show,clear};
})();
