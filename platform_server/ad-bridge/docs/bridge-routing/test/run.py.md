# test/run.py

- 경로: [ad-bridge/test/run.py](../../../test/run.py)
- 기능: 기능 그룹·단일 사례 테스트 실행
- 호출·연결: CLI → main → unittest 또는 Django DiscoverRunner
- 파라미터: group: GROUPS의 그룹 또는 all; case: 그룹의 단일 사례 별칭; --list: 목록 출력. all은 저장소 DB 그룹을 제외한다.

| 시그니처·메서드 | 기능 |
| --- | --- |
| `main()` | CLI 인자로 테스트 그룹·실행기 선택 |

현재 코드는 `ad-bridge/`를 sys.path에 추가하지만 구현은 `scripts/`에 있고 import 이름은 `ad_bridge`다. 패키지 경로 연결은 별도 확인이 필요하며 이 문서 작업에서 실행 코드를 바꾸지 않았다.
