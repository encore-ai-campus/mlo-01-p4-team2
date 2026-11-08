# test/support/django_storage_settings.py

- 경로: [ad-bridge/test/support/django_storage_settings.py](../../../../test/support/django_storage_settings.py)
- 기능: 메모리 SQLite 저장소 테스트 설정
- 호출·연결: test.run의 storage 그룹 → DiscoverRunner → 메모리 SQLite
- 파라미터: 기본 테스트 설정을 import하고 DATABASES를 sqlite3 :memory:로 지정한다. 외부 DB는 사용하지 않는다.

시그니처: 직접 선언한 함수·클래스 없음.
