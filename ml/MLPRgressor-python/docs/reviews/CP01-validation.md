# CP01 검증과 테스트

코드 재검토 PASS 이후 순서대로 수행했다.

1. 정적·계약 검증: PASS. Python 파일8개 AST 검사, 설치 패키지13개 lock 대조, C8 필드·빈 키 예제 확인.
2. 테스트: `/private/tmp/mlp-venv/bin/python -m unittest discover -s tests -v`.
3. 결과: 18개 통과. 실제 로컬 Delta 날짜 필터·스키마, 입력·산식·설정·NULL/0·비밀값 검사 포함.

테스트는 임시 로컬 파일과 모의 오류만 사용했다. 실제 S3와 광고 서버에는 접속하지 않았다.
CP02 구현 진입 조건을 충족했다.
