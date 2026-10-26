/* World-anchored, keyboard-accessible guide and lunch-wheel interactions. */
(() => {
  'use strict';
  const viewport=document.getElementById('viewport');if(!viewport)return;
  function action(className,label,image){
    const button=document.createElement('button');button.type='button';button.className='campus-world-action '+className;button.setAttribute('aria-label',label);button.hidden=true;
    const img=document.createElement('img');img.src=new URL(image,document.baseURI).href;img.alt='';img.draggable=false;
    button.append(img);viewport.append(button);
    button.addEventListener('keydown',event=>{if(['Enter',' ','ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key))event.stopPropagation();});
    return button;
  }
  const guide=action('world-kkuwang','메타버스 가이드 꾸왕이에요. 클릭하여 이용안내 열기','assets/guide-kkuwang-refined-v3.png');
  // Render the guide at the display's actual pixel density; avoid repeated CSS crop/resampling.
  const guideImage=guide.querySelector('img');
  const guideCanvas=document.createElement('canvas');guideCanvas.className='world-guide-sprite';guideCanvas.setAttribute('aria-hidden','true');
  let guideBounds=null;
  function paintGuide(){
    if(!guideImage.complete||!guideImage.naturalWidth)return;
    if(!guideBounds){
      const source=document.createElement('canvas');source.width=guideImage.naturalWidth;source.height=guideImage.naturalHeight;
      const sourceContext=source.getContext('2d',{willReadFrequently:true});sourceContext.drawImage(guideImage,0,0);
      const pixels=sourceContext.getImageData(0,0,source.width,source.height).data;
      let left=source.width,top=source.height,right=0,bottom=0;
      for(let y=0;y<source.height;y++)for(let x=0;x<source.width;x++)if(pixels[(y*source.width+x)*4+3]>32){left=Math.min(left,x);right=Math.max(right,x);top=Math.min(top,y);bottom=Math.max(bottom,y);}
      guideBounds={x:left,y:top,w:right-left+1,h:bottom-top+1};
    }
    const density=Math.max(2,Math.min(window.devicePixelRatio||1,3)),width=46,height=54;
    guideCanvas.width=Math.round(width*density);guideCanvas.height=Math.round(height*density);
    const context=guideCanvas.getContext('2d');context.imageSmoothingEnabled=true;context.imageSmoothingQuality='high';
    const padding=2*density,scale=Math.min((guideCanvas.width-padding*2)/guideBounds.w,(guideCanvas.height-padding*2)/guideBounds.h);
    const w=Math.round(guideBounds.w*scale),h=Math.round(guideBounds.h*scale);
    context.drawImage(guideImage,guideBounds.x,guideBounds.y,guideBounds.w,guideBounds.h,Math.round((guideCanvas.width-w)/2),guideCanvas.height-h-padding,w,h);
    if(!guideCanvas.isConnected)guideImage.replaceWith(guideCanvas);
  }
  window.CampusGuideSprite={draw(context){if(!guideBounds)return;const scale=Math.min(38/guideBounds.w,40/guideBounds.h),w=guideBounds.w*scale,h=guideBounds.h*scale;context.imageSmoothingEnabled=true;context.imageSmoothingQuality='high';context.drawImage(guideImage,guideBounds.x,guideBounds.y,guideBounds.w,guideBounds.h,-w/2,-h,w,h);}};
  guideImage.addEventListener('load',paintGuide);if(guideImage.complete)paintGuide();window.addEventListener('resize',paintGuide);
  const balloon=document.createElement('span');balloon.className='world-guide-balloon';balloon.textContent='메타버스 가이드 꾸왕이에요';
  const link=document.createElement('strong');link.textContent='클릭하기 →';balloon.append(link);guide.append(balloon);
  guide.addEventListener('click',()=>{if(!window.CampusApp?.floor||window.CampusGuide?.blocking)return;window.CampusGuide?.open();});
  const wheel=action('world-lunch-wheel','점심추천 돌림판 열기','assets/lunch-wheel-stand.svg');
  const caption=document.createElement('span');caption.textContent='점심추천 돌림판';wheel.append(caption);
  wheel.addEventListener('click',()=>window.dispatchEvent(new CustomEvent('campus:open-service',{detail:'lunch'})));
  const guideObject=window.CampusWorlds.campus.objects.find(item=>item.id==='campus-guide-kkuwang');
  const wheelObject=window.CampusWorlds.rooftop.objects.find(item=>item.id==='roof-lunch-wheel');
  const sites=[];
  if(guideObject)sites.push({button:guide,floor:'campus',x:guideObject.x+guideObject.w/2,y:guideObject.y+guideObject.h/2});
  else guide.remove();
  if(wheelObject)sites.push({button:wheel,floor:'rooftop',x:wheelObject.x+wheelObject.w/2,y:wheelObject.y+wheelObject.h/2});
  else wheel.remove();
  window.CampusWorldActions={update({floor,project,width,height,blocked}){
    for(const item of sites){
      if(floor!==item.floor||blocked){item.button.hidden=true;continue;}
      const p=project(item.x,item.y);item.button.hidden=p.x< -120||p.x>width+120||p.y< -20||p.y>height+150;
      item.button.style.left=Math.round(p.x)+'px';item.button.style.top=Math.round(p.y)+'px';
    }
  }};
})();
