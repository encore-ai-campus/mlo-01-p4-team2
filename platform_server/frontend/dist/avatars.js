/* Four-direction sprite sheets: four walking frames, with separate feet-together idle sheets. */
(() => {
  'use strict';
  const definitions = {
    'male-1': {name: '민트 후드', color: '#4e9b91'},
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
    ctx.drawImage(image, column * sourceWidth, row * sourceHeight, sourceWidth, sourceHeight,
      -size / 2, -124 / frameSize * size, size, size);
    ctx.restore();
    return true;
  }
  const load=(im,url)=>window.CampusEntry?window.CampusEntry.image(im,url):(im.src=url);
  for (const id of Object.keys(definitions)) {
    const image = new Image(); images.set(id, image);
    load(image,`assets/characters/${id}-walk-v5.png`);
    const idle=new Image();idleImages.set(id,idle);load(idle,`assets/characters/${id}-idle-v1.png`);
    const guard=new Image();guardImages.set(id,guard);load(guard,`assets/characters/${id}-guard-v1.webp`);
  }
  window.CampusAvatars = {definitions, ids: Object.keys(definitions), size, resolve, frameFor, draw, images, guardImages, idleImages};
})();
