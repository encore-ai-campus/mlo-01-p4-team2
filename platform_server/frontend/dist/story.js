/* Account-local mission UI. Only server-confirmed progress advances the guide. */
(() => {
  'use strict';
  if (window.CAMPUS_SEASON !== 'pepero') return;
  const viewport = document.getElementById('viewport');
  if (!viewport) return;
  let state = null, busy = false, pendingAuto = false, lastRefresh = 0;
  const dialog = document.createElement('dialog');
  dialog.id = 'storyDialog'; dialog.className = 'story-dialog';
  dialog.setAttribute('aria-labelledby', 'storyTitle');
  dialog.innerHTML = '<header><span class="story-eyebrow">11.11 · CAMPUS MYSTERY</span><button type="button" class="story-close" aria-label="미션 창 닫기">×</button></header><div class="story-body"><h2 id="storyTitle" tabindex="-1"></h2><div id="storyCopy"></div><div id="storyChoices"></div><p id="storyError" role="alert" hidden></p></div><footer id="storyActions"></footer>';
  document.body.append(dialog);
  const $ = id => document.getElementById(id);
  const tracker = document.createElement('section'); tracker.id = 'storyTracker'; tracker.className = 'story-tracker';
  tracker.setAttribute('aria-label', '메인 미션 진행도');
  tracker.innerHTML = '<header><div><small>MAIN QUEST</small><strong>사라진 11번째 빼빼로</strong></div><button type="button" aria-label="미션 진행도 접기" aria-expanded="true">−</button></header><div class="story-tracker-details"><ol></ol><p class="story-current" aria-live="polite"></p><button class="story-resume" type="button">미션 안내</button></div>';
  viewport.append(tracker);
  const arrow = document.createElement('div'); arrow.className = 'story-arrow'; arrow.hidden = true;
  arrow.innerHTML = '<span class="story-arrow-symbol" aria-hidden="true">➤</span><span class="story-arrow-label"></span>'; viewport.append(arrow);
  const classHint = document.createElement('div'); classHint.className = 'story-class-hint'; classHint.hidden = true;
  document.getElementById('classroomDialog')?.append(classHint);
  const classProgress=document.createElement('div');classProgress.className='story-class-tracker';classProgress.hidden=true;
  document.getElementById('classroomDialog')?.append(classProgress);
  tracker.querySelector('header button').onclick = event => {
    const folded = tracker.classList.toggle('collapsed'); event.currentTarget.textContent = folded ? '+' : '−';
    event.currentTarget.setAttribute('aria-expanded', String(!folded));
  };
  tracker.querySelector('.story-resume').onclick = () => run(async () => { await refresh(); resume(); });
  dialog.querySelector('.story-close').onclick = close;
  dialog.addEventListener('cancel', event => {event.preventDefault(); close();});
  dialog.addEventListener('keydown', event => event.stopPropagation());
  dialog.addEventListener('close', () => {
    const app = window.CampusApp;
    app?.presence.setInput([]);
    (app?.classroom.blocking ? document.getElementById('classroomCanvas') : document.getElementById('mapCanvas'))?.focus({preventScroll:true});
  });
  function close() { if (!busy) dialog.close(); }
  function button(text, fn, secondary=false) {
    const b=document.createElement('button'); b.type='button'; b.textContent=text;
    if(secondary)b.className='secondary'; b.onclick=()=>run(fn); return b;
  }
  function modal(title, paragraphs, actions, {choices=null, table=false, portrait=false}={}) {
    window.dispatchEvent(new Event('campus:guide-open'));
    $('storyTitle').textContent=title; $('storyCopy').replaceChildren(); $('storyChoices').replaceChildren();
    if(portrait){const img=document.createElement('img');img.src='assets/guide-kkuwang-detective-v1.png';img.alt='탐정 꾸왕이';img.className='story-portrait';$('storyCopy').append(img);}
    for(const text of paragraphs){const p=document.createElement('p');p.textContent=text;$('storyCopy').append(p);}
    if(table){const t=document.createElement('table');t.innerHTML='<caption>선물 수량 확인표</caption><thead><tr><th>표</th><th>참가자</th><th>선물</th><th>여분</th></tr></thead><tbody><tr><th>A</th><td>11명</td><td>11개</td><td>0개</td></tr><tr><th>B</th><td>10명</td><td>10개</td><td>0개</td></tr><tr><th>C</th><td>10명</td><td>11개</td><td>1개</td></tr></tbody>';$('storyCopy').append(t);}
    if(choices)choices.forEach((text,index)=>{const label=document.createElement('label');const input=document.createElement('input');input.type='radio';input.name='storyChoice';input.value=String(index+1);label.append(input,document.createTextNode(text));$('storyChoices').append(label);});
    $('storyActions').replaceChildren(...actions.map(a=>button(...a)));$('storyError').hidden=true;
    if(!dialog.open)dialog.showModal(); $('storyTitle').focus({preventScroll:true});
  }
  async function api(path, data) {
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),15000);
    const token=document.cookie.split('; ').find(c=>c.startsWith('campus_csrftoken='))?.split('=').slice(1).join('=')||'';
    try{
      const response=await fetch('/api/event-story/'+path,{method:data?'POST':'GET',credentials:'same-origin',cache:'no-store',signal:controller.signal,
        headers:data?{'Content-Type':'application/json','X-CSRFToken':decodeURIComponent(token)}:{},body:data?JSON.stringify(data):undefined});
      if(response.redirected)throw new Error('로그인이 만료됐어요. 다시 로그인해 주세요.');
      const result=await response.json();if(!response.ok){const error=new Error(result.message||'기록을 저장하지 못했어요. 다시 시도해 주세요.');error.code=result.error;throw error;}return result;
    }catch(error){if(error.name==='AbortError')throw new Error('저장 결과를 확인하지 못했어요. 같은 버튼을 눌러 다시 시도해 주세요.');throw error;}finally{clearTimeout(timer);}
  }
  async function run(fn) {
    if(busy)return;busy=true;
    dialog.querySelectorAll('button').forEach(b=>b.disabled=true);
    try{await fn();}catch(error){
      if(dialog.open){$('storyError').textContent=error.message||'기록을 저장하지 못했어요. 다시 시도해 주세요.';$('storyError').hidden=false;}
      else window.CampusApp?.toast(error.message||'미션 정보를 불러오지 못했어요.');
    }finally{busy=false;dialog.querySelectorAll('button').forEach(b=>b.disabled=false);}
  }
  function setState(next) {state=next;paintTracker();}
  async function refresh(){setState(await api('status/'));lastRefresh=Date.now();return state;}
  async function act(action, extra={}) {const result=await api('action/',{action,...extra});setState(result.state);return result;}
  async function ticket(target){return (await window.CampusApp.presence.command('story_access',{target})).ticket;}
  function guidance(){
    if(!state)return '미션 기록을 불러오는 중…';
    if(state.window.phase!=='open')return state.window.phase==='before'?'11월 11일에 시작해요':'이벤트가 종료됐어요';
    if(!state.started)return '이벤트를 시작하고 첫 단서를 찾아보세요.';
    if(state.pending==='memo')return '꾸왕이가 남긴 메모를 확인하세요.';
    if(state.pending==='apology')return '꾸왕이의 이야기를 들어보세요.';
    if(state.pending==='ending')return '꾸왕이와 함께 선물을 채워놓으세요.';
    return ['','18층 게시판에서 조사 시작','라운지 2 · 빈 포장지와 수량표 확인','Python 또는 SQL 초급 한 세트 통과','라운지 2 · 행사 기록에서 수량표 추리','옥상 심리테스트 부스 · 해결 방법 선택','사건 해결 완료! 공간을 자유롭게 탐색하세요.'][state.stage];
  }
  function paintTracker(){
    tracker.querySelector('ol').replaceChildren(...(state?.steps||[]).map((step,index)=>{
      const li=document.createElement('li');li.className=step.complete?'done':state.started&&state.stage===index+1?'current':'';
      const mark=document.createElement('span');mark.textContent=step.complete?'✓':String(index+1);
      const counter=index===1&&!step.complete?` (${Number(!!state.flags.wrapper)+Number(!!state.flags.count)}/2)`:'';
      li.append(mark,document.createTextNode(step.name+counter));
      if(step.complete)li.setAttribute('aria-label',step.name+' 완료');return li;
    }));
    tracker.querySelector('.story-current').textContent=guidance();
    tracker.querySelector('.story-resume').textContent=!state?.started?'이벤트 시작 안내':state.pending?'대화 이어보기':state.completed?'해결한 사건 보기':'현재 미션 안내';
    tracker.dataset.complete=String(Boolean(state?.completed));
    tracker.querySelector('small').textContent=state?.window.preview?'MAIN QUEST · 미리보기':'MAIN QUEST';
    classProgress.replaceChildren(...(state?.steps||[]).map((step,index)=>{const line=document.createElement('div');line.textContent=(step.complete?'✓ ':`${index+1}. `)+step.name;line.style.opacity=step.complete||state.stage===index+1?'1':'.45';return line;}));
  }
  function intro(){
    if(state.window.phase!=='open'){modal(state.window.phase==='before'?'아직 이벤트가 시작되지 않았어요':'이벤트가 종료됐어요',[guidance(),'운영 시간: 11월 11일 00:00 ~ 24:00 (한국시간)'],[['확인',()=>dialog.close()]]);return;}
    modal('메타버스 특별 이벤트',['사라진 11번째 빼빼로의 행방을 밝혀주세요. 게시판과 라운지를 조사하고, Python 또는 SQL 초급에 도전해 꾸왕이가 남긴 메모를 찾아보세요.','11월 11일 하루 종일 참여할 수 있어요. 미션 진행은 계정에 저장되어 다시 접속해도 이어갈 수 있어요.'],[['이벤트 시작하기',async()=>{await act('start');dialog.close();window.CampusApp?.toast('게시판에 첫 번째 목표를 표시했어요.');}]]);
    api('action/',{action:'popup_view'}).catch(()=>{});
  }
  function resume(){
    if(!state.started)return intro();
    if(state.pending)return pendingDialog();
    if(state.completed)return ending(true);
    modal('현재 미션',[guidance(),'화살표는 내 화면에서만 표시돼요. 대상 가까이에서 설정한 상호작용 키를 누르세요.'],[['확인',()=>dialog.close()]]);
  }
  function pendingDialog(){
    if(state.pending==='memo')modal('꾸왕이가 남긴 메모',['배가 고파서 선물을 하나 먹었어. 확인표에는 여분이 하나 있다고 적혀 있었는데… 내가 본 게 마지막 확인표가 아니었나?'],[['확인',async()=>{await act('memo');modal('수량표를 비교해 보세요',['라운지 2의 행사 기록에서 확인표 A·B·C와 담당자 메모를 비교하세요.'],[['확인',()=>dialog.close()]]);}]],{portrait:true});
    else if(state.pending==='apology')modal('꾸왕이의 이야기',['내가 예전 확인표만 보고 여유가 있는 줄 알았어. 배가 고파서 하나 먹고, 포장지도 치우는 걸 깜빡했어. 미안해.','담당자에게 말씀드리고, 내가 새 선물 하나를 준비할게!'],[['함께 설명하러 가자',async()=>{await act('apology');ending(false);}]],{portrait:true});
    else if(state.pending==='ending')ending(false);
  }
  function ending(done){modal('선물 준비 완료!',['꾸왕이는 담당자에게 사실대로 설명하고 새 선물 하나를 채웠어요.','빈 포장지도 정리해서, 참가자 11명에게 줄 선물 11개가 다시 준비됐어요.'],[[done?'확인':'이제 다른 이스터에그도 찾아볼까?',async()=>{if(!done)await act('finish');dialog.close();}]],{portrait:true});}
  async function openTarget(target){
    await refresh();
    if(!state.started)return intro();
    if(state.completed&&['wrapper','count','guide'].includes(target))return ending(true);
    if(state.pending&&target==='guide')return pendingDialog();
    if(target==='guide')return resume();
    if(target==='board'){
      modal('사라진 11번째 빼빼로',['행사 선물 하나가 개봉됐어요. 18층 라운지 2의 탁자에서 빈 포장지와 선물 수량 확인표를 조사해 주세요.'],[
        [state.flags.board?'조사 안내':'조사 시작',async()=>{await act('board',{ticket:await ticket('board')});modal('라운지에 남은 단서를 찾아보세요',['18층 라운지 2의 탁자에서 빈 포장지와 선물 수량 확인표를 각각 조사하세요.','물건 가까이에서 상호작용 키를 누르고 팝업의 [확인]을 선택하면 됩니다.'],[['확인',()=>dialog.close()]]);}],
        ['일반 게시판 보기',()=>{dialog.close();window.CampusApp.openRegularBoard();},true]
      ]);return;
    }
    const needed=target==='ethics'?state.flags.quantity:state.flags.board;
    if(!needed){modal('현재 미션을 먼저 완료해 주세요',['아직 확인하지 않은 단서가 있어요.',guidance()], [['확인',()=>dialog.close()]]);return;}
    if(target==='count'&&state.flags.memo&&!state.flags.quantity)return quiz('quantity');
    if(target==='ethics')return quiz('ethics');
    const token=await ticket(target);
    const copy=target==='wrapper'?['탁자에 남겨진 빈 포장지','선물 하나의 포장지가 뜯겨 있습니다. 안에 있던 빼빼로는 없고, 빈 포장지만 탁자에 남아 있습니다.','누군가 여기서 먹고 포장지를 치우지 않은 걸까요?']:['포장 완료 시점의 선물 수량 확인표','참가자 11명 / 준비한 선물 11개 / 여분 0개','참가자에게 하나씩 나눠줄 선물입니다. 포장이 끝났을 때 선물은 정확히 11개였고, 여분은 없었습니다.'];
    const wasComplete=state.flags.wrapper&&state.flags.count;
    modal(copy[0],copy.slice(1),[['확인',async()=>{await act(target,{ticket:token});
      if(!wasComplete&&state.flags.wrapper&&state.flags.count)modal('단서 2개를 모두 확인했어요!',['포장 완료 당시 선물은 11개였고 여분은 없었습니다. 그런데 빈 포장지가 남아 있어요.','꾸왕이가 선물 준비를 도와주다가 학습교실로 갔어요. Python이나 SQL 초급에 도전한 뒤, 꾸왕이가 남긴 메모도 확인해 보세요.'],[['확인',()=>dialog.close()]]);
      else dialog.close();}]]);
  }
  async function quiz(kind){
    const token=await ticket(kind),quantity=kind==='quantity';
    const title=quantity?'수량표 변경 순서를 추리해 보세요':'실수를 해결할 방법은 무엇일까요?';
    const paragraphs=quantity?['담당자 메모: “선물을 하나 더 준비한 뒤 참가 신청 한 명을 추가 반영했습니다. 취소된 참가자는 없습니다.”','작성된 순서대로 확인표를 선택하세요.']:['여유분인 줄 알고 행사 선물을 하나 먹었는데, 참가자에게 줄 선물이 부족해졌어요. 어떻게 해결하면 좋을까요?'];
    modal(title,paragraphs,[['제출',async()=>{
      const selected=dialog.querySelector('input[name="storyChoice"]:checked');if(!selected)throw new Error('선택지 하나를 선택하세요.');
      const result=await act(kind,{ticket:token,answer:Number(selected.value)});
      if(!result.correct){modal(quantity?'순서를 다시 확인해 보세요':'다시 생각해 보세요',[quantity?'담당자 메모에서 무엇을 먼저 추가했는지 다시 확인해 보세요.':'모른 척하거나 남에게 책임을 돌리면 선물은 여전히 부족해요. 실수를 해결할 수 있는 방법을 다시 선택해 보세요.'],[['다시 풀기',()=>quiz(kind)]]);return;}
      modal(quantity?'수량표의 작성 순서를 알아냈어요!':'실수를 해결할 방법을 찾았어요!',quantity?['처음에는 참가자 10명, 선물 10개였습니다. 선물 하나를 추가했지만 참가자도 한 명 늘어나 여분은 없어졌어요.','꾸왕이는 중간 기록 C를 보고 여분이 있다고 착각했군요! 옥상 심리테스트 부스에서 해결 방법을 선택해 보세요.']:['실수는 사실대로 말하고 부족한 선물을 채워놓으면 해결할 수 있어요. 꾸왕이에게 이 방법을 알려주고 이야기를 들어보세요.'],[['확인',()=>quantity?dialog.close():pendingDialog()]]);
    }]],{table:quantity,choices:quantity?['① A → C → B','② B → C → A','③ C → B → A']:['① 모른 척하고 돌아간다','② 사실대로 말하고 채워놓기','③ 다른 사람에게 책임을 돌린다']});
  }
  function target(){
    if(!state?.started||state.completed||state.pending||state.window.phase!=='open')return null;
    const app=window.CampusApp,player=app?.player;if(!player)return null;
    let options=state.stage===1?[['campus',1250,185,'18층 게시판']]:state.stage===2?[
      ...(!state.flags.wrapper?[['campus',590,445,'빈 포장지 조사']]:[]),...(!state.flags.count?[['campus',700,445,'수량 확인표 읽기']]:[])]:state.stage===3?[['campus',window.CampusWorlds.campus.rooms.find(r=>r.id==='class1').target[0],555,'Python 초급 학습'],['campus',window.CampusWorlds.campus.rooms.find(r=>r.id==='class2').target[0],555,'SQL 초급 학습']]:state.stage===4?[['campus',700,445,'행사 기록 · 수량표 추리']]:[['rooftop',692,897,'심리테스트 부스']];
    let goal=options.filter(o=>o[0]===app.floor).sort((a,b)=>Math.hypot(a[1]-player.x,a[2]-player.y)-Math.hypot(b[1]-player.x,b[2]-player.y))[0]||options[0];
    if(goal[0]!==app.floor){
      const paths=app.data.objects.filter(o=>o.toFloor===goal[0]);
      const near=paths.sort((a,b)=>Math.hypot(a.x-player.x,a.y-player.y)-Math.hypot(b.x-player.x,b.y-player.y))[0];
      if(!near)return null;const point=near.approach||near.interactionPoint||[near.x+near.w/2,near.y+near.h/2];
      goal=[app.floor,point[0],point[1],goal[0]==='rooftop'?'옥상으로 이동':'18층으로 이동'];
    }
    return goal;
  }
  function update({project,width,height,blocked}){
    if(state?.window.phase==='open'&&!state.window.preview&&Date.now()>=Date.parse(state.window.end)){state.window.phase='closed';paintTracker();}
    const goal=target(),app=window.CampusApp;
    tracker.hidden=!!window.CampusGuide?.blocking;
    arrow.hidden=!goal||blocked||!!app?.classroom.blocking;
    if(arrow.hidden)return;
    const p=project(goal[1],goal[2]),margin=60,bottom=height-130;
    const on=p.x>margin&&p.x<width-margin&&p.y>105&&p.y<bottom;
    const x=Math.max(margin,Math.min(width-margin,p.x)),y=Math.max(110,Math.min(bottom,p.y-70));
    arrow.style.left=x+'px';arrow.style.top=y+'px';arrow.classList.toggle('on-target',on);
    const angle=on?90:Math.atan2(p.y-y,p.x-x)*180/Math.PI;
    arrow.querySelector('.story-arrow-symbol').style.transform=`rotate(${angle}deg)`;
    arrow.querySelector('.story-arrow-label').textContent=goal[3];
  }
  function updateClassroom({actor,project,width,height}){
    const active=state?.started&&!state.completed&&state.window.phase==='open'&&!dialog.open&&!window.CampusLearning?.blocking;
    classHint.hidden=!active;classProgress.hidden=!active;if(!active)return;
    classHint.textContent=state.stage<=3?'◆ STEP 03 · 학습 영역에서 초급 한 세트를 통과하세요.':'▼ 메모 확인 후 교실 출구로 나가 다음 미션을 진행하세요.';
    classHint.style.bottom='75px';
  }
  window.CampusStory={get blocking(){return dialog.open;},get state(){return state;},get restored(){return !!state?.completed;},update,updateClassroom,
    handles(item){return item.id==='class3-4-noticeboard'||item.action==='story'||(item.platform==='psych'&&state?.started&&!state.completed&&state.stage>=5);},
    open(item){const t=item.id==='class3-4-noticeboard'?'board':item.platform==='psych'?'ethics':item.id==='story-wrapper'?'wrapper':'count';run(()=>openTarget(t));},
    guide(){run(async()=>{await refresh();if(state.started)resume();else intro();});},
    paint(o,helpers,base){
      if(o.type!=='story-evidence')return false;
      const {g,p,box,plane,line}=helpers,{x,y,w,h}=o;
      for(const [dx,dy] of [[5,5],[w-10,5],[5,h-10],[w-10,h-10]])box(x+dx,y+dy,5,5,base,27,'#d5ae70','#937049','#684a33');
      box(x,y,w,h,base+27,5,'#fff0d2','#bc9569','#94694c');plane(x+3,y+3,w-6,h-6,base+32.1,'#96546d','#e9bd8b');
      const count=state?.completed?11:10;
      for(let i=0;i<count;i++){const gx=x+13+(i%6)*19,gy=y+8+Math.floor(i/6)*19;
        box(gx,gy,13,12,base+33,5,i%2?'#f5b8c8':'#77443e','#b57577','#58302f');
        plane(gx+5,gy,3,12,base+38.2,'#f9d18c');plane(gx,gy+5,13,2,base+38.3,'#f9d18c');}
      const q=p(x+14,y+h-12,base+34);if(!state?.completed){g.save();g.translate(q.x,q.y);g.rotate(-.25);g.fillStyle='#fce3bd';g.fillRect(-7,-5,18,9);g.fillStyle='#a8506c';g.fillRect(-3,-5,9,9);g.restore();}
      plane(x+w-25,y+h-20,20,14,base+33,'#fff9e8','#c99d61');
      for(let i=0;i<3;i++)line(p(x+w-22,y+h-17+i*3,base+33.3),p(x+w-9,y+h-17+i*3,base+33.3),'#745351',.7);
      return true;
    }
  };
  window.addEventListener('campus:learning-result',()=>{
    run(async()=>{await refresh();if(state.pending==='memo'){
      pendingAuto=true;
      const resultScreen=document.getElementById('result-screen');
      let next=document.getElementById('storyLearningNext');if(!next){next=button('확인 · 꾸왕이 메모 보기',()=>{document.getElementById('learningDialog').close();pendingAuto=false;pendingDialog();});next.id='storyLearningNext';resultScreen?.prepend(next);}
      next.hidden=false;
    }else{const next=document.getElementById('storyLearningNext');if(next)next.hidden=true;pendingAuto=false;}});
  });
  document.getElementById('learningDialog')?.addEventListener('close',()=>{if(pendingAuto){pendingAuto=false;pendingDialog();}});
  async function initialize(){await refresh();if(window.CampusGuide?.blocking||window.CampusGuide?.pending)return;
    if(!state.started)intro();else if(state.pending)pendingDialog();}
  window.addEventListener('campus:ready',()=>run(initialize),{once:true});
  window.addEventListener('campus:guide-close',()=>{if(!state?.started&&!dialog.open)run(initialize);});
  window.addEventListener('focus',()=>{if(!busy&&Date.now()-lastRefresh>3000)run(refresh);});
  document.addEventListener('visibilitychange',()=>{if(!document.hidden&&!busy)run(refresh);});
})();
