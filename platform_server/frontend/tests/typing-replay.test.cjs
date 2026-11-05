const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=path.resolve(__dirname,'..');
const html=fs.readFileSync(root+'/dist/minigames/typing.html','utf8');
const app=[...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].at(-1)[1];

function fixture(embedded=true){
 let now=0,interval;const elements=new Map(),results=[],restarts=[];
 function element(){
  const handlers=new Map();
  return {value:'',disabled:false,hidden:false,children:[],dataset:{},style:{setProperty(){}},classList:{add(){},toggle(){}},
   firstElementChild:{style:{}},selectionStart:0,selectionEnd:0,
   addEventListener(type,fn){if(!handlers.has(type))handlers.set(type,new Set());handlers.get(type).add(fn);},
   removeEventListener(type,fn){handlers.get(type)?.delete(fn);},
   emit(type,extra={}){const event={key:'',code:'',cancelable:true,preventDefault(){},...extra};for(const fn of handlers.get(type)||[])fn(event);},
   click(){if(!this.disabled)this.emit('click');},focus(){},append(...items){this.children.push(...items);},
   replaceChildren(...items){this.children=items;},setAttribute(){},removeAttribute(){},toggleAttribute(){},
   setSelectionRange(start,end){this.selectionStart=start;this.selectionEnd=end;},
   querySelector(){return null;},querySelectorAll(){return [];},
  };
 }
 const get=id=>{if(!elements.has(id))elements.set(id,element());return elements.get(id);};
 get('mode').value='natural';get('minimum-accuracy').value='95';get('result').hidden=true;
 get('guide-body').querySelector=()=>get('instruction');
 const channel={start(fn){fn({minimumAccuracy:95,timeLimitMs:11110},'first-play');},
  complete(result){results.push(JSON.parse(JSON.stringify(result)));},restart(){restarts.push('restart');},exit(){}};
 vm.runInNewContext(app,{window:{minigameChannel:embedded?channel:undefined,addEventListener(){}},
  document:{getElementById:get,createElement:element},performance:{now:()=>now},crypto:{randomUUID:()=> 'id'},queueMicrotask,
  setInterval(fn){interval=fn;return 1;},clearInterval(){interval=null;},requestAnimationFrame(){} });
 function press(char){const input=get('input');input.emit('keydown',{key:char==='\n'?'Enter':char});input.emit('keyup');}
 function code(){return get('code').children.flatMap(line=>line.children[1].children).map(char=>char.dataset.char).join('');}
 return {get,results,restarts,press,set now(value){now=value;},tick(){interval?.();},
  finish(){const text=code();press(text[0]);now=10000;for(const char of text.slice(1))press(char);}};
}

test('completed typing exposes replay and requests a new platform play without resetting its saved result',()=>{
 const f=fixture();f.finish();
 assert.deepEqual(f.results,[{duration_ms:10000,status:'SUCCESS'}]);
 assert.equal(f.get('repeat-actions').hidden,false);
 assert.equal(f.get('next').hidden,true);
 assert.equal(f.get('restart').disabled,false);
 f.get('repeat').click();assert.equal(f.restarts.length,1);
 assert.equal(f.get('input').disabled,true);assert.equal(f.results.length,1);
});

test('timed-out typing can request replay from the restart button',()=>{
 const f=fixture();f.press('p');f.now=11111;f.tick();
 assert.deepEqual(f.results,[{duration_ms:11111,status:'FAILED'}]);
 assert.equal(f.get('restart').disabled,false);
 f.get('restart').click();assert.equal(f.restarts.length,1);assert.equal(f.results.length,1);
});

test('standalone typing replay clears the completed attempt and restores input',()=>{
 const f=fixture(false);f.finish();assert.equal(f.get('result').hidden,false);
 f.get('repeat').click();assert.equal(f.get('result').hidden,true);
 assert.equal(f.get('input').disabled,false);assert.equal(f.get('input').value,'');
 assert.equal(f.get('metric-time').textContent,'00:00.00');
 assert.equal(f.results.length,0);assert.equal(f.restarts.length,0);
});
