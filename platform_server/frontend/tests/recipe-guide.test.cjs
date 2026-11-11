const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../dist/main-quest.js'),'utf8');
const start=source.indexOf('guide(){run('),end=source.indexOf(',paint(o,h,base)',start);
assert.ok(start>=0&&end>start);
const plain=value=>JSON.parse(JSON.stringify(value));
function fixture(serverState,hasNote=false,saved={}){
 const scenes=[],calls=[],writes=[],requests=[],events=[];let pending,reads=0;
 const progress={hasNote,noteDelivered:false,deferred:false,secretStarted:false,secretCompleted:false,topping:null,...saved};
 const dialog={open:true,close(){this.open=false;}};
 const c=vm.createContext({state:{completed:true,stage:6},dialog,
  window:{dispatchEvent:event=>events.push(event)},CustomEvent:class{constructor(type,{detail}){this.type=type;this.detail=detail;}},
  run:fn=>{pending=fn();},
  refresh:async()=>{reads++;c.state={...serverState};},
  api:async(data,route,base)=>{
   requests.push(plain({data,route,base}));
   if(!data)return {...progress,mainCompleted:serverState.completed};
   assert.equal(data.ticket,'nearby-proof');assert.equal(route,'recipe/action/');
   if(data.action==='recognize')progress.noteDelivered=true;
   else if(data.action==='defer')progress.deferred=true;
   else if(data.action==='start'){progress.secretStarted=true;progress.deferred=false;}
   else if(data.action==='topping')progress.topping=data.topping;
   else if(data.action==='finish'){progress.secretCompleted=true;return {state:{...progress},achievements:[{event_uuid:'recipe',achieved:true}],newly_completed:['recipe']};}
   else assert.fail('Unexpected recipe action: '+data.action);
   return {state:{...progress}};
  },
  investigate:async()=>calls.push('investigate'),confession:async()=>calls.push('confession'),
  story:(title,chapter,lines,actions=[])=>{dialog.open=true;scenes.push({title,chapter,lines,actions});},
  kline:(text,extra={})=>({speaker:'꾸왕이',text,...extra}),pline:text=>({speaker:'나',text}),
  guidance:()=>`현재 미션 ${c.state.stage}`,ticket:async target=>{assert.ok(['recipe-guide','final-guide'].includes(target));return 'nearby-proof';},
  act:async action=>{writes.push(action);assert.equal(action,'deliver');serverState={completed:true,stage:6};c.state={...serverState};},
 });
 const helperStart=source.indexOf('async function recipeAct('),helperEnd=source.indexOf('function workshopStory(',helperStart);
 vm.runInContext(source.slice(helperStart,helperEnd),c);
 vm.runInContext('globalThis.guide={'+source.slice(start,end)+'}.guide;',c);
 return {scenes,calls,writes,requests,events,dialog,progress,setNote(value){progress.hasNote=value;},get reads(){return reads;},
  async open(){c.guide();await pending;},
  async choose(label){const item=scenes.at(-1).actions.find(a=>a[0]===label);assert.ok(item,'Missing choice: '+label);await item[1]();},
 };
}
test('completed accounts receive dialogue 06 with confirmation and no progress writes',async()=>{
 const f=fixture({completed:true,stage:6});await f.open();
 assert.equal(f.reads,1);assert.equal(f.scenes.length,1);
 assert.deepEqual(f.requests,[{data:null,route:'recipe/status/',base:'/api/event-story/'}]);
 const scene=f.scenes[0];assert.equal(scene.chapter,'SECRET RECIPE · 일반 대화');
 assert.equal(scene.lines[0].text,'캠퍼스는 잘 둘러보고 있어? 막히는 일이 있으면 나한테 물어봐. 작은 단서도 그냥 지나치지 말고!');
 await f.choose('확인');assert.equal(f.dialog.open,false);assert.deepEqual(f.writes,[]);
});
test('unfinished investigation, confession and guidance retain the refreshed account state',async()=>{
 for(const stage of [1,2,3]){
  const f=fixture({completed:false,stage},true);await f.open();assert.equal(f.reads,1);assert.deepEqual(f.requests,[]);
  if(stage===1)assert.deepEqual(f.calls,['investigate']);
  else if(stage===3)assert.deepEqual(f.calls,['confession']);
  else {assert.equal(f.scenes[0].chapter,'MAIN QUEST');assert.equal(f.scenes[0].lines[0].text,'현재 미션 2');}
  assert.deepEqual(f.writes,[]);
 }
});
test('final main delivery retains its ending before subsequent dialogue 06',async()=>{
 const f=fixture({completed:false,stage:5});await f.open();assert.equal(f.scenes[0].title,'마지막 한 개의 선물');
 await f.scenes[0].actions[0][1]();assert.deepEqual(f.writes,['deliver']);
 assert.equal(f.scenes.at(-1).chapter,'EPILOGUE · SWEETER TOGETHER');
 await f.open();assert.equal(f.scenes.at(-1).chapter,'SECRET RECIPE · 일반 대화');
});
test('dialogue 07 verifies note delivery with a guide ticket and leaves the note owned',async()=>{
 const f=fixture({completed:true,stage:6},true);await f.open();
 const scene=f.scenes[0];assert.equal(scene.chapter,'SECRET RECIPE · 아이템 전용 대화');
 assert.equal(scene.lines[0].text,'옥상에서 반짝이는 곳을 조사하다가 이걸 찾았어. 비밀 레시피라는데… 이 발도장, 꾸왕이 네 거 아니야?');
 assert.equal(scene.lines[1].text,'어… 그 쪽지! 화단 깊숙이 숨겨 둔 건데… 어떻게 찾아냈어?');
 assert.equal(f.requests.at(-1).data.action,'recognize');assert.equal(f.progress.hasNote,true);
 f.dialog.close();await f.open();assert.equal(f.scenes.at(-1).title,'아이템 전용 대화');
 assert.equal(f.progress.secretStarted,false);assert.deepEqual(f.events,[]);
});
test('ownership is refreshed on each interaction and remains account-specific',async()=>{
 const owner=fixture({completed:true,stage:6});await owner.open();owner.setNote(true);await owner.open();
 assert.equal(owner.scenes.at(-1).title,'아이템 전용 대화');
 const other=fixture({completed:true,stage:6});await other.open();assert.equal(other.scenes.at(-1).title,'안내가이드 꾸왕이');
});
async function invitation(f,recipient=false){
 await f.open();await f.choose('이야기를 듣는다');assert.equal(f.scenes.at(-1).title,'숨겨진 이야기');
 await f.choose(recipient?'누구에게 주려고 했는데?':'한 개인데 재료는 왜 두 사람 분량이야?');
 if(recipient){assert.equal(f.scenes.at(-1).title,'선택 대사 · 마음');await f.choose('이야기를 계속 듣는다');}
 assert.equal(f.scenes.at(-1).title,'레시피의 빈칸');await f.choose('이야기를 계속 듣는다');
 assert.equal(f.scenes.at(-1).title,'루트 선택');
}
test('both confession questions and both topping choices reach the ending before the single reward',async()=>{
 for(const recipient of [false,true])for(const topping of ['thanks','promise']){
  const f=fixture({completed:true,stage:6},true);await invitation(f,recipient);
  await f.choose('좋아. 이번에는 같이 만들자.');assert.equal(f.progress.secretStarted,true);
  assert.equal(f.scenes.at(-1).title,'함께 완성하기');await f.choose('마지막 토핑을 고른다');
  await f.choose(topping==='thanks'?'고마워. 네 이야기를 들려줘서.':'다음에도 같이 만들자.');
  assert.equal(f.progress.topping,topping);assert.equal(f.progress.secretCompleted,false);assert.deepEqual(f.events,[]);
  await f.choose('레시피를 함께 완성한다');assert.equal(f.scenes.at(-1).title,'둘이 쓴 레시피');
  assert.equal(f.progress.secretCompleted,false);await f.choose('레시피 완성');assert.equal(f.dialog.open,false);
  assert.deepEqual(f.requests.filter(r=>r.data).map(r=>r.data.action),['recognize','start','topping','finish']);
  assert.deepEqual(f.events.map(e=>e.type),['campus:achievements','campus:achievement-earned']);
  assert.equal(f.progress.hasNote,true);const writes=f.requests.filter(r=>r.data).length;
  await f.open();assert.equal(f.scenes.at(-1).title,'완료 후 다시 대화');await f.choose('확인');
  assert.equal(f.requests.filter(r=>r.data).length,writes);assert.equal(f.events.length,2);
 }
});
test('deferral preserves ownership and resumes at the invitation without granting an achievement',async()=>{
 const f=fixture({completed:true,stage:6},true);await invitation(f);
 await f.choose('지금은 하던 일부터 마저 할게.');assert.equal(f.scenes.at(-1).title,'일반 동선으로 복귀');
 assert.equal(f.progress.deferred,true);assert.equal(f.progress.hasNote,true);assert.equal(f.progress.secretStarted,false);
 await f.choose('확인');assert.equal(f.dialog.open,false);await f.open();
 assert.equal(f.scenes.at(-1).title,'루트 선택');assert.deepEqual(f.events,[]);
});
test('saved checkpoints restore unfinished making, selected topping and the completed revisit',async()=>{
 for(const [saved,title] of [[{secretStarted:true},'함께 완성하기'],[{secretStarted:true,topping:'thanks'},'히든 대사 · 고마움'],[{secretStarted:true,topping:'promise'},'히든 대사 · 약속'],[{secretCompleted:true},'완료 후 다시 대화']]){
  const f=fixture({completed:true,stage:6},true,saved);await f.open();assert.equal(f.scenes.at(-1).title,title);
  assert.deepEqual(f.events,[]);
 }
});
