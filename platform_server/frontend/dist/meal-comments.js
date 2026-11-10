(() => {
  'use strict';
  const $=id=>document.getElementById(id),form=$('mealCommentForm');if(!form)return;
  let date='',page=1,pages=1,editing=null,busy=false,loadVersion=0;
  const status=(message='',error=false)=>{const el=$('mealCommentsStatus');el.textContent=message;el.dataset.error=String(error);};
  const element=(tag,text)=>{const el=document.createElement(tag);if(text!==undefined)el.textContent=text;return el;};
  async function api(path='',method='GET',body){
    const headers={Accept:'application/json'};
    if(method!=='GET'){
      headers['X-CSRFToken']=document.querySelector('#campusLogoutForm input[name="csrfmiddlewaretoken"]')?.value||'';
      headers['Content-Type']='application/json';
    }
    const response=await fetch('/api/community/meal-comments/'+path,{method,credentials:'same-origin',cache:'no-store',headers,...(body===undefined?{}:{body:JSON.stringify(body)})});
    if(response.redirected||!response.headers.get('content-type')?.includes('application/json'))throw Error('로그인 상태를 확인하고 다시 시도해 주세요.');
    const data=await response.json();if(!response.ok)throw Error(data.error||'댓글 요청을 처리하지 못했습니다.');return data;
  }
  function reset(){editing=null;form.reset();$('mealCommentSubmit').textContent='댓글 등록';$('mealCommentCancel').hidden=true;}
  function setBusy(value){busy=value;form.querySelectorAll('button,textarea').forEach(el=>el.disabled=value);$('mealCommentsList').querySelectorAll('button').forEach(el=>el.disabled=value);}
  function action(text,handler){const button=element('button',text);button.type='button';button.onclick=()=>{if(!busy)handler();};return button;}
  function render(data){
    date=data.date;page=data.pagination.page;pages=data.pagination.pages;
    $('mealCommentsDate').textContent=date+' 식단 · 댓글 '+data.pagination.total+'개';$('mealCommentsPage').textContent=page+' / '+pages;
    $('mealCommentsPrev').disabled=page<=1;$('mealCommentsNext').disabled=page>=pages;$('mealCommentsPage').closest('nav').hidden=pages<=1;
    const list=$('mealCommentsList');list.replaceChildren();
    list.hidden=!data.comments.length;
    for(const item of data.comments){
      const row=element('li');row.className='meal-comment';const heading=element('div');heading.className='meal-comment-heading';
      heading.append(element('strong',item.author.nickname),element('time',new Date(item.createdAt).toLocaleString('ko-KR')));row.append(heading,element('p',item.body));
      const actions=element('div');actions.className='meal-comment-actions';
      if(item.canEdit)actions.append(action('수정',()=>{editing=item;$('mealCommentBody').value=item.body;$('mealCommentSubmit').textContent='수정 저장';$('mealCommentCancel').hidden=false;$('mealCommentBody').focus();}));
      if(item.canDelete)actions.append(action('삭제',async()=>{
        if(!confirm('댓글을 삭제할까요?'))return;setBusy(true);
        try{await api(item.id+'/','DELETE');if(editing?.id===item.id)reset();await load(page);status('댓글을 삭제했습니다.');}catch(error){status(error.message,true);}finally{setBusy(false);}
      }));row.append(actions);list.append(row);
    }
  }
  async function load(next=1,fresh=false){
    const version=++loadVersion;status('댓글을 불러오는 중…');
    try{const query=new URLSearchParams({page:String(next)});if(date&&!fresh)query.set('date',date);const data=await api('?'+query);if(version!==loadVersion)return;if(date&&date!==data.date)reset();render(data);status();return true;}
    catch(error){if(version===loadVersion)status(error.message,true);return false;}
  }
  form.addEventListener('submit',async event=>{
    event.preventDefault();if(busy)return;const body=$('mealCommentBody').value.trim();if(!body){status('댓글을 입력해 주세요.',true);return;}
    setBusy(true);const current=editing;
    try{await api(current?current.id+'/':'?'+new URLSearchParams({date} ),current?'PATCH':'POST',{body,...(current?{version:current.updatedAt}:{})});reset();const ok=await load(current?page:100000);if(ok)status(current?'댓글을 수정했습니다.':'댓글을 등록했습니다.');}
    catch(error){status(error.message,true);}finally{setBusy(false);}
  });
  $('mealCommentBody').addEventListener('keydown',event=>{
    if(event.key!=='Enter'||event.shiftKey||event.isComposing||event.keyCode===229)return;
    event.preventDefault();event.stopPropagation();
    if(!busy&&!event.repeat)form.requestSubmit();
  });
  $('mealCommentCancel').onclick=reset;
  $('mealCommentsPrev').onclick=()=>{if(!busy)load(page-1);};$('mealCommentsNext').onclick=()=>{if(!busy)load(page+1);};
  window.CampusMealComments={open(){if(!busy)load(1,true);}};
})();
