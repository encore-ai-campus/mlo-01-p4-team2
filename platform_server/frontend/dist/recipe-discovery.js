/* Personal discovery cinematic. No movement command, progress write or broadcast. */
(()=>{'use strict';
const reduce=matchMedia('(prefers-reduced-motion: reduce)'),viewport=document.getElementById('viewport');
const mark=document.createElement('div');mark.className='recipe-discovery-mark';mark.textContent='!';mark.hidden=true;mark.setAttribute('aria-hidden','true');viewport.append(mark);
const reveal=document.createElement('dialog');reveal.className='recipe-item-reveal';reveal.setAttribute('aria-label','숨겨진 레시피 발견');reveal.innerHTML='<img src="assets/seasonal/pepero/secret-recipe-pixel-v1.webp" alt="초콜릿 발도장이 찍힌 비밀 레시피"><button type="button">살펴보기</button>';document.body.append(reveal);
let active=false,started=0,phase='',cancel=null;
function pose(actor,t){
 if(!active||phase!=='reaction'||reduce.matches||actor!==window.CampusApp?.player)return null;
 const elapsed=Math.max(0,t-started),p=Math.min(1,elapsed/650);
 return {height:p<.2?0:Math.sin(Math.min(1,(p-.2)/.8)*Math.PI)*7,scale:p<.2?1-p*.65:1+Math.sin((p-.2)/.8*Math.PI)*.075};
}
async function play(){
 if(active)return false;active=true;phase='reaction';started=performance.now();
 const result=await new Promise(resolve=>{
  let frame=0,timer=0,ended=false;
  const finish=value=>{if(ended)return;ended=true;cancelAnimationFrame(frame);clearTimeout(timer);mark.hidden=true;if(reveal.open)reveal.close();document.removeEventListener('keydown',keys,true);reveal.removeEventListener('cancel',escape);reveal.querySelector('button').onclick=null;active=false;phase='';cancel=null;resolve(value)};
  cancel=()=>finish(false);
  function keys(e){if(!active)return;if(e.key==='Escape'){e.preventDefault();e.stopImmediatePropagation();finish(false);return}if(phase==='reaction'){e.preventDefault();e.stopImmediatePropagation()}}
  function escape(e){e.preventDefault();finish(false)}
  function follow(){if(ended)return;const app=window.CampusApp;if(!app||window.CampusFreeze?.blocking){finish(false);return}const q=app.worldToScreen(app.player.x,app.player.y);mark.style.left=q.x+'px';mark.style.top=(q.y-(window.CampusAvatars?.size||40)*app.camera.zoom-26)+'px';frame=requestAnimationFrame(follow)}
  document.addEventListener('keydown',keys,true);reveal.addEventListener('cancel',escape);mark.hidden=false;follow();
  timer=setTimeout(()=>{phase='item';mark.hidden=true;cancelAnimationFrame(frame);reveal.showModal();reveal.querySelector('button').focus({preventScroll:true});reveal.querySelector('button').onclick=()=>finish(true);timer=setTimeout(()=>finish(true),1900)},reduce.matches?250:720);
 });
 return result;
}
window.addEventListener('pagehide',()=>cancel?.());
window.CampusRecipeDiscovery={play,pose,get blocking(){return active}};
})();
