const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=path.resolve(__dirname,'..');
const script=fs.readFileSync(path.join(root,'dist/main-quest.js'),'utf8');
const note=require('../maps/pepero-worlds.json').worlds.rooftop.objects.find(o=>o.id==='hidden-recipe-note');
const plain=x=>JSON.parse(JSON.stringify(x));
function section(start,end){const a=script.indexOf(start),b=script.indexOf(end,a);assert.ok(a>=0&&b>a);return script.slice(a,b);}
function fixture({hasNote=false,completed=false,failure=null}={}){
 const requests=[],commands=[],scenes=[],errors=[];
 const dialog={open:true,querySelector:()=>({append:node=>errors.push(node.textContent)}),close(){this.open=false;}};
 const context=vm.createContext({busy:false,dialog,AbortSignal,stop(){},
  document:{cookie:'campus_csrftoken=token',createElement:()=>({setAttribute(){}})},
  window:{CampusApp:{presence:{command:async(action,data)=>{commands.push({action,...data});return {ticket:'nearby-proof'};}}}},
  pline:text=>({speaker:'나',person:'player',text}),
  story:(title,chapter,lines,actions)=>{dialog.open=true;scenes.push({title,lines,actions});},
  info:(title,copy,actions=[])=>scenes.push({title,copy,actions}),
  fetch:async(url,options)=>{
   const data=options.body?JSON.parse(options.body):null;requests.push({url,data,options});
   if(!data)return {ok:true,json:async()=>({hasNote,mainCompleted:completed})};
   if(failure)return {ok:false,json:async()=>failure};
   if(!completed)return {ok:false,json:async()=>({error:'main_quest_required',message:'메인 미션을 완료하면 주울 것 같이 생겼어요'})};
   hasNote=true;return {ok:true,json:async()=>({state:{hasNote:true,mainCompleted:true}})};
  },
 });
 vm.runInContext(section('async function api(','async function refresh('),context);
 vm.runInContext(section('async function ticket(','function btn('),context);
 vm.runInContext(section('async function recipeNote(','function workshopStory('),context);
 return {context,dialog,requests,commands,scenes,errors,setCompleted(){completed=true;}};
}
test('discovery keeps its original text regardless of main mission completion',async()=>{
 const pending=fixture();await pending.context.recipeNote();
 assert.equal(pending.scenes[0].lines[0].person,'player');assert.equal(pending.scenes[0].lines[0].text,'어? 이게 뭐지? 반짝이던 곳에 이런 쪽지가 있었네.');
 const completed=fixture({completed:true});await completed.context.recipeNote();
 assert.equal(completed.scenes[0].lines[0].text,'어? 이게 뭐지? 반짝이던 곳에 이런 쪽지가 있었네.');
});
test('inspection and leaving only read status and create no pickup request',async()=>{
 const f=fixture();await f.context.recipeNote();
 assert.deepEqual(f.requests.map(r=>r.url),['/api/event-story/recipe/status/']);
 assert.deepEqual(plain(f.scenes[0].actions.map(a=>a[0])),['접힌 쪽지를 챙긴다','일단 그대로 둔다']);
 await f.scenes[0].actions[1][1]();assert.equal(f.dialog.open,true);
 const leave=f.scenes.at(-1);
 assert.equal(leave.title,'나중에 다시 조사 가능');
 assert.equal(leave.lines[0].text,'누군가 잃어버린 레시피일지도 모른다. 쪽지를 잎 아래에 그대로 두었다. 다시 조사할 수 있도록 금빛이 반짝이던 위치를 기억해 두자.');
 assert.deepEqual(plain(leave.actions.map(a=>a[0])),['확인']);
 assert.equal(f.commands.length,0);assert.equal(f.requests.length,1);
 await leave.actions[0][1]();assert.equal(f.dialog.open,false);
 await f.context.recipeNote();assert.equal(f.scenes.at(-1).title,'화단 사이에 숨은 쪽지');
 assert.equal(f.requests.some(r=>r.data),false);
});
test('an incomplete main mission shows one dialogue with a confirmation that only closes',async()=>{
 const f=fixture();await f.context.recipeNote();
 await f.context.run(f.scenes[0].actions[0][1]);
 assert.deepEqual(f.errors,[]);
 const blocked=f.scenes.at(-1);
 assert.equal(blocked.title,'화단 사이에 숨은 쪽지');
 assert.equal(blocked.lines.length,1);
 assert.equal(blocked.lines[0].text,'메인 미션을 완료해야 주울 수 있을 것 같습니다.');
 assert.deepEqual(plain(blocked.actions.map(a=>a[0])),['확인']);
 const count=f.requests.length;await blocked.actions[0][1]();
 assert.equal(f.dialog.open,false);assert.equal(f.requests.length,count);
 assert.equal(f.scenes.some(s=>s.title==='아이템 획득'),false);
});
test('server completion permits pickup even when the inspection started before completion',async()=>{
 const f=fixture();await f.context.recipeNote();const acquire=f.scenes[0].actions[0][1];
 f.setCompleted();await f.context.run(acquire);
 assert.equal(f.scenes.at(-1).title,'아이템 획득');
 const post=f.requests.find(r=>r.data);
 assert.equal(post.url,'/api/event-story/recipe/action/');
 assert.deepEqual(post.data,{action:'acquire',ticket:'nearby-proof'});
 assert.equal(post.options.headers['X-CSRFToken'],'token');
 assert.deepEqual(f.commands,[{action:'story_access',target:'recipe-note'}]);
});
test('other server failures retain their error and do not become a prerequisite dialogue',async()=>{
 const f=fixture({completed:true,failure:{error:'interaction_expired',message:'가까이에서 다시 열어 주세요.'}});
 await f.context.recipeNote();await f.context.run(f.scenes[0].actions[0][1]);
 assert.deepEqual(f.errors,['가까이에서 다시 열어 주세요.']);assert.equal(f.scenes.length,1);
});
test('the existing busy guard suppresses repeated pickup clicks',async()=>{
 const f=fixture({completed:true});await f.context.recipeNote();
 const acquire=f.scenes[0].actions[0][1];
 await Promise.all([f.context.run(acquire),f.context.run(acquire)]);
 assert.equal(f.requests.filter(r=>r.data).length,1);
});
test('restored note ownership shows the holding message without issuing a pickup',async()=>{
 const f=fixture({hasNote:true,completed:true});await f.context.recipeNote();
 assert.equal(f.scenes[0].title,'쪽지 보관 중');
 assert.match(f.scenes[0].lines[0].text,/쪽지를 잘 접어 챙겼다/);
 assert.equal(f.commands.length,0);assert.equal(f.requests.length,1);
});
test('note uses the supplied sprite in the rooftop flowerbed gap without blocking movement',()=>{
 const worlds=require('../maps/pepero-worlds.json').worlds;
 assert.ok(!worlds.campus.objects.some(o=>o.id===note.id));
 assert.equal(note.roomId,'roof-plaza');assert.equal(note.solid,false);
 assert.deepEqual([note.x+note.w/2,note.y+note.h/2],[950,1690]);
 assert.deepEqual(note.interactionPoint,[950,1660]);
 const exportedWorlds=require('../../backend/campus/data/worlds.json').worlds;
 assert.ok(!exportedWorlds.campus.objects.some(o=>o.id===note.id));
 const exported=exportedWorlds.rooftop;
 assert.equal(exported.objects.filter(o=>o.id===note.id).length,1);
 assert.equal(exported.colliders.some(c=>c.objectId===note.id),false);
 const c=vm.createContext({window:{CAMPUS_SEASON:'pepero',dispatchEvent(){}},matchMedia:()=>({matches:true}),Image:class{},CustomEvent:class{}});
 vm.runInContext(fs.readFileSync(path.join(root,'dist/pepero-theme.js'),'utf8'),c);
 assert.equal(c.window.CampusSeasonal.art['recipe-note'],undefined,'physical note is not loaded for the map');
 const drawn=[],positions=[],gradient={addColorStop(){}};
 const g={save(){},restore(){},translate(...v){positions.push(v)},rotate(){},scale(){},beginPath(){},moveTo(){},bezierCurveTo(){},closePath(){},arc(){},fill(){drawn.push('light')},stroke(){},createRadialGradient(){return gradient}};
 assert.equal(c.window.CampusSeasonal.paint(note,{g,p:()=>({x:100,y:100})}),true);
 assert.ok(drawn.length>0);
 c.window.CampusSeasonal.paintRecipeGlimmer(note,{g,p:()=>({x:100,y:100})},0,1000);
 assert.equal(typeof c.window.CampusSeasonal.paintRecipeGlimmer,'function');
 assert.match(script,/handles\(o\)\{return o\.action==='recipe-note'/);
 assert.match(script,/open\(o\)\{if\(o\.action==='recipe-note'\)\{run\(recipeNote\)/);
});

test('discovery waits for the personal cinematic and cancellation never opens dialogue or writes progress',async()=>{
 const f=fixture();let finish;f.context.window.CampusRecipeDiscovery={play:()=>new Promise(r=>finish=r)};
 const pending=f.context.recipeNote();await new Promise(r=>setImmediate(r));assert.equal(f.scenes.length,0);finish(false);await pending;
 assert.equal(f.scenes.length,0);assert.equal(f.requests.length,1);assert.equal(f.commands.length,0);
});

test('an existing owner can replay the discovery without acquiring again or changing completion',async()=>{
 const f=fixture({hasNote:true,completed:true});let plays=0;
 f.context.window.CampusRecipeDiscovery={play:async()=>{assert.equal(f.dialog.open,false,'the holding dialog must close before the map reaction');plays++;return true;}};
 await f.context.recipeNote();assert.equal(f.scenes.at(-1).title,'쪽지 보관 중');
 await f.scenes.at(-1).actions.find(a=>a[0]==='발견 연출 다시 보기')[1]();
 assert.equal(plays,1);assert.equal(f.scenes.at(-1).lines[0].text,'어? 이게 뭐지? 반짝이던 곳에 이런 쪽지가 있었네.');
 assert.equal(f.scenes.at(-1).actions[0][0],'다시 보기 마치기');
 await f.scenes.at(-1).actions[0][1]();assert.equal(f.dialog.open,false);
 assert.equal(f.requests.filter(r=>r.data).length,0);assert.equal(f.commands.length,0);
});
