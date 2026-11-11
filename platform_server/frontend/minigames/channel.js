(() => {
 const gameId='__GAME_ID__',playId=window.frameElement?.dataset.sessionId;
 if(window.parent===window||!playId)return;
 const origin=window.parent.location.origin;
 let started=false,finished=false,callback;
 const send=data=>window.parent.postMessage({version:1,gameId,playId,...data},origin);
 window.addEventListener('message',event=>{
  const d=event.data;
  if(event.source!==window.parent||event.origin!==origin||!d||d.type!=='minigame:init'||d.playId!==playId||d.gameId!==gameId||d.version!==1||started||!callback)return;
  try{callback(d.config,playId);started=true;}catch{send({type:'minigame:error'});}
 });
 window.minigameChannel=Object.freeze({
  start(fn){callback=fn;send({type:'minigame:ready'});},
  complete(result){if(!started||finished)return;finished=true;send({type:'minigame:result',result});},
  restart(){if(started&&finished)send({type:'minigame:restart'});},
  exit(){send({type:'minigame:exit'});}
 });
})();
