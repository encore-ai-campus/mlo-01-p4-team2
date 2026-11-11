// 개발자 설정. 모든 시간의 단위는 밀리초(ms)입니다.
export const GAME_CONFIG = Object.freeze({
  targetMs: 11110,
  toleranceMs: 0,
  // 목표 + 허용 오차를 넘긴 뒤 결과를 보여 주기까지의 여유 시간.
  overtimeMs: 2000,
  // 배포 시 false로 설정하면 화면의 데모 설정을 숨깁니다.
  showSettings: false,
  // iframe 결과를 받을 메타버스의 정확한 origin을 지정하세요.
  // 예: 'https://campus.example.com'. null이면 부모에게 전송하지 않습니다.
  parentOrigin: null,
  // URL의 ?parentOrigin= 값을 허용할 추가 origin. 같은 origin은 자동 허용.
  allowedParentOrigins: [],
});
