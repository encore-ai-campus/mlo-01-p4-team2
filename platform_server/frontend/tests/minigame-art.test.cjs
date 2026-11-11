const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
function fixture(){
 const window={CAMPUS_SEASON:'pepero',dispatchEvent(){}};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../dist/pepero-theme.js'),'utf8'),{window,Image:class{},matchMedia:()=>({matches:true})});
 return window.CampusSeasonal;
}
test('an unavailable minigame image permits the renderer fallback instead of hiding the station',()=>{
 const art=fixture();
 for(const key of ['pairing-garden-east','unknown-new-asset']){
  assert.equal(art.paint({seasonalArt:key,action:'minigame'},{}),false);
 }
 assert.equal(art.paint({seasonalArt:'gifts'},{}),true);
});
test('pairing garden loads its own image and paints that image when ready',()=>{
 const art=fixture(),img=art.art['pairing-garden-east'];
 assert.equal(img.src,'assets/minigames/pairing-garden-east-v1.png');
 Object.assign(img,{complete:true,naturalWidth:606,naturalHeight:640});
 let painted;
 const g={save(){},restore(){},translate(){},transform(){},drawImage(image){painted=image;}};
 assert.equal(art.paint({seasonalArt:'pairing-garden-east',action:'minigame',x:0,y:0,w:54,h:74,artWidth:112,artHeight:146},{g,p:()=>({x:0,y:0})}),true);
 assert.equal(painted,img);
});
