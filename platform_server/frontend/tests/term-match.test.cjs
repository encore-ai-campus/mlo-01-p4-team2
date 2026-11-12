const test=require('node:test'),assert=require('node:assert/strict');
async function setup(){
 const {MemoryGame}=await import('../minigames/term_match/engine.js');
 const {GAME_CONFIG}=await import('../minigames/term_match/config.js');
 let now=0;const deck=[...Array(8).keys(),...Array(8).keys()];
 const game=new MemoryGame({...GAME_CONFIG,deck},{now:()=>now,id:()=> 'play'});
 game.start();now=game.playStarted;game.tick();
 return {game,setTime(t){now=t;},advance(ms){now+=ms;}};
}
test('one 16-card round uses the server deck and excludes preview from the 40 second limit',async()=>{
 const h=await setup(),g=h.game;
 assert.equal(g.config.totalRounds,1);assert.equal(g.config.timeLimitMs,40000);
 assert.equal(g.cards.length,16);assert.equal(g.cards[0].term.id,g.cards[8].term.id);
 for(let i=0;i<8;i++){h.advance(1000);g.choose(i);g.choose(i+8);}
 assert.equal(g.result.success,true);assert.equal(g.result.rounds[0].elapsedMs,8000);
 assert.equal(g.result.picks.length,8);assert.deepEqual(g.result.picks.at(-1),[7,15,8000]);
});
test('deadline and five wrong pairs fail without completing the badge condition',async()=>{
 let h=await setup();h.setTime(h.game.deadline);h.game.tick();assert.equal(h.game.result.success,false);
 assert.equal(h.game.result.rounds[0].elapsedMs,40000);
 h=await setup();for(let i=0;i<5;i++){h.game.choose(0);h.game.choose(1);h.advance(650);h.game.tick();}
 assert.equal(h.game.result.success,false);assert.equal(h.game.result.rounds[0].mistakes,5);
});

for(const elapsed of [20000,39999])test(`pairing remains playable after 11.11 seconds and wins at ${elapsed}ms`,async()=>{
 const h=await setup();
 for(let i=0;i<8;i++){
  h.setTime(h.game.playStarted+Math.floor(elapsed*(i+1)/8));h.game.choose(i);h.game.choose(i+8);
 }
 assert.equal(h.game.result.success,true);assert.equal(h.game.result.rounds[0].elapsedMs,elapsed);
});
