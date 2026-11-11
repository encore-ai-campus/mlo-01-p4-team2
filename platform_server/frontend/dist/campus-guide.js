/* First-visit instructions use example captures and a per-account completion flag. */
(() => {
  'use strict';
  const get = id => document.getElementById(id);
  const dialog = get('campusGuideDialog'), zoom = get('campusGuideImageDialog');
  if (!dialog || !zoom) return;
  const config = JSON.parse(get('campusViewConfig')?.textContent || '{}');
  const steps = [
    {
      category: '11.11 이벤트 캠퍼스', title: '초콜릿 아틀리에에 오신 것을 환영해요', image: 'pepero-campus-20261023.png',
      alt: '빼빼로 데이로 꾸며진 18층 초콜릿 아틀리에 전체 맵',
      text: '18층과 옥상이 빼빼로 데이 행사장으로 바뀌었어요. 먼저 이동과 메뉴 조작부터 익혀 보세요.',
      items: ['설정한 이동키로 걸어요. 기본 달리기 키 Shift를 함께 누르면 빨리 움직이고 Space로 점프할 수 있어요.', 'Tab 키는 왼쪽 사이드 메뉴를 접고 펼쳐요. 메뉴 맨 위의 빠른 이동 버튼을 누르면 18층 두 곳과 옥상 두 곳 중 목적지를 고를 수 있어요.', '상자·부스·출입구 가까이에서 안내가 뜨면 E로 상호작용해요.'],
      keys: ['Tab 사이드 메뉴', '빠른 이동 버튼', 'E 상호작용'],
    },
    {
      category: '층 이동', title: '18층과 옥상을 오가요', image: 'pepero-rooftop-20261023.png',
      alt: '빼빼로 데이 테마의 옥상 초콜릿 광장과 부스 전체 맵',
      text: '18층 라운지 2의 소화기와 연결 계단, 옥상의 18F 포탈로 층을 오갈 수 있어요.',
      items: ['18층 라운지 2의 소화기 가까이에서 E를 누르고 이동을 확인하면 옥상 왼편 포탈로 이동해요.', '18층의 옥상 연결 계단으로도 올라갈 수 있어요. 옥상에서 18층으로 돌아올 때는 18F 라운지 2 포탈이나 내려가는 계단을 이용해요.', '왼쪽 위 빠른 이동 버튼을 누르면 18층 두 곳과 옥상 두 곳 중 목적지를 바로 선택할 수도 있어요.'],
      keys: ['소화기 앞 E', '옥상 18F 포탈', '빠른 이동 4곳'],
    },
    {
      category: '빼빼로 이벤트', title: '상자에서 꺼내고 휘둘러 보세요', image: 'pepero-refill-20261023.png',
      alt: '빼빼로가 가득 담긴 리필 상자와 방어 자세를 취한 캐릭터',
      text: '행사장 곳곳의 큰 빼빼로 상자에서 빼빼로를 꺼내고, 다른 캐릭터와 가볍게 겨뤄 보세요.',
      items: ['리필 상자 앞에서 E를 누르면 빼빼로가 10/10으로 채워져요. 남은 수량은 화면에 표시돼요.', '기본 찌르기 키 Z를 누르고 있으면 연속으로 휘둘러요. 상대를 맞힐 때마다 1개가 줄고, 빗맞히거나 빈손일 때도 휘두를 수 있어요.', '기본 방어 키 Ctrl을 누르고 있으면 방어막이 유지돼요. 방어 중인 상대를 맞히면 공격자의 빼빼로가 한 번에 부서지므로 상자에서 다시 채워 주세요.'],
      keys: ['E 리필 · 10/10', 'Z 연속 휘두르기', 'Ctrl 방어'],
    },
    {
      category: '학습교실', title: '교실 안의 박스에서 E를 눌러요', image: 'classroom-20261020.png',
      alt: '학습교실 내부의 객관식·코드 빈칸 영역과 조작 안내 예시',
      text: '이벤트 장식이 있는 18층에서도 Python·CS·SQL 학습교실에 들어가 문제를 풀 수 있어요.',
      items: ['18층의 Python·CS·SQL 학습교실 출입구로 들어가요.', '교실 안에서 원하는 학습 유형의 표시된 박스 안으로 이동한 뒤 E를 눌러요.', '나갈 때는 출입구로 돌아가 하단에 메타버스로 나가기 안내가 나타나면 E를 눌러요.'],
      keys: ['교실 출입구', '박스 안에서 E'],
    },
    {
      category: '문제 풀이', title: '유형과 난이도를 골라 시작해요', image: 'quiz-20261020.png',
      alt: '코드 빈칸 문제를 풀고 답을 제출하는 학습 화면 예시',
      text: '객관식·코드 빈칸 유형과 초급·중급·고급 난이도를 선택하면 10문제씩 학습해요.',
      items: ['객관식은 보기를 고르고, 코드 빈칸은 빈칸에 들어갈 답만 입력해요. 제출 버튼이 보이지 않으면 문제창 안을 아래로 스크롤하세요.', '한 문제에 최대 세 번 도전할 수 있어요. Python 답안은 대소문자도 확인해 주세요.', '학습을 마치면 결과와 적립 포인트를 확인하고 틀린 문제를 다시 볼 수 있어요.'],
      keys: ['10문제', '문제당 최대 3회'],
    },
    {
      category: '포인트', title: '학습 포인트를 확인해요', image: 'points-20261020.png',
      alt: '사이드 메뉴에 표시된 보유 포인트와 적립 내역 예시',
      text: '정답을 맞혀 쌓은 포인트는 왼쪽 사이드 메뉴의 내 계정 영역에서 볼 수 있어요.',
      items: ['기본 적립은 정답 한 문제당 초급 20P, 중급 50P, 고급 80P예요.', '보유 포인트를 누르면 과목·난이도·적립 시각 등 최근 적립 내역이 펼쳐져요.', '더 보기가 표시되면 이전 내역도 확인할 수 있어요. 실제 적립액은 제출 결과를 확인해 주세요.'],
      keys: ['보유 포인트 클릭 → 내역'],
    },
    {
      category: '옥상 부스', title: '초콜릿 광장의 부스를 둘러봐요', image: 'pepero-rooftop-20261023.png',
      alt: '빼빼로 데이 테마로 꾸며진 옥상 부스와 중앙 초콜릿 광장',
      text: '옥상에는 빼빼로 데이 장식과 함께 게임·심리 테스트·유튜브 부스가 있어요.',
      items: ['부스 안에서 E를 눌러 콘텐츠를 열어요.', '게임 부스에서 원하는 게임을 고를 수 있어요. 콘텐츠에 따라 새 창이나 팝업이 열립니다.', '중앙 광장에도 리필 상자가 있어요. 빼빼로가 떨어졌다면 상자 앞으로 가서 E를 눌러요.'],
      keys: ['부스 안에서 E', '옥상에서도 리필'],
    },
    {
      category: '채팅과 게시판', title: '함께 접속한 사람들과 소통해요', image: 'community-20261020.png',
      alt: '사이드 메뉴에서 게시판을 열고 채팅하는 화면 예시',
      text: '이벤트 맵에서도 채팅과 게시판, 구내식당 메뉴를 그대로 이용할 수 있어요.',
      items: ['Enter로 채팅을 열고 전송해요. Esc로 채팅 입력을 끝낼 수 있어요.', '왼쪽 사이드 메뉴의 PLAYDATA 게시판을 누르면 게시판이 열려요.', '구내식당 · 식단톡에서는 식단을 확인하고 의견을 남길 수 있어요.'],
      keys: ['Enter 채팅', 'Tab 사이드 메뉴', '게시판 · 식단톡'],
    },
    {
      category: '설정과 다시 보기', title: '원하는 키와 화면으로 즐겨요', image: 'settings-20261020.png',
      alt: '설정 창의 키보드 조작과 화면 표시 옵션 예시',
      text: '왼쪽 사이드 메뉴 아래쪽의 설정 버튼이나 Esc로 설정을 열 수 있어요.',
      items: ['설정에서 이동·달리기·점프·휘두르기·방어 키를 원하는 키로 바꿀 수 있어요. 화면 표시 옵션도 조정할 수 있어요.', '사이드 메뉴의 내 계정에서 닉네임을 확인하고, 맨 아래에서 로그아웃할 수 있어요.', '이 안내서는 회원가입 후 첫 접속 때 나타나요. 나중에는 사이드 메뉴의 처음 이용 안내 다시 보기를 눌러 다시 볼 수 있어요.'],
      keys: ['Esc 설정', '키보드 설정', '안내 다시 보기'],
    },
  ];
  let index = 0, returnFocus = null, saving = false, pending = config.guidePending === true;
  const dots = get('campusGuideSteps');
  steps.forEach((step, number) => {
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'campus-guide-dot';
    button.setAttribute('aria-label', `${number + 1}단계 ${step.category}`);
    button.addEventListener('click', () => show(number));
    dots.append(button);
  });
  function source(step) { return new URL(`assets/guide/${step.image}?v=20261023-pepero-refill-label`, document.baseURI).href; }
  function show(number) {
    index = Math.max(0, Math.min(number, steps.length - 1));
    const step = steps[index], image = get('campusGuideImage');
    get('campusGuideCategory').textContent = step.category;
    get('campusGuideTitle').textContent = step.title;
    get('campusGuideText').textContent = step.text;
    get('campusGuideCounter').textContent = `${index + 1} / ${steps.length}`;
    get('campusGuideItems').replaceChildren(...step.items.map(text => { const item = document.createElement('li'); item.textContent = text; return item; }));
    get('campusGuideKeys').replaceChildren(...step.keys.map(text => { const item = document.createElement('span'); item.textContent = text; return item; }));
    image.alt = step.alt; image.src = source(step); image.hidden = false;
    get('campusGuideImageError').hidden = true;
    get('campusGuideEnlarge').disabled = false;
    get('campusGuideBack').disabled = index === 0;
    get('campusGuideNext').textContent = index === steps.length - 1 ? '캠퍼스 시작하기' : '다음';
    [...dots.children].forEach((button, number) => {
      if (number === index) button.setAttribute('aria-current', 'step'); else button.removeAttribute('aria-current');
    });
    get('campusGuideScroll').scrollTop = 0;
    if (dialog.open) get('campusGuideTitle').focus({preventScroll: true});
  }
  function open() {
    if (dialog.open || window.CampusSession?.ending) return;
    pending = false;
    returnFocus = document.activeElement;
    const settings = get('settingsDialog'); if (settings?.open) settings.close();
    show(0);
    window.dispatchEvent(new Event('campus:guide-open'));
    dialog.showModal(); get('campusGuideTitle').focus({preventScroll: true});
  }
  async function finish() {
    if (zoom.open) zoom.close();
    dialog.close();
    const target = get('mapCanvas');
    target?.focus({preventScroll: true});
    window.dispatchEvent(new Event('campus:guide-close'));
    if (saving || !config.guideCompleteUrl) return;
    saving = true;
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 10000);
    try {
      const policy = JSON.parse(get('campusSessionPolicyConfig')?.textContent || '{}');
      const response = await fetch(config.guideCompleteUrl, {method: 'POST', credentials: 'same-origin', cache: 'no-store',
        headers: {'X-CSRFToken': policy.csrfToken || ''}, signal: controller.signal});
      if (!response.ok || response.redirected || (await response.json()).guideCompleted !== true) throw new Error('Guide completion unavailable');
      config.guidePending = false;
    } catch {
      window.CampusApp?.toast('안내 확인 상태를 저장하지 못했어요. 다음 접속 때 다시 표시될 수 있어요.');
    } finally { clearTimeout(timer); saving = false; }
  }
  get('campusGuideImage').addEventListener('error', () => {
    get('campusGuideImage').hidden = true; get('campusGuideImageError').hidden = false;
    get('campusGuideEnlarge').disabled = true;
  });
  get('campusGuideOpen')?.addEventListener('click', open);
  get('campusGuideBack').addEventListener('click', () => show(index - 1));
  get('campusGuideNext').addEventListener('click', () => index === steps.length - 1 ? finish() : show(index + 1));
  get('campusGuideSkip').addEventListener('click', finish);
  dialog.addEventListener('cancel', event => { event.preventDefault(); finish(); });
  dialog.addEventListener('keydown', event => {
    event.stopPropagation();
    if (event.altKey || event.ctrlKey || event.metaKey || event.isComposing) return;
    if (event.key === 'ArrowRight') { event.preventDefault(); if (index < steps.length - 1) show(index + 1); }
    if (event.key === 'ArrowLeft') { event.preventDefault(); show(index - 1); }
  });
  get('campusGuideEnlarge').addEventListener('click', () => {
    const image = get('campusGuideLargeImage'); image.src = source(steps[index]); image.alt = steps[index].alt;
    get('campusGuideLargeTitle').textContent = steps[index].category + ' 화면 예시';
    zoom.showModal(); get('campusGuideImageClose').focus({preventScroll: true});
  });
  get('campusGuideImageClose').addEventListener('click', () => zoom.close());
  zoom.addEventListener('keydown', event => event.stopPropagation());
  zoom.addEventListener('close', () => { if (dialog.open) get('campusGuideEnlarge').focus({preventScroll: true}); });
  window.addEventListener('campus:ready', () => {
    if (config.guidePending === true) requestAnimationFrame(() => open());
  }, {once: true});
  window.CampusGuide = {open, get pending() { return pending; }, get blocking() { return dialog.open || zoom.open; }};
})();
