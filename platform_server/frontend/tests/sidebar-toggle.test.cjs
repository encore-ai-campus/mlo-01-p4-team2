const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const root=path.resolve(__dirname,'..'),source=fs.readFileSync(root+'/dist/campus-snb.js','utf8');
function setup(saved=null){
 class Element{constructor(tag='div'){this.tagName=tag.toUpperCase();this.attributes={};this.handlers={};this.dataset={};}setAttribute(k,v){this.attributes[k]=v;}append(){}prepend(){}addEventListener(k,fn){this.handlers[k]=fn;}focus(){this.focused=true;}closest(){return this.editing||false;}}
 const panel=new Element(),shell=new Element(),canvas=new Element('canvas'),events={};let toggle,closed=0,value=saved,modal=false;
 const document={createElement(tag){const e=new Element(tag);if(tag==='button')toggle=e;return e;},getElementById:()=>canvas,querySelector:()=>modal?{}:null,addEventListener:(k,fn)=>events[k]=fn};
 const context=vm.createContext({document,Element,panel,shell,innerWidth:1440,window:{CampusApp:{quickTravelMenu:{close(){closed++;}}}},localStorage:{getItem:()=>value,setItem:(_k,v)=>value=v}});
 const begin=source.indexOf('  const toggle='),end=source.lastIndexOf('})();');assert.ok(begin>=0&&end>begin);vm.runInContext(source.slice(begin,end),context);
 return {panel,shell,canvas,get toggle(){return toggle;},get saved(){return value;},get closed(){return closed;},set modal(v){modal=v;},key(extra={}){const e={key:'Tab',target:canvas,preventDefault(){this.prevented=true;},...extra};events.keydown(e);return e;}};
}
test('sidebar click closes and reopens a real button and synchronizes focus, aria and saved state',()=>{
 const f=setup();assert.equal(f.toggle.tagName,'BUTTON');assert.equal(f.toggle.type,'button');assert.equal(f.shell.dataset.snbOpen,'true');
 f.toggle.handlers.click();assert.equal(f.shell.dataset.snbOpen,'false');assert.equal(f.toggle.attributes['aria-expanded'],'false');assert.equal(f.panel.inert,true);assert.equal(f.saved,'false');assert.equal(f.closed,1);assert.equal(f.canvas.focused,true);
 f.toggle.handlers.click();assert.equal(f.shell.dataset.snbOpen,'true');assert.equal(f.panel.inert,false);assert.equal(f.saved,'true');
 assert.match(fs.readFileSync(root+'/dist/campus-snb.css','utf8'),/campus-snb-toggle\{pointer-events:auto;cursor:pointer/);
});
test('Tab toggles once while editing, modal dialogs, Shift Tab and key repeats stay protected',()=>{
 const f=setup('false');f.key();assert.equal(f.shell.dataset.snbOpen,'true');f.key({repeat:true});assert.equal(f.shell.dataset.snbOpen,'true');
 f.key({shiftKey:true});assert.equal(f.shell.dataset.snbOpen,'true');f.canvas.editing=true;f.key();assert.equal(f.shell.dataset.snbOpen,'true');f.canvas.editing=false;
 f.modal=true;f.key();assert.equal(f.shell.dataset.snbOpen,'true');f.modal=false;f.key();assert.equal(f.shell.dataset.snbOpen,'false');
});
test('native button activation does not reach movement or chat key handlers',()=>{
 const f=setup();for(const key of ['Enter',' ']){let stopped=false;f.toggle.handlers.keydown({key,stopPropagation(){stopped=true;}});assert.equal(stopped,true);}
});
