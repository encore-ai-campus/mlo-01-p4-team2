# test/support/django_settings.py

- 경로: [ad-bridge/test/support/django_settings.py](../../../../test/support/django_settings.py)
- 기능: DB 없는 게임 어댑터 테스트 설정
- 호출·연결: test.run → django.setup → ROOT_URLCONF / INSTALLED_APPS
- 파라미터: ROOT_URLCONF: 현재 모듈; api/ads/ → ad_bridge.game.urls; DATABASES: 빈 설정; ALLOWED_HOSTS: testserver.

시그니처: 직접 선언한 함수·클래스 없음.
