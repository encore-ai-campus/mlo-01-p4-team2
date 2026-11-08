# 작업 코드와 실행 경로 대응

기준 커밋: `8397431c8b17d4b8cee36eb7dcbb5d7081215b6e`.

모든 파일의 대응 규칙은 **작업 경로에서 맨 앞 `src/`를 제거한 경로가 기존 실행 경로**이다.

예: `src/frontend/dist/campus-ads.js` → `frontend/dist/campus-ads.js`.

## 통합 원칙

1. 작업 중에는 src의 해당 파일을 수정한다.
2. 아래 목록에서 실제 변경한 파일만 기존 실행 경로와 비교해 반영한다. src 전체를 원본 폴더에 덮어쓰지 않는다.
3. 기준 커밋 이후 event에서 같은 파일이 바뀌었으면 변경을 합친다. 복사 당시 파일로 최신 변경을 덮어쓰지 않는다.
4. 원래 실행 경로에서 테스트하고 `git diff`로 기능 변경이 포함됐는지 확인한다.
5. event-74를 커밋·푸시하고 event 대상 PR로 병합한다. Git은 src 경로를 자동으로 기존 경로에 대응시키지 않는다.

이 구성은 기존 프로젝트 경로를 옮기는 마이그레이션이 아니다. 복사본의 백엔드는 전체 앱 의존성을 포함하지 않으며 단독 서버 실행을 지원하지 않는다. 새 기능 파일이나 마이그레이션을 추가하면 이 목록과 원래 경로도 함께 관리한다.

## 포함 범위

- ad_bridge: 기존 코드, 마이그레이션, 정적 스크립트와 단위 테스트. 기존 README 대신 docs/ROUTING.md에서 전체 흐름을 설명한다.
- event_story 및 config: 클릭 업적과 URL 연결을 확인할 관련 파일만 포함한다.
- frontend: 광고 코드, 렌더러·호출부와 관련 테스트. map-data.js는 렌더러 테스트 의존성이다.
- 환경 파일, API 키, DB 파일, 이미지·오디오 에셋은 복사하지 않았다.

## 파일 목록

| 작업 파일 | 기존 실행 경로 |
| --- | --- |
| [src/backend/ad_bridge/__init__.py](../src/backend/ad_bridge/__init__.py) | `backend/ad_bridge/__init__.py` |
| [src/backend/ad_bridge/api/__init__.py](../src/backend/ad_bridge/api/__init__.py) | `backend/ad_bridge/api/__init__.py` |
| [src/backend/ad_bridge/api/client.py](../src/backend/ad_bridge/api/client.py) | `backend/ad_bridge/api/client.py` |
| [src/backend/ad_bridge/api/config.py](../src/backend/ad_bridge/api/config.py) | `backend/ad_bridge/api/config.py` |
| [src/backend/ad_bridge/api/errors.py](../src/backend/ad_bridge/api/errors.py) | `backend/ad_bridge/api/errors.py` |
| [src/backend/ad_bridge/api/types.py](../src/backend/ad_bridge/api/types.py) | `backend/ad_bridge/api/types.py` |
| [src/backend/ad_bridge/apps.py](../src/backend/ad_bridge/apps.py) | `backend/ad_bridge/apps.py` |
| [src/backend/ad_bridge/clicks.py](../src/backend/ad_bridge/clicks.py) | `backend/ad_bridge/clicks.py` |
| [src/backend/ad_bridge/decisions/__init__.py](../src/backend/ad_bridge/decisions/__init__.py) | `backend/ad_bridge/decisions/__init__.py` |
| [src/backend/ad_bridge/decisions/service.py](../src/backend/ad_bridge/decisions/service.py) | `backend/ad_bridge/decisions/service.py` |
| [src/backend/ad_bridge/decisions/store.py](../src/backend/ad_bridge/decisions/store.py) | `backend/ad_bridge/decisions/store.py` |
| [src/backend/ad_bridge/delivery/__init__.py](../src/backend/ad_bridge/delivery/__init__.py) | `backend/ad_bridge/delivery/__init__.py` |
| [src/backend/ad_bridge/delivery/policy.py](../src/backend/ad_bridge/delivery/policy.py) | `backend/ad_bridge/delivery/policy.py` |
| [src/backend/ad_bridge/delivery/sender.py](../src/backend/ad_bridge/delivery/sender.py) | `backend/ad_bridge/delivery/sender.py` |
| [src/backend/ad_bridge/delivery/worker.py](../src/backend/ad_bridge/delivery/worker.py) | `backend/ad_bridge/delivery/worker.py` |
| [src/backend/ad_bridge/django_urls.py](../src/backend/ad_bridge/django_urls.py) | `backend/ad_bridge/django_urls.py` |
| [src/backend/ad_bridge/game/__init__.py](../src/backend/ad_bridge/game/__init__.py) | `backend/ad_bridge/game/__init__.py` |
| [src/backend/ad_bridge/game/placements.py](../src/backend/ad_bridge/game/placements.py) | `backend/ad_bridge/game/placements.py` |
| [src/backend/ad_bridge/game/urls.py](../src/backend/ad_bridge/game/urls.py) | `backend/ad_bridge/game/urls.py` |
| [src/backend/ad_bridge/game/views.py](../src/backend/ad_bridge/game/views.py) | `backend/ad_bridge/game/views.py` |
| [src/backend/ad_bridge/impressions/__init__.py](../src/backend/ad_bridge/impressions/__init__.py) | `backend/ad_bridge/impressions/__init__.py` |
| [src/backend/ad_bridge/impressions/service.py](../src/backend/ad_bridge/impressions/service.py) | `backend/ad_bridge/impressions/service.py` |
| [src/backend/ad_bridge/impressions/store.py](../src/backend/ad_bridge/impressions/store.py) | `backend/ad_bridge/impressions/store.py` |
| [src/backend/ad_bridge/management/__init__.py](../src/backend/ad_bridge/management/__init__.py) | `backend/ad_bridge/management/__init__.py` |
| [src/backend/ad_bridge/management/commands/__init__.py](../src/backend/ad_bridge/management/commands/__init__.py) | `backend/ad_bridge/management/commands/__init__.py` |
| [src/backend/ad_bridge/management/commands/dispatch_ad_reactions.py](../src/backend/ad_bridge/management/commands/dispatch_ad_reactions.py) | `backend/ad_bridge/management/commands/dispatch_ad_reactions.py` |
| [src/backend/ad_bridge/migrations/__init__.py](../src/backend/ad_bridge/migrations/__init__.py) | `backend/ad_bridge/migrations/__init__.py` |
| [src/backend/ad_bridge/migrations/0001_initial.py](../src/backend/ad_bridge/migrations/0001_initial.py) | `backend/ad_bridge/migrations/0001_initial.py` |
| [src/backend/ad_bridge/migrations/0002_impression_retry.py](../src/backend/ad_bridge/migrations/0002_impression_retry.py) | `backend/ad_bridge/migrations/0002_impression_retry.py` |
| [src/backend/ad_bridge/migrations/0003_decision_owner.py](../src/backend/ad_bridge/migrations/0003_decision_owner.py) | `backend/ad_bridge/migrations/0003_decision_owner.py` |
| [src/backend/ad_bridge/migrations/0004_shared_impressions.py](../src/backend/ad_bridge/migrations/0004_shared_impressions.py) | `backend/ad_bridge/migrations/0004_shared_impressions.py` |
| [src/backend/ad_bridge/migrations/0005_placementbatch.py](../src/backend/ad_bridge/migrations/0005_placementbatch.py) | `backend/ad_bridge/migrations/0005_placementbatch.py` |
| [src/backend/ad_bridge/models.py](../src/backend/ad_bridge/models.py) | `backend/ad_bridge/models.py` |
| [src/backend/ad_bridge/placements.py](../src/backend/ad_bridge/placements.py) | `backend/ad_bridge/placements.py` |
| [src/backend/ad_bridge/results/__init__.py](../src/backend/ad_bridge/results/__init__.py) | `backend/ad_bridge/results/__init__.py` |
| [src/backend/ad_bridge/results/service.py](../src/backend/ad_bridge/results/service.py) | `backend/ad_bridge/results/service.py` |
| [src/backend/ad_bridge/routing.py](../src/backend/ad_bridge/routing.py) | `backend/ad_bridge/routing.py` |
| [src/backend/ad_bridge/static/ad_bridge/display.js](../src/backend/ad_bridge/static/ad_bridge/display.js) | `backend/ad_bridge/static/ad_bridge/display.js` |
| [src/backend/ad_bridge/static/ad_bridge/impression.js](../src/backend/ad_bridge/static/ad_bridge/impression.js) | `backend/ad_bridge/static/ad_bridge/impression.js` |
| [src/backend/ad_bridge/tests/__init__.py](../src/backend/ad_bridge/tests/__init__.py) | `backend/ad_bridge/tests/__init__.py` |
| [src/backend/ad_bridge/tests/decisions/__init__.py](../src/backend/ad_bridge/tests/decisions/__init__.py) | `backend/ad_bridge/tests/decisions/__init__.py` |
| [src/backend/ad_bridge/tests/decisions/test_service.py](../src/backend/ad_bridge/tests/decisions/test_service.py) | `backend/ad_bridge/tests/decisions/test_service.py` |
| [src/backend/ad_bridge/tests/decisions/test_store.py](../src/backend/ad_bridge/tests/decisions/test_store.py) | `backend/ad_bridge/tests/decisions/test_store.py` |
| [src/backend/ad_bridge/tests/impressions/__init__.py](../src/backend/ad_bridge/tests/impressions/__init__.py) | `backend/ad_bridge/tests/impressions/__init__.py` |
| [src/backend/ad_bridge/tests/impressions/test_service.py](../src/backend/ad_bridge/tests/impressions/test_service.py) | `backend/ad_bridge/tests/impressions/test_service.py` |
| [src/backend/ad_bridge/tests/impressions/test_store.py](../src/backend/ad_bridge/tests/impressions/test_store.py) | `backend/ad_bridge/tests/impressions/test_store.py` |
| [src/backend/ad_bridge/tests/settings.py](../src/backend/ad_bridge/tests/settings.py) | `backend/ad_bridge/tests/settings.py` |
| [src/backend/ad_bridge/tests/storage/__init__.py](../src/backend/ad_bridge/tests/storage/__init__.py) | `backend/ad_bridge/tests/storage/__init__.py` |
| [src/backend/ad_bridge/tests/storage/test_migrations.py](../src/backend/ad_bridge/tests/storage/test_migrations.py) | `backend/ad_bridge/tests/storage/test_migrations.py` |
| [src/backend/ad_bridge/tests/support.py](../src/backend/ad_bridge/tests/support.py) | `backend/ad_bridge/tests/support.py` |
| [src/backend/ad_bridge/tests/test_placement_views.py](../src/backend/ad_bridge/tests/test_placement_views.py) | `backend/ad_bridge/tests/test_placement_views.py` |
| [src/backend/ad_bridge/tests/test_placements.py](../src/backend/ad_bridge/tests/test_placements.py) | `backend/ad_bridge/tests/test_placements.py` |
| [src/backend/ad_bridge/tests/test_routing.py](../src/backend/ad_bridge/tests/test_routing.py) | `backend/ad_bridge/tests/test_routing.py` |
| [src/backend/config/urls.py](../src/backend/config/urls.py) | `backend/config/urls.py` |
| [src/backend/event_story/achievement_services.py](../src/backend/event_story/achievement_services.py) | `backend/event_story/achievement_services.py` |
| [src/backend/event_story/models.py](../src/backend/event_story/models.py) | `backend/event_story/models.py` |
| [src/backend/tests/test_ad_achievement.py](../src/backend/tests/test_ad_achievement.py) | `backend/tests/test_ad_achievement.py` |
| [src/frontend/dist/ad-visibility.js](../src/frontend/dist/ad-visibility.js) | `frontend/dist/ad-visibility.js` |
| [src/frontend/dist/campus-ads.js](../src/frontend/dist/campus-ads.js) | `frontend/dist/campus-ads.js` |
| [src/frontend/dist/campus.js](../src/frontend/dist/campus.js) | `frontend/dist/campus.js` |
| [src/frontend/dist/isometric.js](../src/frontend/dist/isometric.js) | `frontend/dist/isometric.js` |
| [src/frontend/dist/map-data.js](../src/frontend/dist/map-data.js) | `frontend/dist/map-data.js` |
| [src/frontend/tests/ad-display.test.cjs](../src/frontend/tests/ad-display.test.cjs) | `frontend/tests/ad-display.test.cjs` |
| [src/frontend/tests/ad-visibility.test.cjs](../src/frontend/tests/ad-visibility.test.cjs) | `frontend/tests/ad-visibility.test.cjs` |
| [src/frontend/tests/campus-ads.test.cjs](../src/frontend/tests/campus-ads.test.cjs) | `frontend/tests/campus-ads.test.cjs` |
| [src/frontend/tests/isometric.test.cjs](../src/frontend/tests/isometric.test.cjs) | `frontend/tests/isometric.test.cjs` |
