/* Four-direction sprite sheets: four walking frames, with separate feet-together idle sheets. */
(() => {
  'use strict';
  const definitions = {
    'male-1': {name: '민트 후드', color: '#4e9b91'},
    'ops-gold': {name: '운영팀 딥골드 후드', color: '#b58a45', base: 'male-1', shade: 'deep'},
    'ops-gm1': {name: '운영팀 라이트골드 후드', color: '#b58a45', base: 'male-1', shade: 'light'},
    'ops-gm2': {name: '운영팀 골드 재킷', color: '#b58a45', base: 'male-2'},
    'ops-gf1': {name: '운영팀 골드 후드', color: '#b58a45', base: 'female-1'},
    'ops-gf2': {name: '운영팀 골드 카디건', color: '#b58a45', base: 'female-2'},
    'male-2': {name: '보라색 재킷', color: '#8a78bb'},
    'female-1': {name: '보라색 후드', color: '#cb7773'},
    'female-2': {name: '초록색 카디건', color: '#4e9b91'},
  };
  const images = new Map();
  const guardImages = new Map();
  const idleImages = new Map();
  const rows = {down: 0, left: 1, right: 2, up: 3};
  const frameSize = 128, size = 40;
  const resolve = id => Object.hasOwn(definitions, id) ? id : 'male-1';
  function frameFor(actor, t) {
    const row = rows[actor.dir] ?? 0;
    const elapsed = Number.isFinite(actor.walkTime) ? actor.walkTime * 1000 : t;
    const cadence = actor.running ? 78 : 115;
    const column = actor.moving ? (Number.isInteger(actor.stairFrame) ? actor.stairFrame : Math.floor(elapsed / cadence) % 4) : (row === 1 || row === 2 ? 4 : 1);
    return {row, column};
  }
  const variants = new WeakMap();
  function variantImage(image, avatar) {
    const spec=definitions[avatar];
    if (!spec?.base || !image?.complete || !image.naturalWidth) return image;
    if (variants.get(image)?.has(avatar)) return variants.get(image).get(avatar);
    const canvas=document.createElement('canvas');
    canvas.width=image.naturalWidth;canvas.height=image.naturalHeight;
    const context=canvas.getContext('2d',{willReadFrequently:true});
    context.drawImage(image,0,0);
    const pixels=context.getImageData(0,0,canvas.width,canvas.height),d=pixels.data;
    for(let i=0;i<d.length;i+=4){
      const r=d[i],g=d[i+1],b=d[i+2];
      // Match each outfit's fabric palette, preserving warm skin and brown hair.
      const purple=spec.base==='male-2'||spec.base==='female-1';
      const luminance=.2126*r+.7152*g+.0722*b;
      const frameHeight=image.naturalHeight/(image.src.includes('combo-')?2:4);
      const torso=(Math.floor(i/4/canvas.width)%frameHeight)/frameHeight>.43;
      const fabric=purple ? torso && luminance>32 && b-g>8 && r-g>4 && b>r*.85 : g-r>8 && g>b*.95;
      if(d[i+3] && fabric){
        const light=purple?Math.min(235,luminance*.55+95):luminance;
        const factors=spec.shade==='light'?[1.23,1,.52]:spec.shade==='deep'?[1.05,.74,.25]:[1.13,.85,.35];
        d[i]=Math.min(255,light*factors[0]);d[i+1]=Math.min(255,light*factors[1]);d[i+2]=Math.min(255,light*factors[2]);
      }
    }
    context.putImageData(pixels,0,0);if(!variants.has(image))variants.set(image,new Map());variants.get(image).set(avatar,canvas);return canvas;
  }
  function draw(ctx, actor, t) {
    if(window.CampusSwing?.drawBody(ctx,actor,t))return true;
    const id=resolve(actor.avatar),guarding=actor.guarding===true;
    const idleImage=idleImages.get(id);
    const idle=!actor.moving&&!guarding&&idleImage?.complete&&idleImage.naturalWidth>0;
    const image = guarding ? guardImages.get(id) : idle ? idleImage : images.get(id);
    if (!image?.complete || !image.naturalWidth) return false;
    const frame=frameFor(actor,t),row=frame.row,column=guarding?Math.floor(t/140)%5:idle?0:frame.column;
    const sourceWidth=guarding?image.naturalWidth/5:frameSize,sourceHeight=guarding?image.naturalHeight/4:frameSize;
    ctx.save();
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(variantImage(image,id), column * sourceWidth, row * sourceHeight, sourceWidth, sourceHeight,
      -size / 2, -124 / frameSize * size, size, size);
    ctx.restore();
    return true;
  }
  const load=(im,url)=>window.CampusEntry?window.CampusEntry.image(im,url):(im.src=url);
  for (const id of Object.keys(definitions)) {
    if (definitions[id].base) continue;
    const image = new Image(); images.set(id, image);
    load(image,`assets/characters/${id}-walk-v5.png`);
    const idle=new Image();idleImages.set(id,idle);load(idle,`assets/characters/${id}-idle-v1.png`);
    const guard=new Image();guardImages.set(id,guard);load(guard,`assets/characters/${id}-guard-v1.webp`);
  }
  for(const [id,spec] of Object.entries(definitions)){if(spec.base){images.set(id,images.get(spec.base));idleImages.set(id,idleImages.get(spec.base));guardImages.set(id,guardImages.get(spec.base));}}
  window.CampusAvatars = {variantImage, definitions, ids: Object.keys(definitions), size, resolve, frameFor, draw, images, guardImages, idleImages};
})();
