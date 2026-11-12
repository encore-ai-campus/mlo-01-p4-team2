// 개발자 설정: 시간은 모두 밀리초(ms). 바꾼 뒤 npm run build를 실행하세요.
export const GAME_CONFIG = Object.freeze({
  timeLimitMs: 40000,       // 라운드별 제한 시간 (미리보기 제외)
  totalRounds: 1,          // 반드시 끝까지 진행할 N라운드 (1 또는 10 등)
  requiredWins: 1,         // 최종 통과에 필요한 M회 성공 (1 <= M <= N)
  maxMistakes: 5,          // 서로 다른 두 카드를 고른 횟수. 도달 즉시 라운드 실패
  rows: 4,                // 세로는 4장 고정
  columns: 4,             // 4 이상 정수. 4×4 → 4×5 → 4×6 … 4×12
  topic: 'mixed',          // 'mixed' | 'python' | 'sql'
  previewMs: 4000,         // 모든 앞면을 보여 주는 시간
  flipStaggerMs: 35,       // 카드를 순서대로 덮는 간격
  flipDurationMs: 250,     // 마지막 카드가 닫힐 때까지 입력 잠금
  mismatchMs: 650,         // 오답 카드 두 장을 보여 주는 시간
  roundTransitionMs: 0,    // 이전 설정 호환용. 값과 무관하게 확인 후에만 다음 라운드 시작
  showSettings: false,      // 단독 실행용 데모 설정 (iframe 안에서는 자동 숨김)
  parentOrigin: null,      // 예: 'https://campus.example.com' (경로·마지막 / 제외)
  allowedParentOrigins: [], // ?parentOrigin= 으로 받을 추가 origin. 동일 origin은 자동 허용
});
