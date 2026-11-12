/* First-visit instructions use example captures and a per-account completion flag. */
(() => {
  'use strict';
  const get = id => document.getElementById(id);
  const dialog = get('campusGuideDialog'), zoom = get('campusGuideImageDialog');
  if (!dialog || !zoom) return;
  const config = JSON.parse(get('campusViewConfig')?.textContent || '{}');
  const steps = [
  {
    "category": "이벤트 캠퍼스 시작",
    "title": "탐정 꾸왕이와 사건을 해결해요",
    "image": "event-campus-20261105.webp",
    "alt": "현재 18층 이벤트맵과 사이드 메뉴, 조작키 안내",
    "text": "빼빼로 데이 캠퍼스에 오신 걸 환영해요. 18층과 옥상을 탐험하며 사라진 선물의 행방을 찾아보세요.",
    "items": [
      "기본 이동은 WASD 또는 방향키예요. Shift를 함께 누르면 달리고, Space를 누르면 제자리에서 점프해요.",
      "오브젝트 가까이에서 상호작용 안내가 뜨면 기본 E키 또는 화면 버튼을 눌러요. 채팅 입력 중에는 이동 대신 글자가 입력되므로 Esc로 입력을 끝내 주세요.",
      "Tab은 왼쪽 사이드 메뉴를 접고 펼쳐요. 조작키를 변경했다면 설정한 키를 사용하세요."
    ],
    "keys": [
      "WASD / 방향키 이동",
      "Shift 달리기",
      "Space 점프",
      "E 상호작용"
    ]
  },
  {
    "category": "빠른 이동 · 층 이동",
    "title": "왼쪽 위 빠른 이동으로 바로 이동해요",
    "image": "event-travel-20261105.webp",
    "alt": "18층 두 곳과 옥상 두 곳을 선택하는 빠른 이동 메뉴",
    "text": "화면 왼쪽 맨 위의 빠른 이동 탭을 클릭하면 목적지를 선택할 수 있어요. 메뉴가 접혀 있다면 Tab으로 왼쪽 메뉴를 펼쳐 주세요.",
    "items": [
      "① 왼쪽 위 빠른 이동 클릭 → ② 원하는 목적지 선택 → ③ 이동 안내를 확인하고 도착해요. 18층은 스위트 갤러리·기프트 마켓, 옥상은 초콜릿 워크숍·초콜릿 광장 두 곳씩이에요.",
      "빠른 이동은 짧은 재사용 대기 시간이 있어요. 학습교실이나 ROOM 01·02 안에서는 출입구로 밖에 나온 다음 사용하세요. Tab은 사이드 메뉴를 여닫는 키이고, 목적지 선택은 클릭으로 해요.",
      "걸어서 이동하고 싶다면 연결 계단이나 소화기 포탈 앞에서 E로 상호작용해 층을 오갈 수 있어요."
    ],
    "keys": [
      "왼쪽 위 빠른 이동 클릭",
      "18층 2곳 · 옥상 2곳",
      "Tab = 사이드 메뉴 열고 닫기"
    ]
  },
  {
    "category": "메인 미션 · 전체 흐름",
    "title": "18층 게시판 앞 꾸왕이에게 말을 걸어요",
    "image": "event-main-20261105.webp",
    "alt": "18층 게시판 앞 탐정 꾸왕이와 오른쪽 메인 미션 진행도",
    "text": "메인 미션은 총 다섯 단계예요. 현재 단계는 화면 오른쪽 진행도에서 확인하고, 내 화면의 화살표를 따라 이동하세요.",
    "items": [
      "① 꾸왕이와 대화해 이상한 수량표 확인 → ② 옥상 ROOM 01에서 증거 분석 → ③ 18층 꾸왕이에게 돌아가기 순서로 진행해요.",
      "그다음 ④ 옥상 ROOM 02에서 빼빼로 제작 → ⑤ 18층 꾸왕이에게 최종 전달하면 사건이 끝나요. 학습교실은 원할 때 별도로 이용할 수 있어요.",
      "목적지가 화면 안에 있으면 그 위에 화살표가 떠요. 화면 밖이면 가장자리에서 방향을 알려주고, 다른 층이면 연결 계단 방향으로 안내해요."
    ],
    "keys": [
      "꾸왕이 앞 E",
      "오른쪽 현재 단계",
      "내 화면의 미션 화살표"
    ]
  },
  {
    "category": "메인 미션 · ROOM 01",
    "title": "영석 매니저님과 증거를 분석해요",
    "image": "event-room1-20261105.webp",
    "alt": "옥상 ROOM 01 증거분석실 내부와 영석 매니저 NPC",
    "text": "ROOM 01은 옥상 계단 위쪽에 있는 증거분석실이에요. 건물 앞 포탈에서 E를 눌러 내부로 들어가세요.",
    "items": [
      "먼저 영석 매니저님에게 말을 걸어 분석 방법을 들어요. 이 대화를 마쳐야 SQL 순서 기억 게임을 시작할 수 있어요.",
      "분석 작업대에서 SQL 명령 순서를 기억하고 나온 순서대로 눌러요. 게임을 통과한 뒤 수량표를 분석하고 정답을 제출하세요.",
      "분석이 끝나면 하단의 옥상으로 나가기 버튼을 누르고 18층 꾸왕이에게 돌아가요. 대화 뒤 10초 빼빼로 타격 챌린지를 마치면 다음 제작 단계로 이어져요."
    ],
    "keys": [
      "영석 매니저님 먼저 대화",
      "작업대 앞 E",
      "하단 옥상으로 나가기"
    ]
  },
  {
    "category": "메인 미션 · ROOM 02",
    "title": "네 작업대를 돌며 선물을 완성해요",
    "image": "event-room2-20261105.webp",
    "alt": "재료 준비, 초콜릿 코팅, 토핑 데코, 포장 작업대가 배치된 ROOM 02",
    "text": "꾸왕이에게 돌아가는 단계를 마쳤다면 옥상 ROOM 02 쇼콜라 제작실로 가세요. 현재 작업대는 방 안의 진행도와 화살표가 알려줘요.",
    "items": [
      "① 재료 준비: 빼빼로 재료 카드의 짝을 모두 찾아요. ② 초콜릿 코팅: 75% 완벽선을 노리고 멈춰요. 세 번 모두 70~80% 구간 안에 들어오고, 완벽선과의 시간차 평균이 200ms 이내면 통과해요.",
      "③ 토핑 데코: 30초 안에 딸기와 아몬드를 각각 10개 이상 받아요. 탄 토핑을 받으면 받는 범위가 줄고, 세 번 받으면 실패하니 피하세요. ④ 포장: 짧은 포장 애니메이션이 끝날 때까지 기다려요.",
      "제작을 모두 마친 뒤 옥상으로 나가 18층 꾸왕이에게 선물을 최종 전달하세요. 대화만 닫지 말고 선물 전달까지 마쳐야 메인 미션이 완료돼요."
    ],
    "keys": [
      "카드 → 코팅 → 토핑 → 포장",
      "현재 작업대 앞 E",
      "꾸왕이에게 최종 전달"
    ]
  },
  {
    "category": "수사수첩 · 나의 기록",
    "title": "내가 달성한 기록을 확인해요",
    "image": "event-notebook-20261105.webp",
    "alt": "나의 달성 기록과 메인 미션 개인 배지가 표시된 꾸왕이의 수사수첩",
    "text": "왼쪽 사이드 메뉴의 꾸왕이의 수사수첩에서 내 계정의 달성 기록을 확인할 수 있어요.",
    "items": [
      "획득 전에는 제목과 실루엣이 보이고, 내가 달성한 항목만 아이콘이 컬러로 표시돼요.",
      "이스터에그는 종류별 서버 최초 한 명, 계정당 한 개만 획득할 수 있어요. 다른 사람의 달성 아이콘은 내 수첩에서 활성화되지 않아요.",
      "꾸왕이의 명예 조수는 이스터에그와 별개인 개인 메인 미션 배지예요. 메인 미션을 완료한 모든 이용자가 받을 수 있어요."
    ],
    "keys": [
      "Tab → 꾸왕이의 수사수첩",
      "내 달성 기록 확인",
      "메인 미션 배지는 별도"
    ]
  },
  {
    "category": "18층 · 별도 미니게임",
    "title": "직원실 안쪽의 세 게임을 즐겨요",
    "image": "event-minigames-20261109.webp",
    "alt": "18층 직원실 안쪽 벽에 나란히 놓인 코드 타이핑·타이머·페어링 정원 게임 장치",
    "text": "18층 직원실 안쪽 긴 벽에는 메인 미션과 별개인 게임 세 가지가 있어요. ROOM 01·ROOM 02 작업대와는 다른 게임이에요.",
    "items": [
      "코드 타이핑: 타자기가 놓인 책상에서 화면에 나온 코드를 정확하게 입력해 보세요.",
      "타이머 맞추기: 타이머 장치에서 11.11초를 노리고 멈춰 보세요. 페어링 정원: 카드의 위치를 기억해 같은 짝을 찾아요.",
      "각 장치 가까이 가서 E 또는 상호작용 버튼을 누르면 게임 팝업이 열려요. 메인 미션의 선행·완료 조건은 아니며, 게임은 이벤트가 열려 있고 강의 프리즈가 아닐 때 이용할 수 있어요."
    ],
    "keys": [
      "18층 직원실 안쪽",
      "장치 앞 E · 상호작용",
      "메인 미션과 별도 진행"
    ]
  },
  {
    "category": "빼빼로 · 리필과 휘두르기",
    "title": "10/10으로 채우고 가볍게 겨뤄요",
    "image": "event-refill-20261105.webp",
    "alt": "현재 이벤트맵의 리필 상자와 빼빼로 잔량 표시",
    "text": "빼빼로 리필 표시가 있는 큰 상자 가까이에서 E를 누르면 빼빼로를 채울 수 있어요. 잔량은 화면 왼쪽 위에서 확인해요.",
    "items": [
      "리필하면 10/10으로 채워져요. 빼빼로를 들고 ROOM 01·02에 들어가도 안에서 계속 들고 있어요.",
      "기본 Z키를 누르고 있으면 연속으로 휘둘러요. 상대에게 적중할 때마다 잔량이 1 줄고, 빈손이나 빗맞혔을 때도 휘두르는 모션은 나와요.",
      "Ctrl을 누르고 있으면 방어해요. 방어 중인 상대나 샌드백을 빼빼로로 때리면 공격자의 빼빼로가 한 번에 부서져요. 잔량이 없어지면 상자에서 다시 채워 주세요."
    ],
    "keys": [
      "E 리필 · 10/10",
      "Z 연속 휘두르기",
      "Ctrl 누르는 동안 방어"
    ]
  },
  {
    "category": "선택 학습 · Python · SQL · CS",
    "title": "원하는 과목과 난이도로 공부해요",
    "image": "event-learning-20261105.webp",
    "alt": "현재 이벤트 테마의 학습교실 내부와 학습 영역 안내",
    "text": "18층의 Python·SQL·CS 학습교실은 메인 미션 진행과 별도로 이용할 수 있어요. 교실 출입구를 통해 안으로 들어가세요.",
    "items": [
      "교실 안에서 원하는 학습 유형의 표시된 박스 안으로 이동한 뒤 E를 눌러요. 객관식·코드 빈칸 유형과 초급·중급·고급 난이도를 선택할 수 있어요.",
      "한 세트는 10문제이며 문제당 최대 세 번 제출할 수 있어요. 코드 빈칸은 들어갈 답만 입력하고 Python 답안은 대소문자도 확인하세요.",
      "결과에서 정답과 적립 포인트를 확인해요. 나갈 때는 출입구로 돌아가 메타버스로 나가기 안내가 나타나면 E를 누르세요."
    ],
    "keys": [
      "교실 출입구로 입장",
      "학습 박스 안에서 E",
      "10문제 · 문제당 최대 3회"
    ]
  },
  {
    "category": "포인트와 적립 기록",
    "title": "보유 포인트를 누르면 내역이 펼쳐져요",
    "image": "event-points-20261105.webp",
    "alt": "현재 사이드 메뉴의 보유 포인트와 펼쳐진 적립 내역",
    "text": "왼쪽 사이드 메뉴에서 코인 아이콘 옆 보유 포인트를 확인할 수 있어요.",
    "items": [
      "학습 정답 한 문제의 기본 적립은 초급 20P, 중급 50P, 고급 80P예요. 실제 적립 결과는 학습 결과와 내역에서 확인하세요.",
      "보유 포인트를 누르면 과목·난이도·적립 시각 등이 담긴 최근 내역이 펼쳐져요. 더 보기가 있으면 이전 내역도 확인할 수 있어요.",
      "수사수첩의 아이콘 공개나 발견 연출은 포인트 적립과 별개의 표시예요. 공유 기록과 내 포인트를 혼동하지 마세요."
    ],
    "keys": [
      "Tab 사이드 메뉴",
      "코인 옆 보유 포인트 클릭"
    ]
  },
  {
    "category": "채팅 · 게시판 · 공지 알림",
    "title": "공지와 쪽지도 확인해 주세요",
    "image": "event-notices-20261105.webp",
    "alt": "공지 알림 아이콘에서 연 공지사항 목록과 읽음 표시",
    "text": "채팅으로 함께 이야기하고, 게시판과 쪽지함에서 필요한 소식을 확인하세요.",
    "items": [
      "Enter로 채팅을 입력하고 전송해요. 입력을 마쳤다면 Esc를 눌러 채팅에서 빠져나온 뒤 이동하세요. 왼쪽 사이드 메뉴에서 게시판과 쪽지함도 열 수 있어요.",
      "오른쪽 아래 종 아이콘은 게시판 공지 알림이에요. 안 읽은 공지가 있으면 숫자가 표시되고, 공지 본문을 읽으면 숫자가 줄어요. 목록을 열기만 하면 읽음으로 처리되지 않아요.",
      "옥상의 다른 부스도 E로 이용할 수 있어요. 콘텐츠에 따라 별도 창이나 팝업이 열릴 수 있으니, 이용 후 캠퍼스 화면으로 돌아와 주세요."
    ],
    "keys": [
      "Enter 채팅 · Esc 입력 종료",
      "종 아이콘 → 공지 본문 읽기",
      "사이드 메뉴 게시판 · 쪽지함"
    ]
  },
  {
    "category": "설정과 안내 다시 보기",
    "title": "내 조작키를 바꾸고 필요할 때 다시 읽어요",
    "image": "event-settings-20261105.webp",
    "alt": "키보드 조작 변경과 화면 표시 옵션이 있는 현재 설정 창",
    "text": "설정 버튼이나 Esc로 설정을 열어 이동·달리기·점프·휘두르기·방어·상호작용 키를 바꿀 수 있어요.",
    "items": [
      "바꿀 동작을 누르고 원하는 키를 입력하세요. 이미 사용하는 키를 고르면 두 동작의 키가 서로 바뀌고, 기본값 복원도 가능해요. 키 설정은 현재 브라우저에 계정별로 저장돼요.",
      "다른 사람 닉네임이나 말풍선 등 화면 표시 옵션도 조절할 수 있어요. 수업 중 이용이 일시 중지됐다는 안내가 나오면 운영자가 재개할 때까지 기다려 주세요.",
      "이 안내서는 첫 접속 때 표시돼요. 이후에는 설정 안의 처음 이용 안내 다시 보기에서 언제든 다시 열 수 있어요. 먼저 18층 게시판 앞의 꾸왕이에게 말을 걸어 보세요!"
    ],
    "keys": [
      "Esc / 설정 버튼",
      "키 변경 · 기본값 복원",
      "처음 이용 안내 다시 보기"
    ]
  }
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
  function source(step) { return new URL(`assets/guide/${step.image}?v=20261105-event-guide`, document.baseURI).href; }
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
