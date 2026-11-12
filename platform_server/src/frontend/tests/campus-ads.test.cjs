const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const visibility=require('../dist/ad-visibility.js');
function setup(){
  let now=0,respond,autoLoad=true,timerId=0;const calls=[],frames=[],listeners={},images=[],submitted=[],events=[],timers=new Map();
  const slot={hidden:true,children:[],append(child){this.children.push(child);},replaceChildren(){this.children=[];}};
  const dialog={open:true};
  const context={URL,Date,Promise,AbortController,Error,Map,Set,innerWidth:1000,innerHeight:800,
    performance:{now:()=>now},location:{href:'https://game.test/'},CampusAdVisibility:visibility,
    setTimeout(fn,delay){const id=++timerId;timers.set(id,{fn,at:now+delay});return id;},
    clearTimeout:id=>timers.delete(id),requestAnimationFrame:fn=>frames.push(fn),
    addEventListener:(name,fn)=>listeners[name]=fn,dispatchEvent:event=>events.push(event),
    CustomEvent:class{constructor(type,{detail}){this.type=type;this.detail=detail;}},
    document:{hidden:false,hasFocus:()=>true,cookie:'csrftoken=test',querySelector:()=>null,addEventListener:(name,fn)=>listeners[name]=fn,
      getElementById:id=>id==='floorTransitionAdSlot'?slot:dialog,
      body:{append(){}},
      createElement:tag=>({tag,children:[],append(child){this.children.push(child);},submit(){submitted.push(this);},remove(){this.removed=true;}})},
    Image:class{set src(value){this.url=value;images.push(this);if(autoLoad)Promise.resolve().then(()=>this.onload?.());}getBoundingClientRect(){return {left:0,top:0,right:100,bottom:100};}},
    fetch:async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>respond(url)};},
  };
  context.window=context;
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../dist/campus-ads.js'),'utf8'),context);
  return {context,slot,dialog,calls,listeners,images,submitted,events,setResponse(fn){respond=fn;},setTime(time){now=time;},
    holdImages(){autoLoad=false;},loadImage(index){images[index].onload?.();},failImage(index){images[index].onerror?.();},
    async flush(){for(let i=0;i<20;i++)await Promise.resolve();},
    async advance(time){now=time;for(const [id,timer] of [...timers])if(timer.at<=now){timers.delete(id);timer.fn();}await this.flush();},
    async frame(time){now=time;for(const fn of frames.splice(0))fn(time);await this.flush();}};
}
const entry=id=>({decision_id:id,ad:{title:'광고',creative_id:'creative',image_url:'https://ads.test/image/'+id,click_url:'https://ads.test/click/'+id,click_token:'signed-'+id}});

// 응답 도착과 이미지 로딩을 별도로 제어해 화면·링크·노출 대상의 교체 시점을 검증한다.
function setupRoof(){
  const h=setup(),writes=[];let current=null,board={x:0,y:0,w:100,h:100};
  const renderer={setDisplayImage(id,img){writes.push({id,img});current=img;return true;},
    displayPolygon(id,{placeholder=false}={}){const {x,y,w,h}=board;return current||placeholder?[{x,y},{x:x+w,y},{x:x+w,y:y+h},{x,y:y+h}]:null;}};
  return {...h,writes,renderer,setBoard(x,y,w=100,h=100){board={x,y,w,h};},
    tick(now,{floor='rooftop',blocked=false,camera={zoom:1,x:0,y:0},renderer:view=renderer,
      bounds={left:0,top:0,right:100,bottom:100,width:100,height:100},width=100,height=100}={}){
      h.setTime(now);h.context.CampusAds.tick({now,floor,renderer:view,camera,
        canvas:{getBoundingClientRect:()=>bounds},width,height,blocked});
    }};
}
const stamp=ms=>new Date(Date.UTC(2026,9,16)+ms).toISOString();
const roofBatch=(id,now=0,end=10000)=>({state:'READY',ads:[entry(id)],server_time:stamp(now),expires_at:stamp(end)});
const roofCalls=h=>h.calls.filter(c=>c.url.includes('placements/rooftop/'));
const impressions=(h,id)=>h.calls.filter(c=>c.url.includes('/'+id+'/impression/'));
function assertRoof(h,id){
  assert.equal(h.writes.at(-1).img.url,entry(id).ad.image_url);
  assert.equal(h.context.CampusAds.clickRoof(),true);
  const form=h.submitted.at(-1);
  assert.equal(form.action,'/api/ads/decisions/'+id+'/click/');
  assert.equal(form.method,'POST');assert.equal(form.target,'_blank');assert.equal(form.rel,'noopener');
  assert.equal(form.children.find(input=>input.name==='token').value,entry(id).ad.click_token);
  assert.equal(form.children.find(input=>input.name==='csrfmiddlewaretoken').value,'test');
  assert.equal(form.removed,true);
  assert.ok(h.writes.every(write=>write.img!==null));
}

test('10초 뒤에도 기존 이미지와 링크를 유지하고 새 이미지가 준비된 순간 함께 교체한다',async()=>{
  const h=setupRoof();let release;
  assert.equal(h.context.CampusAds.clickRoof(),false);
  h.setResponse(url=>url.includes('rooftop')?roofBatch('old'):{});
  h.tick(0);await h.flush();assertRoof(h,'old');
  h.tick(9999);assert.equal(roofCalls(h).length,1);
  h.setResponse(url=>url.includes('rooftop')?new Promise(resolve=>release=resolve):{});
  h.tick(10000);await h.flush();h.tick(10100);assertRoof(h,'old');
  assert.equal(roofCalls(h).length,2);
  h.holdImages();release(roofBatch('new',10100,20000));await h.flush();
  h.tick(10500);assertRoof(h,'old');assert.equal(h.writes.length,1);
  assert.equal(impressions(h,'new').length,0);
  h.setTime(10700);h.loadImage(1);await h.flush();
  assertRoof(h,'new');assert.equal(h.writes.length,2);
  // 이미지 도착 시점에 10초를 더하지 않고 서버 회차의 다음 경계에서 조회한다.
  h.setResponse(url=>url.includes('rooftop')?roofBatch('third',20000,30000):{});
  h.tick(19899);assert.equal(roofCalls(h).length,2);
  h.tick(20000);await h.flush();assert.equal(roofCalls(h).length,3);
});

test('기존 광고의 연속 노출은 10초 경계에서 끊지 않고 새 광고는 실제 표시 후 1초를 센다',async()=>{
  const h=setupRoof();
  h.setResponse(url=>url.includes('rooftop')?roofBatch('old'):{});
  h.tick(0);await h.flush();h.tick(9500);
  h.holdImages();h.setResponse(url=>url.includes('rooftop')?roofBatch('new',10000,20000):{});
  h.tick(10000);await h.flush();h.tick(10499);
  assert.equal(impressions(h,'old').length,0);
  h.tick(10500);await h.flush();
  assert.equal(impressions(h,'old').length,1);assert.equal(impressions(h,'new').length,0);
  h.setTime(10700);h.loadImage(1);await h.flush();h.tick(10700);
  h.tick(11699);assert.equal(impressions(h,'new').length,0);
  h.tick(11700);await h.flush();h.tick(13000);
  assert.equal(impressions(h,'old').length,1);assert.equal(impressions(h,'new').length,1);
});

for(const failure of ['광고 조회 실패','이미지 로딩 실패','이미지 시간 초과']){
  test(failure+' 시 기존 광고와 링크를 유지하고 재시도에 성공하면 교체한다',async()=>{
    const h=setupRoof();
    h.setResponse(url=>url.includes('rooftop')?roofBatch('old'):{});
    h.tick(0);await h.flush();h.holdImages();
    h.setResponse(url=>{
      if(!url.includes('rooftop'))return {};
      if(failure==='광고 조회 실패')throw Error('network');
      return roofBatch('failed',10000,20000);
    });
    h.tick(10000);await h.flush();let retryAt=15000;
    if(failure==='이미지 로딩 실패'){h.failImage(1);await h.flush();}
    if(failure==='이미지 시간 초과'){await h.advance(14000);retryAt=19000;}
    assertRoof(h,'old');assert.equal(h.writes.length,1);
    h.tick(retryAt-1);assert.equal(roofCalls(h).length,2);
    h.setResponse(url=>url.includes('rooftop')?roofBatch('recovered',retryAt,20000):{});
    h.tick(retryAt);await h.flush();assert.equal(roofCalls(h).length,3);
    h.loadImage(h.images.length-1);await h.flush();assertRoof(h,'recovered');
    // 시간 초과된 이미지가 나중에 도착해도 새 광고를 덮어쓰지 않는다.
    if(failure==='이미지 시간 초과'){h.loadImage(1);await h.flush();assertRoof(h,'recovered');}
  });
}

for(const state of ['PENDING','FAILED','EMPTY']){
  test('새 회차가 '+state+'이면 기존 광고를 유지하고 다음 조회 때 다시 확인한다',async()=>{
    const h=setupRoof();
    h.setResponse(url=>url.includes('rooftop')?roofBatch('old'):{});
    h.tick(0);await h.flush();
    h.setResponse(url=>url.includes('rooftop')?{...roofBatch('new',10000,20000),state:state==='EMPTY'?'READY':state,
      ads:state==='EMPTY'?[{decision_id:'empty',ad:null}]:[]}:{});
    h.tick(10000);await h.flush();assertRoof(h,'old');assert.equal(h.writes.length,1);
    const retryAt=state==='PENDING'?10250:20000;
    h.tick(retryAt-1);assert.equal(roofCalls(h).length,2);
    h.setResponse(url=>url.includes('rooftop')?roofBatch('new',retryAt,30000):{});
    h.tick(retryAt);await h.flush();assertRoof(h,'new');
    assert.equal(roofCalls(h).length,3);
  });
}

test('로딩 중 유효 회차가 지난 후보를 표시하지 않고 기존 광고를 유지하며 최신 회차를 조회한다',async()=>{
  const h=setupRoof();
  h.setResponse(url=>url.includes('rooftop')?roofBatch('old'):{});
  h.tick(0);await h.flush();h.holdImages();
  h.setResponse(url=>url.includes('rooftop')?roofBatch('stale',19500,20000):{});
  h.tick(19500);await h.flush();h.setTime(20100);h.loadImage(1);await h.flush();
  assertRoof(h,'old');assert.equal(h.writes.length,1);
  h.setResponse(url=>url.includes('rooftop')?roofBatch('latest',20100,30000):{});
  h.tick(20100);await h.flush();h.loadImage(2);await h.flush();assertRoof(h,'latest');
});

test('잘못된 응답 시각이나 클릭 주소는 기존 광고를 덮어쓰지 않는다',async()=>{
  for(const invalid of ['time','click']){
    const h=setupRoof();h.setResponse(url=>url.includes('rooftop')?roofBatch('old'):{});
    h.tick(0);await h.flush();const batch=roofBatch('bad',10000,20000);
    if(invalid==='time')batch.expires_at='invalid';else batch.ads[0].ad.click_url='javascript:alert(1)';
    h.setResponse(url=>url.includes('rooftop')?batch:{});
    h.tick(10000);await h.flush();assertRoof(h,'old');assert.equal(h.writes.length,1);
    h.setResponse(url=>url.includes('rooftop')?roofBatch('good',15000,20000):{});
    h.tick(15000);await h.flush();assertRoof(h,'good');
  }
});

test('옥상에서 나갔다 돌아와도 유지 중인 같은 결정의 노출을 중복 보고하지 않는다',async()=>{
  const h=setupRoof();h.setResponse(url=>url.includes('rooftop')?roofBatch('old'):{});
  h.tick(0);await h.flush();h.tick(1);h.tick(1001);await h.flush();
  assert.equal(impressions(h,'old').length,1);
  h.tick(11000,{floor:'campus'});assert.equal(roofCalls(h).length,1);
  h.setResponse(url=>url.includes('rooftop')?Promise.reject(Error('offline')):{});
  h.tick(12000);await h.flush();h.tick(13000);
  assertRoof(h,'old');assert.equal(impressions(h,'old').length,1);
});
test('포털 좌우 같은 결정은 표시 두 개와 노출 보고 한 건으로 연결된다',async()=>{
  const h=setup();h.setResponse(url=>url.includes('placements')?{state:'READY',ads:[entry('one'),entry('one')]}:{});
  await h.context.CampusAds.startPortal('approved');await h.flush();
  assert.equal(h.slot.children.length,2);
  for(const form of h.slot.children){
    assert.equal(form.action,'/api/ads/decisions/one/click/');assert.equal(form.method,'POST');
    assert.equal(form.target,'_blank');assert.equal(form.rel,'noopener');
    assert.equal(form.children.find(input=>input.name==='token').value,'signed-one');
    assert.equal(form.children.find(input=>input.name==='csrfmiddlewaretoken').value,'test');
    const button=form.children.find(child=>child.tag==='button');assert.equal(button.type,'submit');
    assert.equal(button.children[0].alt,'광고');
  }
  await h.frame(0);await h.frame(999);
  assert.equal(h.calls.filter(c=>c.url.includes('impression')).length,0);
  await h.frame(1000);await h.frame(2000);
  assert.equal(h.calls.filter(c=>c.url.includes('impression')).length,1);
  assert.equal(JSON.parse(h.calls[0].options.body).token,'approved');
});
test('이동이 끝난 뒤 도착한 응답은 광고를 표시하거나 보고하지 않는다',async()=>{
  const h=setup();let release;
  h.setResponse(()=>new Promise(resolve=>release=resolve));
  const request=h.context.CampusAds.startPortal('approved');await h.flush();
  h.dialog.open=false;h.listeners['campus:floor-transition-end']();
  release({state:'READY',ads:[entry('late')]});await request;
  assert.equal(h.slot.children.length,0);await h.frame(3000);
  assert.equal(h.calls.length,1);
});
test('좌우 다른 결정은 각각 연속 노출을 보고한다',async()=>{
  const h=setup();h.setResponse(url=>url.includes('placements')?{state:'READY',ads:[entry('left'),entry('right')]}:{});
  await h.context.CampusAds.startPortal('approved');await h.frame(0);await h.frame(1000);
  assert.equal(h.calls.filter(c=>c.url.includes('impression')).length,2);
});
test('옥상 회차가 포털 표시 중 끝나도 포털의 연속 노출 시간은 유지된다',async()=>{
  const h=setup();
  h.setResponse(url=>url.includes('rooftop')?{state:'READY',ads:[entry('roof')],server_time:'2026-10-15T00:00:00.000Z',expires_at:'2026-10-15T00:00:00.500Z'}:
    url.includes('portal')?{state:'READY',ads:[entry('portal')]}:{});
  const renderer={setDisplayImage(){return true;},displayPolygon(){return [{x:0,y:0},{x:100,y:0},{x:100,y:100},{x:0,y:100}];}};
  const tick=(now,floor)=>h.context.CampusAds.tick({now,floor,renderer,camera:{zoom:1,x:0,y:0},canvas:{getBoundingClientRect(){return {left:0,top:0,right:100,bottom:100,width:100,height:100};}},width:100,height:100,blocked:false});
  tick(0,'rooftop');await h.flush();assert.equal(roofCalls(h).length,1);
  await h.context.CampusAds.startPortal('approved');await h.frame(0);
  await h.frame(600);tick(600,'campus');await h.frame(1000);
  assert.equal(h.calls.filter(c=>c.url.includes('/portal/impression/')).length,1);
});

test('노출 보고는 campus CSRF 쿠키를 사용하고 업적을 지급하지 않는다',async()=>{
 const h=setupRoof();h.context.document.cookie='campus_csrftoken=campus-token';
 const badge={event_uuid:'ad',name:'시선 강탈 사건',achieved:true};
 h.setResponse(url=>url.includes('placements/rooftop/')?roofBatch('ad'):{achievements:[badge],newly_completed:['ad']});
 h.tick(0);await h.flush();h.tick(10);h.tick(1010);await h.flush();
 assert.equal(impressions(h,'ad')[0].options.headers['X-CSRFToken'],'campus-token');
 assert.deepEqual(h.events,[]);
 h.tick(2010);await h.flush();assert.equal(impressions(h,'ad').length,1);
});

test('옥상 클릭은 현재 CSRF 쿠키와 서명 토큰으로 바로 폼을 제출한다',async()=>{
 const h=setupRoof();h.setResponse(()=>roofBatch('ad'));h.tick(0);await h.flush();
 h.context.document.cookie='campus_csrftoken=current-token';
 const before=h.calls.length;assert.equal(h.context.CampusAds.clickRoof(),true);
 assert.equal(h.calls.length,before);
 const form=h.submitted.at(-1);assert.equal(form.action,'/api/ads/decisions/ad/click/');
 assert.equal(form.children.find(input=>input.name==='csrfmiddlewaretoken').value,'current-token');
 assert.equal(form.children.find(input=>input.name==='token').value,'signed-ad');
});


test('멀리 있거나 다른 층이면 회차가 지나도 요청하지 않고 이미지 없는 접근 시 한 번 미리 받는다',async()=>{
  const h=setupRoof();h.setResponse(url=>url.includes('rooftop')?roofBatch('near',20000,30000):{});
  h.setBoard(1000,0);h.tick(0);h.tick(10000);await h.flush();assert.equal(roofCalls(h).length,0);
  h.setBoard(200,0);h.tick(15000,{floor:'other'});assert.equal(roofCalls(h).length,0);
  h.tick(20000);h.tick(20001);await h.flush();assert.equal(roofCalls(h).length,1);assertRoof(h,'near');
  h.tick(21001);h.tick(30001);h.tick(40001);await h.flush();
  assert.equal(roofCalls(h).length,1);assert.equal(impressions(h,'near').length,0);
  h.setResponse(url=>url.includes('rooftop')?roofBatch('visible',40002,50000):{});
  h.setBoard(0,0);h.tick(40002);await h.flush();assert.equal(roofCalls(h).length,2);
  h.tick(40003);h.tick(41003);await h.flush();assert.equal(impressions(h,'visible').length,1);
});

test('경계 주변 이동은 추가 발급 없이 유지하고 충분히 이탈한 뒤 재진입하면 다시 받는다',async()=>{
  const h=setupRoof();h.setResponse(url=>url.includes('rooftop')?roofBatch('near'):{});
  h.setBoard(350,0);h.tick(0);await h.flush();assert.equal(roofCalls(h).length,1);
  for(const [time,x] of [[10000,370],[20000,350],[30000,470]]){h.setBoard(x,0);h.tick(time);await h.flush();}
  assert.equal(roofCalls(h).length,1);
  h.setBoard(490,0);h.tick(31000);h.setBoard(370,0);h.tick(32000);assert.equal(roofCalls(h).length,1);
  h.setResponse(url=>url.includes('rooftop')?roofBatch('return',33000,40000):{});
  h.setBoard(350,0);h.tick(33000);await h.flush();assert.equal(roofCalls(h).length,2);assertRoof(h,'return');
});

test('유효한 광고는 이탈 후 돌아와도 재요청하지 않고 만료된 광고만 갱신한다',async()=>{
  const h=setupRoof();h.setResponse(url=>url.includes('rooftop')?roofBatch('one'):{});
  h.tick(0);await h.flush();h.setBoard(1000,0);h.tick(100);h.setBoard(0,0);h.tick(200);await h.flush();
  assert.equal(roofCalls(h).length,1);
  h.setBoard(1000,0);h.tick(10000);h.tick(20000);assert.equal(roofCalls(h).length,1);
  h.setResponse(url=>url.includes('rooftop')?roofBatch('two',21000,30000):{});
  h.setBoard(0,0);h.tick(21000);await h.flush();assert.equal(roofCalls(h).length,2);assertRoof(h,'two');
});

for(const stage of ['response','image'])test('이탈 뒤 늦은 '+stage+'는 표시하지 않고 재진입 요청을 막지 않는다',async()=>{
  const h=setupRoof();let release;
  h.setResponse(url=>url.includes('rooftop')?roofBatch('old'):{});h.tick(0);await h.flush();
  if(stage==='response')h.setResponse(url=>url.includes('rooftop')?new Promise(resolve=>release=resolve):{});
  else {h.holdImages();h.setResponse(url=>url.includes('rooftop')?roofBatch('late',10000,20000):{});}
  h.tick(10000);await h.flush();h.setBoard(1000,0);h.tick(10010);h.setBoard(0,0);h.tick(10020);
  assert.equal(roofCalls(h).length,2);
  if(stage==='response')release(roofBatch('late',10000,20000));else h.loadImage(1);
  await h.flush();assertRoof(h,'old');assert.equal(h.writes.length,1);
  h.setResponse(url=>url.includes('rooftop')?roofBatch('fresh',10030,20000):{});
  h.tick(10030);await h.flush();if(stage==='image'){h.loadImage(2);await h.flush();}
  assert.equal(roofCalls(h).length,3);assertRoof(h,'fresh');
});

test('숨김 이벤트로 진행 중 응답을 무효화하고 모달 또는 숨김 상태에서는 요청하지 않는다',async()=>{
  const h=setupRoof();let release;
  h.setResponse(()=>new Promise(resolve=>release=resolve));h.tick(0,{blocked:true});assert.equal(roofCalls(h).length,0);
  h.tick(1);await h.flush();h.context.document.hidden=true;h.listeners.visibilitychange();
  release(roofBatch('late'));await h.flush();assert.equal(h.writes.length,0);
  h.tick(20000);assert.equal(roofCalls(h).length,1);
  h.context.document.hidden=false;h.listeners.visibilitychange();h.setResponse(()=>roofBatch('back',20001,30000));
  h.tick(20001);await h.flush();assertRoof(h,'back');
});

test('서로 다른 렌더러의 만료 시각과 늦은 응답을 재사용하지 않는다',async()=>{
  const h=setupRoof(),other=setupRoof();let release;
  h.setResponse(()=>new Promise(resolve=>release=resolve));h.tick(0);await h.flush();
  h.tick(1,{renderer:other.renderer});release(roofBatch('old'));await h.flush();assert.equal(h.writes.length,0);
  h.setResponse(()=>roofBatch('new',2,10000));h.tick(2,{renderer:other.renderer});await h.flush();
  assert.equal(roofCalls(h).length,2);assert.equal(other.writes.length,1);
});

test('캠퍼스 광고판도 카메라 줌과 CSS 캔버스 배율·위치로 접근 여부를 판단한다',async()=>{
  const h=setupRoof();h.renderer.hasDisplay=()=>true;h.setResponse(()=>roofBatch('scaled'));h.setBoard(300,0,50,50);
  const bounds={left:100,top:50,right:300,bottom:250,width:200,height:200};
  h.tick(0,{floor:'campus',bounds});assert.equal(roofCalls(h).length,0);
  h.tick(1,{floor:'campus',bounds,camera:{zoom:.5,x:0,y:0}});await h.flush();assert.equal(roofCalls(h).length,1);
  h.tick(10001,{floor:'campus',bounds,camera:{zoom:1,x:0,y:0}});assert.equal(roofCalls(h).length,1);
  h.setResponse(()=>roofBatch('panned',10002,20000));
  h.tick(10002,{floor:'campus',bounds,camera:{zoom:1,x:-300,y:0}});await h.flush();assert.equal(roofCalls(h).length,2);
});

test('화면 크기 변경과 브라우저 바깥으로 잘린 캔버스 영역을 반영한다',async()=>{
  const h=setupRoof();h.setResponse(()=>roofBatch('resized'));h.setBoard(400,0);
  h.tick(0);assert.equal(roofCalls(h).length,0);
  h.tick(1,{width:500,bounds:{left:0,top:0,right:500,bottom:100,width:500,height:100}});await h.flush();
  assert.equal(roofCalls(h).length,1);
  const hidden=setupRoof();hidden.setResponse(()=>roofBatch('hidden'));
  hidden.tick(0,{bounds:{left:1100,top:0,right:1200,bottom:100,width:100,height:100}});
  hidden.tick(10000,{width:0});assert.equal(roofCalls(hidden).length,0);
});

test('사전 요청이 PENDING이거나 실패하면 근처에서 재시도하고 완료 뒤 화면 밖 갱신은 멈춘다',async()=>{
  const h=setupRoof();h.setBoard(200,0);
  h.setResponse(()=>({...roofBatch('near'),state:'PENDING',ads:[]}));h.tick(0);await h.flush();
  h.tick(249);assert.equal(roofCalls(h).length,1);
  h.setResponse(()=>{throw Error('offline');});h.tick(250);await h.flush();h.tick(5249);assert.equal(roofCalls(h).length,2);
  h.setResponse(()=>roofBatch('ready',5250,10000));h.tick(5250);await h.flush();h.tick(20000);
  assert.equal(roofCalls(h).length,3);assertRoof(h,'ready');assert.equal(impressions(h,'ready').length,0);
});
