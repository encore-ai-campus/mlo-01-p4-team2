const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../dist/achievements.js'),'utf8');
const rows=(earned=false)=>[
 {event_uuid:'greeting',name:'수상한 친화력',achieved:earned,completed_at:earned?'2026-11-03T00:00:00Z':null,image_url:'/static/campus/assets/achievements/manager-greeting.png'},
 {event_uuid:'poking',name:'참을성 테스트',achieved:false,completed_at:null,image_url:'/static/campus/assets/achievements/manager-poking.png'},
 {event_uuid:'python',name:'뱀을 길들이는 자',achieved:false,completed_at:null,image_url:'/static/campus/assets/achievements/python-intermediate.png'},
 {event_uuid:'sql',name:'데이터의 비밀문',achieved:false,completed_at:null,image_url:'/static/campus/assets/achievements/sql-intermediate.png'},
 {event_uuid:'timer',name:'찰나의 기적',achieved:false,completed_at:null,image_url:'/static/campus/assets/achievements/timer.png'},
 {event_uuid:'typing',name:'손가락에 불났다',achieved:false,completed_at:null,image_url:'/static/campus/assets/achievements/code-typing.png'},
 {event_uuid:'all',name:'삼각지대 탈출',achieved:false,completed_at:null,image_url:'/static/campus/assets/achievements/all-subjects.png'},
 {event_uuid:'cs',name:'컴퓨터의 속마음',achieved:false,completed_at:null,image_url:'/static/campus/assets/achievements/cs-intermediate.png'},
 {event_uuid:'recipe',name:'꾸왕이의 비밀 레시피',achieved:false,completed_at:null,image_url:'/static/campus/assets/achievements/secret-recipe.png'}];
const plain=value=>JSON.parse(JSON.stringify(value));
function fixture(){
 const elements=new Map(),listeners=new Map(),requests=[],timers=new Map();let opens=0,closes=0,blocked=false;
 function element(){return {hidden:true,open:false,textContent:'',children:[],dataset:{},attributes:{},handlers:{},
  append(...items){this.children.push(...items);},replaceChildren(){this.children=[];},
  setAttribute(key,value){this.attributes[key]=String(value);},removeAttribute(key){delete this.attributes[key];},
  addEventListener(key,fn){this.handlers[key]=fn;},focus(){this.focused=true;},
  showModal(){this.open=true;},close(){this.open=false;this.handlers.close?.();},
 };}
 const get=id=>{if(!elements.has(id))elements.set(id,element());return elements.get(id);};
 const window={location:{origin:'http://campus.test'},addEventListener:(name,fn)=>listeners.set(name,fn)};
 const document={baseURI:'http://campus.test/static/campus/',getElementById:get,createElement:element};
 const fetch=(url,options)=>new Promise((resolve,reject)=>requests.push({url,options,resolve,reject}));
 vm.runInNewContext(source,{window,document,fetch,URL,AbortController,
  setTimeout(fn,ms){const id={};timers.set(id,{fn,ms});return id;},clearTimeout:id=>timers.delete(id)});
 const api=window.CampusAchievements.create({isBlocked:()=>blocked,onOpen(){opens++;},onClose(){closes++;}});
 const flush=()=>new Promise(resolve=>setImmediate(resolve));
 return {get,requests,listeners,api,timers,flush,set blocked(value){blocked=value;},get opens(){return opens;},get closes(){return closes;},
  async respond(data=rows(),index=requests.length-1,options={}){requests[index].resolve({ok:true,redirected:false,headers:{get:()=> 'application/json'},json:async()=>({achievements:data}),...options});await flush();},
  update(data){listeners.get('campus:achievements')({detail:data});},
  earned(ids){listeners.get('campus:achievement-earned')({detail:{newly_completed:ids}});},
  runTimer(ms){const entry=[...timers].find(([,value])=>value.ms===ms);assert.ok(entry,'timer '+ms);timers.delete(entry[0]);entry[1].fn();},
 };
}
test('loading and request failure never invent unearned achievements; retry reads authenticated JSON',async()=>{
 const f=fixture();assert.equal(f.get('achievementsList').children.length,0);
 assert.equal(f.requests[0].url,'/api/event-story/achievements/');assert.equal(f.requests[0].options.credentials,'same-origin');
 await f.respond(null,0,{ok:false,status:401});
 assert.equal(f.get('achievementsList').children.length,0);assert.equal(f.get('achievementsRetry').hidden,false);
 assert.match(f.get('achievementsStatus').textContent,/불러오지 못/);
 f.get('achievementsRetry').handlers.click();await f.respond();
 assert.equal(f.get('achievementsList').children.length,9);assert.equal(f.get('achievementsRetry').hidden,true);assert.equal(f.timers.size,0);
});
test('the nine implemented achievements render, preserving names and original image URLs across earned states',async()=>{
 const f=fixture();await f.respond([...rows(),{name:'기타 업적'}]);
 const list=f.get('achievementsList');
 assert.deepEqual(list.children.map(row=>row.children[1].textContent),['수상한 친화력','참을성 테스트','뱀을 길들이는 자','데이터의 비밀문','찰나의 기적','손가락에 불났다','삼각지대 탈출','컴퓨터의 속마음','꾸왕이의 비밀 레시피']);
 assert.deepEqual(list.children.map(row=>row.dataset.achieved),Array(9).fill('false'));
 const image=list.children[0].children[0].src;
 f.update(rows(true));assert.equal(list.children[0].dataset.achieved,'true');assert.equal(list.children[0].children[0].src,image);
 assert.equal(list.children[0].children[1].textContent,'수상한 친화력');assert.equal(list.children[0].children[2].textContent,'획득');
 const css=fs.readFileSync(path.join(__dirname,'../dist/achievements.css'),'utf8');
 assert.match(css,/\[data-achieved="false"\] img\{filter:brightness\(0\)\}/);assert.doesNotMatch(css,/grayscale|opacity:/);
});
test('a personal socket update wins over an older HTTP response',async()=>{
 const f=fixture();f.update(rows(true));await f.respond(rows(false));
 assert.equal(f.get('achievementsList').children[0].dataset.achieved,'true');
});
test('opening gates game input, respects existing dialogs, and closing returns focus to the trigger',async()=>{
 const f=fixture();await f.respond();f.blocked=true;f.get('achievementsOpen').handlers.click();assert.equal(f.api.blocking,false);
 f.blocked=false;f.get('achievementsOpen').handlers.click();
 assert.equal(f.opens,1);assert.equal(f.api.blocking,true);assert.equal(f.get('achievementsClose').focused,true);
 f.get('achievementsClose').handlers.click();assert.equal(f.api.blocking,false);assert.equal(f.closes,1);assert.equal(f.get('achievementsOpen').focused,true);
 await f.respond();
 const html=fs.readFileSync(path.join(__dirname,'../dist/index.html'),'utf8');
 assert.match(html,/<dialog id="achievementsDialog" aria-labelledby="achievementsTitle"/);
 assert.match(html,/id="achievementsOpen"[^>]+aria-haspopup="dialog"[^>]+aria-controls="achievementsDialog"/);
});
test('malformed rows and foreign images show retry without replacing server truth',async()=>{
 const f=fixture();const invalid=rows();invalid[0].image_url='https://foreign.test/icon.png';await f.respond(invalid);
 assert.equal(f.get('achievementsList').children.length,0);assert.equal(f.get('achievementsRetry').hidden,false);
 f.get('achievementsRetry').handlers.click();await f.respond(rows(true));
 assert.equal(f.get('achievementsList').children[0].dataset.achieved,'true');
});
test('initial earned snapshots stay quiet and confirmed new completions show original art and name',async()=>{
 const f=fixture();await f.respond(rows(true));
 assert.equal(f.get('achievementNotice').hidden,true);
 f.earned(['greeting']);f.runTimer(0);
 assert.equal(f.get('achievementNotice').hidden,false);
 assert.equal(f.get('achievementNoticeName').textContent,'수상한 친화력');
 assert.match(f.get('achievementNoticeImage').src,/manager-greeting\.png$/);
 f.runTimer(6000);assert.equal(f.get('achievementNotice').hidden,true);
 f.earned(['greeting']);assert.equal(f.timers.size,0);
});
test('simultaneous completions queue once each and manual dismissal advances the queue',async()=>{
 const f=fixture(),data=rows(true);data[1].achieved=true;data[1].completed_at=data[0].completed_at;
 await f.respond(data);f.earned(['greeting','poking']);f.earned(['greeting','poking']);f.runTimer(0);
 assert.equal(f.get('achievementNoticeName').textContent,'수상한 친화력');
 f.get('achievementNoticeClose').handlers.click();
 assert.equal(f.get('achievementNoticeName').textContent,'참을성 테스트');
 f.runTimer(6000);assert.equal(f.get('achievementNotice').hidden,true);assert.equal(f.timers.size,0);
});
test('unknown or unearned IDs cannot trigger a completion notice',async()=>{
 const f=fixture();await f.respond();f.earned(['greeting','unknown']);
 assert.equal(f.get('achievementNotice').hidden,true);assert.equal(f.timers.size,0);
});
test('recovered server state announces a missed completion once without replaying old achievements',async()=>{
 const f=fixture();await f.respond(rows(false));
 f.api.refresh();await f.respond(rows(true));f.runTimer(0);
 assert.equal(f.get('achievementNoticeName').textContent,'수상한 친화력');
 f.runTimer(6000);f.api.refresh();await f.respond(rows(true));
 assert.equal(f.get('achievementNotice').hidden,true);assert.equal(f.timers.size,0);
});

test('learning badges use the same silhouette and completion notice flow',async()=>{
 const f=fixture();await f.respond();
 const data=rows();for(const row of data.slice(2,4)){row.achieved=true;row.completed_at='2026-11-03T00:00:00Z';}
 f.update(data);f.runTimer(0);
 assert.equal(f.get('achievementNoticeName').textContent,'뱀을 길들이는 자');
 assert.match(f.get('achievementNoticeImage').src,/python-intermediate/);
 f.get('achievementNoticeClose').handlers.click();
 assert.equal(f.get('achievementNoticeName').textContent,'데이터의 비밀문');
 assert.equal(f.get('achievementsList').children[2].dataset.achieved,'true');
 assert.equal(f.get('achievementsList').children[3].dataset.achieved,'true');
});

test('both game achievements use supplied art, turn from silhouettes to earned, and queue notices',async()=>{
 const f=fixture();await f.respond();
 const data=rows();for(const row of data.slice(4,6)){row.achieved=true;row.completed_at='2026-11-04T00:00:00Z';}
 f.update(data);f.runTimer(0);
 assert.equal(f.get('achievementNoticeName').textContent,'찰나의 기적');
 assert.match(f.get('achievementNoticeImage').src,/timer.png$/);
 f.get('achievementNoticeClose').handlers.click();
 assert.equal(f.get('achievementNoticeName').textContent,'손가락에 불났다');
 assert.match(f.get('achievementNoticeImage').src,/code-typing.png$/);
 assert.equal(f.get('achievementsList').children[4].dataset.achieved,'true');
 assert.equal(f.get('achievementsList').children[5].dataset.achieved,'true');
 f.runTimer(6000);f.earned(['timer','typing']);assert.equal(f.timers.size,0);
});

test('CS and three-subject achievements queue both earned notices once',async()=>{
 const f=fixture();await f.respond();const data=rows();
 for(const row of data.slice(6,8)){row.achieved=true;row.completed_at='2026-11-04T00:00:00Z';}
 f.update(data);f.runTimer(0);assert.equal(f.get('achievementNoticeName').textContent,'삼각지대 탈출');
 f.get('achievementNoticeClose').handlers.click();assert.equal(f.get('achievementNoticeName').textContent,'컴퓨터의 속마음');
 f.runTimer(6000);f.earned(['all','cs']);assert.equal(f.timers.size,0);
 assert.ok(f.get('achievementsList').children.slice(6,8).every(r=>r.dataset.achieved==='true'));
});
