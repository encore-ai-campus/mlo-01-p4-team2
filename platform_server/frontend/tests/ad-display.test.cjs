const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');

test('광고 브리지의 Canvas 클릭도 새 탭 POST로 서버 서명을 전달한다',()=>{
  const submitted=[],context={document:{cookie:'campus_csrftoken=campus-token',querySelector:()=>null,
    body:{append(){}},createElement:()=>({children:[],append(child){this.children.push(child);},
      submit(){submitted.push(this);},remove(){this.removed=true;}})}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../../backend/ad_bridge/static/ad_bridge/display.js'),'utf8'),context);
  const display=new context.AdBridgeDisplay();display.click();assert.equal(submitted.length,0);
  display.current={decision:{decision_id:'decision',ad:{click_token:'signed-token'}}};display.click();
  const form=submitted[0];
  assert.equal(form.action,'/api/ads/decisions/decision/click/');assert.equal(form.method,'POST');
  assert.equal(form.target,'_blank');assert.equal(form.rel,'noopener');
  assert.equal(form.children.find(input=>input.name==='token').value,'signed-token');
  assert.equal(form.children.find(input=>input.name==='csrfmiddlewaretoken').value,'campus-token');
  assert.equal(form.removed,true);
});
