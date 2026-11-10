/* First-visit instructions use example captures and a per-account completion flag. */
(() => {
  'use strict';
  const get = id => document.getElementById(id);
  const dialog = get('campusGuideDialog'), zoom = get('campusGuideImageDialog');
  if (!dialog || !zoom) return;
  const config = JSON.parse(get('campusViewConfig')?.textContent || '{}');
  const steps = [
    {
      category: '이동과 상호작용', title: '캠퍼스에 오신 것을 환영해요', image: 'campus-20261020.png',
      alt: '18층 캠퍼스와 왼쪽 사이드 메뉴의 이동 및 상호작용 안내 예시',
      text: '왼쪽 사이드 메뉴에서 캠퍼스 정보를 확인하고, 캐릭터로 이동하며 채팅·학습·휴식 콘텐츠를 이용해요.',
      items: ['WASD 또는 방향키로 이동해요. 스페이스바로 점프하고, 이동하면서도 뛸 수 있어요.', '시설 가까이에서 E를 눌러 안내를 보거나 상호작용해요.', '가까운 캐릭터를 향해 Z를 누르면 찌르기, 길게 누르면 연속 찌르기가 돼요.'],
      keys: ['WASD 이동', 'Space 점프', 'E 상호작용', 'Z 찌르기'],
    },
    {
      category: '층 이동', title: '18층과 옥상을 오가요', image: 'floor-movement-20261020.png',
      alt: '18층 라운지와 옥상을 연결하는 이동 안내 예시',
      text: '18층 캠퍼스와 옥상 광장은 계단으로 연결돼 있어요. 층이 바뀔 때는 잠시 로딩 화면이 표시돼요.',
      items: ['18층 라운지의 옥상이동 표시 가까이에서 E를 누르거나 연결 계단으로 걸어가면 옥상으로 올라가요.', '18층으로 돌아올 때는 옥상의 18F 포탈 가까이에서 E를 누르세요. 밟기만 하면 이동하지 않아요.', '빠른 이동은 18층 라운지 2 소화기 근처에서 E를 누르세요. 옥상 부스 앞의 18F 포탈 가까이에서 E를 누르면 18층으로 돌아올 수 있어요.'],
      keys: ['계단으로 걸어 이동', '옥상이동 · 18F 포탈', '가까이에서 E'],
    },
    {
      category: '채팅과 게시판', title: '인사하고 소식을 나눠요', image: 'community-20261020.png',
      alt: '왼쪽 사이드 메뉴에서 바로 여는 현재 PLAYDATA 게시판 화면 예시',
      text: '같이 접속한 사람들과 채팅하고, 게시판에서 식단과 커뮤니티 글을 확인해 보세요.',
      items: ['Enter로 채팅을 열고 전송해요. 전송 옆 꾸왕이 버튼에서 이모티콘을 고르면 입력창에 이미지로 표시되고 글과 함께 보낼 수 있어요. Esc로 입력을 끝내요.', '왼쪽 사이드 메뉴에서 PLAYDATA 게시판을 누르면 게시판이 바로 열려요.', '지하1층 구내식당 & Talk에서 식단을 확인하고 의견을 남겨요. 댓글은 Enter로 등록하고 Shift+Enter로 줄을 바꿔요.'],
      keys: ['Enter 채팅', '꾸왕이 이모티콘', 'SNB 게시판 · 구내식당'],
    },
    {
      category: '학습교실', title: '박스 안에서 E를 눌러요', image: 'classroom-20261020.png',
      alt: 'Python 학습교실의 객관식 및 코드 빈칸 학습 영역과 중앙 하단 조작 안내 예시',
      text: 'Python·SQL 학습교실 안의 객관식 또는 코드 빈칸 학습 영역에서 문제 풀이를 시작해요.',
      items: ['18층에서 Python 또는 SQL 학습교실 출입구로 들어가요.', '원하는 학습 유형의 표시된 박스 안으로 이동한 뒤 E를 눌러요.', '나갈 때는 교실 출입구로 이동해 하단에 메타버스로 나가기 안내가 나오면 E를 눌러요.'],
      keys: ['박스 안에서 E 상호작용'],
    },
    {
      category: '문제 풀이', title: '나에게 맞는 난이도로 시작해요', image: 'quiz-20261020.png',
      alt: '초급 코드 빈칸 문제에서 구체적인 요구 동작을 읽고 답을 제출하는 화면 예시',
      text: '객관식·코드 빈칸 유형과 초급·중급·고급 난이도를 고르면 10문제씩 학습해요.',
      items: ['객관식은 보기를 고르고, 코드 빈칸은 답만 입력한 뒤 제출하기를 눌러요. 버튼이 안 보이면 문제창 안을 아래로 스크롤하세요.', '한 문제에 최대 세 번 도전할 수 있어요. Python 답안은 대소문자도 확인해 주세요.', '학습을 마치면 결과와 적립 포인트를 확인하고 틀린 문제를 다시 볼 수 있어요.'],
      keys: ['초급 · 중급 · 고급', '문제당 최대 3회'],
    },
    {
      category: '포인트', title: '정답을 맞히면 포인트가 쌓여요', image: 'points-20261020.png',
      alt: '왼쪽 사이드 메뉴의 보유 포인트와 적립 내역 예시',
      text: '보유 포인트는 왼쪽 사이드 메뉴의 내 캠퍼스 영역에서 확인해요.',
      items: ['기본 적립은 정답 한 문제당 초급 20P, 중급 50P, 고급 80P예요.', '보유 포인트를 누르면 과목·난이도·적립 시각 등 최근 적립 내역이 펼쳐져요.', '더 보기가 표시되면 이전 내역도 확인할 수 있어요. 실제 적립액은 제출 결과를 확인해 주세요.'],
      keys: ['보유 포인트 클릭 → 적립 내역'],
    },
    {
      category: '옥상 부스', title: '잠깐 쉬어가는 시간도 가져요', image: 'rooftop-20261020.png',
      alt: '옥상 광장에 나란히 배치된 유튜브 스튜디오와 심리 테스트 및 게임 부스 예시',
      text: '18층의 옥상이동 포탈이나 연결 계단으로 올라가면 옥상 광장의 부스를 이용할 수 있어요.',
      items: ['유튜브 스튜디오, 심리테스트, 게임 부스 안에서 E를 눌러 콘텐츠를 열어요.', '게임 부스에서는 오목, 테트리스, 죽림고수, 솔리테어 등 16가지 게임을 고를 수 있어요.', '콘텐츠는 별도 창이나 팝업으로 열릴 수 있어요. 열리지 않으면 브라우저의 팝업 차단을 확인해 주세요.'],
      keys: ['부스 안에서 E', '게임 16종'],
    },
    {
      category: '설정과 다시 보기', title: '이제 캠퍼스를 둘러볼까요', image: 'settings-20261020.png',
      alt: '현재 설정 창의 화면 표시 옵션과 내 계정 메뉴 예시',
      text: '왼쪽 사이드 메뉴 아래쪽의 설정 버튼을 누르거나 Esc로 설정을 열어요. 이 안내는 회원가입 후 첫 접속 때 보여요.',
      items: ['다른 사람 닉네임, 채팅창, 말풍선 표시를 각각 켜고 끌 수 있어요.', '설정의 내 계정에서 닉네임을 변경하고, 로그아웃은 왼쪽 사이드 메뉴 맨 아래에서 할 수 있어요.', '안내를 다시 보려면 18층의 메타버스 가이드 꾸왕이 또는 말풍선의 클릭하기를 눌러 주세요.'],
      keys: ['SNB 설정 · Esc', '꾸왕이 클릭 → 이용 안내'],
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
  function source(step) { return new URL(`assets/guide/${step.image}?v=20261020-all-eight-v2`, document.baseURI).href; }
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
    const target = returnFocus?.isConnected && !returnFocus.closest('dialog:not([open])') ? returnFocus : get('mapCanvas');
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
