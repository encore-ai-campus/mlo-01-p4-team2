# CP02 검증과 테스트

코드 재검토 PASS 뒤에 수행했다.

1. 정적·저장 계약 검증 PASS: Python 11개 구문, 필수 이력·환경 필드, 불변 게시·fsync 경로 확인.
2. `/private/tmp/mlp-venv/bin/python -m unittest discover -s tests -v`: 34개 PASS.

실제 MLP 최초·추가 학습과 이전 버전 해시 보존을 확인했다.
폴더·파일·포인터 저장 실패, 환경·런타임 불일치, 잠금 경쟁을 로컬에서 시험했다.
원격 API·S3·운영 DB에는 접속하지 않았다. 광고 성과는 평가하지 않았다.
