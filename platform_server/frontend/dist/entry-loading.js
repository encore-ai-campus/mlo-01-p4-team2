/* Stage only the arrival floor; deferred art uses one idle background request. */
(()=>{'use strict';
const config=JSON.parse(document.getElementById('campusPresenceConfig')?.textContent||'{}');
const initialFloor=['campus','rooftop'].includes(config.position?.floor)?config.position.floor:'campus';
const records=[],queue=[];let active=0,background=false,blocking=true;const started=performance.now();
const panel=document.getElementById('campusEntry'),label=document.getElementById('entryStatus'),bar=document.getElementById('entryProgress'),count=document.getElementById('entryCount');
const metrics={initialFloor,started,readyAt:null,assetsAt:null,failed:[]};
function update(){const critical=records.filter(r=>!r.background&&r.floors.includes(initialFloor));const done=critical.filter(r=>r.state==='done').length;if(count)count.textContent='이미지 준비 '+done+' / '+critical.length;if(bar)bar.value=critical.length?Math.round(90*done/critical.length):0;}
function pump(){queue.sort((a,b)=>a.priority-b.priority);while(queue.length&&active<(blocking?3:1)){const i=queue.findIndex(r=>r.priority===0||background);if(i<0)return;const r=queue.splice(i,1)[0];active++;r.state='loading';r.image.decoding='async';r.image.fetchPriority=r.priority===0?'high':'low';
 const cleanup=()=>{r.image.removeEventListener('load',loaded);r.image.removeEventListener('error',failed);};
 const failed=()=>{cleanup();r.state='failed';metrics.failed.push(r.url);active--;r.reject(Error('이미지를 준비하지 못했어요. 다시 시도해 주세요.'));update();pump();};
 const loaded=async()=>{cleanup();try{await r.image.decode();r.state='done';r.resolve(r.image);}catch{r.state='failed';metrics.failed.push(r.url);r.reject(Error('이미지를 읽지 못했어요. 다시 시도해 주세요.'));}finally{active--;update();pump();}};
 r.image.addEventListener('load',loaded);r.image.addEventListener('error',failed);r.image.src=r.url;
}}
function image(img,url,{floors=['campus','rooftop'],background:deferred=false}={}){if(!floors.length)return Promise.resolve(img);const r={image:img,url,floors,background:deferred,state:'queued',priority:!deferred&&floors.includes(initialFloor)?0:1};r.promise=new Promise((resolve,reject)=>{r.resolve=resolve;r.reject=reject;});r.promise.catch(()=>{});records.push(r);queue.push(r);update();pump();return r.promise;}
function floorKeys(floor){const keys=new Set();function visit(o){if(o.seasonalArt)keys.add(o.seasonalArt);const legacy={'pepero-monument':'monument','pepero-cart':'cart','pepero-gift-art':'gifts'};if(legacy[o.type])keys.add(legacy[o.type]);for(const c of o.children||[])visit(c);}for(const o of window.CampusWorlds?.[floor]?.objects||[])visit(o);return keys;}
function floorsForArt(key){return ['campus','rooftop'].filter(f=>floorKeys(f).has(key));}
function error(e){label.textContent=e.message||'입장을 준비하지 못했어요. 다시 시도해 주세요.';document.getElementById('entryRetry').hidden=false;panel?.setAttribute('data-error','true');}
function bounded(p){let timer;return Promise.race([p,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('준비가 지연되고 있어요. 연결을 확인한 뒤 다시 시도해 주세요.')),30000)})]).finally(()=>clearTimeout(timer));}
async function ensureFloor(floor){const required=records.filter(r=>!r.background&&r.floors.includes(floor));for(const r of required)r.priority=0;pump();await bounded(Promise.all(required.map(r=>r.promise)));}
async function prepare(){label.textContent=(initialFloor==='rooftop'?'옥상':'18층')+' 캠퍼스를 준비하고 있어요…';try{await ensureFloor(initialFloor);metrics.assetsAt=performance.now();label.textContent='서버 연결과 내 위치를 확인하고 있어요…';bar.value=95;}catch(e){error(e);throw e;}}
async function completeWhenConnected(){try{const deadline=performance.now()+30000;while(!window.CampusApp?.presence.connected){if(performance.now()>deadline)throw Error('서버 연결을 확인하지 못했어요. 다시 시도해 주세요.');await new Promise(r=>setTimeout(r,100));}await ensureFloor(window.CampusApp.floor);await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));bar.value=100;metrics.readyAt=performance.now();performance.mark('campus-entry-ready');blocking=false;panel.hidden=true;document.documentElement.classList.remove('campus-entry-pending');window.CampusApp.presence.setInput([]);document.getElementById('mapCanvas')?.focus({preventScroll:true});const idle=()=>{background=true;pump();};if('requestIdleCallback'in window)requestIdleCallback(idle,{timeout:2000});else setTimeout(idle,1000);window.dispatchEvent(new Event('campus:entry-ready'));}catch(e){error(e);throw e;}}
document.getElementById('entryRetry').onclick=()=>location.reload();setTimeout(()=>{if(blocking&&!panel?.dataset.error)label.textContent='처음 방문하면 조금 더 걸릴 수 있어요. 캠퍼스를 준비하고 있어요…';},10000);
window.CampusEntry={image,floorsForArt,prepare,ensureFloor,completeWhenConnected,metrics,get blocking(){return blocking;},get initialFloor(){return initialFloor;},get avatar(){return config.avatar;},get records(){return records.map(r=>({url:r.url,state:r.state,priority:r.priority,background:r.background,floors:r.floors}));}};
})();
