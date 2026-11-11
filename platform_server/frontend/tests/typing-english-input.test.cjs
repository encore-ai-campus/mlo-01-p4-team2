const test=require('node:test'),assert=require('node:assert/strict');

async function fixture(){
 const {attachTypingInput}=await import('../minigames/typing/src/adapters/nativeTypingInput.js');
 const listeners=new Map(),actions=[];let value='',escapes=0;
 const element={value:'',selectionStart:0,selectionEnd:0,
  addEventListener(type,fn){if(!listeners.has(type))listeners.set(type,new Set());listeners.get(type).add(fn);},
  removeEventListener(type,fn){listeners.get(type)?.delete(fn);},
  setSelectionRange(start,end){this.selectionStart=start;this.selectionEnd=end;},
 };
 const detach=attachTypingInput(element,action=>{
  actions.push(action);value=action.type==='backspace'?value.slice(0,-1):value+action.text;
 },()=>value,()=>escapes++);
 function emit(type,extra={}){
  const event={key:'',code:'',cancelable:true,isComposing:false,keyCode:0,shiftKey:false,metaKey:false,ctrlKey:false,altKey:false,
   ...extra,prevented:false,preventDefault(){if(this.cancelable)this.prevented=true;},getModifierState(name){return name==='CapsLock'&&!!extra.caps;}};
  for(const fn of listeners.get(type)||[])fn(event);
  return event;
 }
 return {element,actions,listeners,detach,emit,get value(){return value;},get escapes(){return escapes;},
  press(key,code='',extra={}){const event=emit('keydown',{key,code,...extra});emit('keyup',{key,code});return event;}};
}

test('physical letter positions produce English through Korean and Process key events',async()=>{
 const f=await fixture();
 for(const [key,code] of [['ㅂ','KeyQ'],['ㅈ','KeyW'],['Process','KeyE'],['ㄱ','KeyR']])
  assert.equal(f.press(key,code,{isComposing:true,keyCode:229}).prevented,true);
 assert.equal(f.value,'qwer');assert.equal(f.element.value,'qwer');
});

test('English letters honor Shift, Caps Lock and their combined state',async()=>{
 const f=await fixture();
 for(const flags of [{},{shiftKey:true},{caps:true},{shiftKey:true,caps:true}])f.press('ㅁ','KeyA',flags);
 assert.equal(f.value,'aAAa');
});

test('number and punctuation keys use the same QWERTY symbols as the keyboard guide',async()=>{
 const {englishKey}=await import('../minigames/typing/src/core/keyboard.js');
 const pairs={Backquote:'`~',Digit1:'1!',Digit2:'2@',Digit3:'3#',Digit4:'4$',Digit5:'5%',Digit6:'6^',Digit7:'7&',Digit8:'8*',Digit9:'9(',Digit0:'0)',
  Minus:'-_',Equal:'=+',BracketLeft:'[{',BracketRight:']}',Backslash:'\\|',Semicolon:';:',Quote:'\'"',Comma:',<',Period:'.>',Slash:'/?',Space:'  '};
 for(const [code,[base,shifted]] of Object.entries(pairs)){
  assert.equal(englishKey({code,shiftKey:false}),base,code);
  assert.equal(englishKey({code,shiftKey:true}),shifted,code+' with Shift');
 }
 const f=await fixture();f.press('Process','KeyP');f.press('Process','Digit0',{shiftKey:true});f.press('Process','BracketLeft',{shiftKey:true});
 f.press('₩','Backslash');assert.equal(f.value,'p){\\');
});

for(const committed of ['ㅂ','q'])test(`composition echo ${committed} does not duplicate a physical English key`,async()=>{
 const f=await fixture();
 f.emit('compositionstart');
 f.emit('keydown',{key:'Process',code:'KeyQ',keyCode:229,isComposing:true});
 f.emit('beforeinput',{inputType:'insertCompositionText',data:committed,isComposing:true});
 f.element.value='q'+committed;f.emit('input',{inputType:'insertCompositionText',data:committed,isComposing:true});
 f.emit('keyup',{key:'Process',code:'KeyQ'});
 f.emit('compositionend',{data:committed});
 f.emit('beforeinput',{inputType:'insertText',data:committed});
 f.element.value='q'+committed;f.emit('input',{inputType:'insertText',data:committed});
 assert.equal(f.value,'q');assert.equal(f.element.value,'q');
});

test('native input echo is discarded while a handled physical key is held',async()=>{
 const f=await fixture();f.emit('keydown',{key:'a',code:'KeyA'});
 f.emit('beforeinput',{inputType:'insertText',data:'a'});
 f.element.value='aa';f.emit('input',{inputType:'insertText',data:'a'});
 assert.equal(f.value,'a');assert.equal(f.element.value,'a');
 f.emit('keyup');f.emit('beforeinput',{inputType:'insertText',data:'b'});
 assert.equal(f.value,'ab');
});

test('native English input remains available when physical key codes are missing',async()=>{
 const f=await fixture();
 f.emit('beforeinput',{inputType:'insertText',data:'p'});
 f.element.value='py';f.emit('input',{inputType:'insertText',data:'y',cancelable:false});
 assert.equal(f.value,'py');
 f.emit('compositionstart');f.emit('compositionend',{data:'thon'});
 f.emit('beforeinput',{inputType:'insertText',data:'thon'});
 assert.equal(f.value,'python');
 await Promise.resolve();f.press('n');assert.equal(f.value,'pythonn');
});

test('Korean and other non-ASCII native edits do not enter the game or start its timer',async()=>{
 const f=await fixture();
 f.press('한');f.emit('beforeinput',{inputType:'insertText',data:'글'});
 f.element.value='한글';f.emit('input',{inputType:'insertText',data:'한글',cancelable:false});
 f.emit('compositionstart');f.emit('compositionend',{data:'한'});
 assert.equal(f.actions.length,0);assert.equal(f.value,'');assert.equal(f.element.value,'');
});

test('editing keys, indentation, keyboard focus escape and copy shortcuts keep working',async()=>{
 const f=await fixture();f.press('a','KeyA');f.press('Process','Tab');f.press('Process','Enter');f.press('Process','Backspace');
 assert.equal(f.value,'a    ');
 assert.equal(f.press('Tab','Tab',{shiftKey:true}).prevented,false);
 assert.equal(f.press('c','KeyC',{ctrlKey:true}).prevented,false);
 for(const flags of [{ctrlKey:true},{metaKey:true},{altKey:true}])f.press('Process','KeyQ',flags);
 assert.equal(f.value,'a    ');
 assert.equal(f.press('v','KeyV',{metaKey:true}).prevented,true);
 f.press('Escape','Escape');assert.equal(f.escapes,1);
});

test('paste, drop and history edits remain blocked and all listeners detach',async()=>{
 const f=await fixture();
 for(const type of ['paste','drop','cut','dragstart'])assert.equal(f.emit(type).prevented,true);
 f.element.value='pasted code';f.emit('input',{inputType:'insertFromPaste',data:'pasted code'});
 assert.equal(f.value,'');assert.equal(f.element.value,'');
 f.detach();assert.ok([...f.listeners.values()].every(set=>set.size===0));
 f.press('a','KeyA');assert.equal(f.value,'');
});

test('noncancelable keydown waits for native English input rather than double typing',async()=>{
 const f=await fixture();f.press('a','KeyA',{cancelable:false});assert.equal(f.value,'');
 f.emit('beforeinput',{inputType:'insertText',data:'a',cancelable:false});
 f.element.value='a';f.emit('input',{inputType:'insertText',data:'a'});
 assert.equal(f.value,'a');
});

test('only accepted English input starts the attempt clock and keeps its deadline',async()=>{
 const {attachTypingInput}=await import('../minigames/typing/src/adapters/nativeTypingInput.js');
 const {AttemptController}=await import('../minigames/typing/src/core/attemptController.js');
 const f=await fixture();f.detach();let now=60000;
 const controller=new AttemptController({id:'english',version:1,language:'python',difficulty:'beginner',code:'qa'},'natural',
  {emit(){}},{timeLimitMs:11110},'play',{monotonic:()=>now,utc:()=>new Date(0).toISOString()});
 const detach=attachTypingInput(f.element,controller.input,()=>controller.getSnapshot().slots.map(s=>s.typed).join(''),()=>{});
 f.emit('compositionstart');f.emit('compositionend',{data:'한'});
 assert.equal(controller.getSnapshot().status,'idle');assert.equal(controller.metrics().elapsed_ms,0);
 f.press('Process','KeyQ',{isComposing:true,keyCode:229});
 assert.equal(f.element.value,'q');assert.equal(controller.getSnapshot().status,'running');
 now=71111;f.press('ㅁ','KeyA',{isComposing:true,keyCode:229});
 assert.equal(controller.expired,true);assert.equal(f.element.value,'q');detach();
});
