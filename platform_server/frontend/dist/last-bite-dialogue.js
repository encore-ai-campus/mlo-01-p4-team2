/* Local dialogue presentation; server validates proximity, stock and completion. */
(()=>{'use strict';
const art='assets/achievements/kkuwang-last-bite-gift-v1.webp';
function show({player:p,kkuwang:k,claim,continueGuide,show:scene,close}){
 scene('부서지기 직전','HIDDEN STORY · 마지막 한 입',[
 p('아직 한 입… 아니, 한 번은 더 쓸 수 있는데.'),
 k('잠깐. 손에 든 그 조그만 건 뭐야? 설마 아까 그 빼빼로야?',{emotion:'surprised'}),
 k('그걸 언제까지 아껴 먹으려고? 부스러기까지 챙겨 갈 기세네!',{art,role:'잔소리 반, 걱정 반'}),
 p('아직 안 부러졌잖아. 마지막까지 쓰려고 했지.'),
 k('참나… 알았어. 내가 새 빼빼로 하나 줄게. 이번에는 눈치 보지 말고 써!',{art,role:'새 빼빼로를 내미는 꾸왕이'})
 ],[['새 빼빼로 받기',async()=>{const result=await claim();if(!result.eligible){const title=result.already_claimed?'누군가 먼저 발견했어':result.account_limited?'이번 선물은 받을 수 없어':'잠깐, 빼빼로가 달라졌네?';const line=result.already_claimed?'앗, 방금 다른 캠퍼스 친구가 먼저 새 빼빼로를 받았대. 이번 서버의 숨겨진 선물은 한 명만 받을 수 있어.':result.account_limited?'이미 다른 이스터에그를 받았어. 계정당 하나만 받을 수 있어서 이번 선물은 양보해야 해.':'어라? 아까 그 마지막 한 조각은 어디 갔어? 손에 든 걸 다시 확인해 봐.';scene(title,'HIDDEN STORY',[k(line)],[['확인',close]]);return;}
 scene('새 빼빼로를 받았다!','HIDDEN STORY · 선물',[
 k('자, 새것이야. 이번에는 부러질 때까지 혼자 고민하지 말고!',{art,role:'빼빼로 10/10 충전'}),
 p('핀잔을 주는 줄 알았는데… 은근히 잘 챙겨 주네.'),
 k('다 들리거든? 그럼, 아까 하던 이야기 계속할까?',{art})
 ],[['꾸왕이와 계속 대화',continueGuide],['고마워! 캠퍼스로',close]]);
 }],['나중에 받을게',close]]);
}
window.CampusLastBite={show};
})();
