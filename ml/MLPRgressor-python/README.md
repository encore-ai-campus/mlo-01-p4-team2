# MLPRegressor 광고 가중치

Gold의 어제 자료로 학습하고, 광고 서버에 과정별 가중치를 저장하는 독립 Python 프로그램이다.
자체 API 서버는 없다. 광고 서버 Internal API의 클라이언트로 실행한다.

흐름: S3 Delta Gold → 입력 검사 → MLP 학습·예측 → 정수 변환 → C8 가중치 쓰기.
구현 순서와 검토 기록은 [라우팅 문서](docs/IMPLEMENTATION_ROUTING.md)에 있다.

## 설치와 설정

Linux·macOS와 Python 3.13을 기준으로 검증했다.
처음에는 `.env.example`을 `.env`로 복사하고 값을 채운다.

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
cp .env.example .env
chmod 600 .env
```

| 설정 | 의미 |
|---|---|
| ADS_INTERNAL_BASE_URL | 광고 서버 주소와 `/api/internal/v1`. 예: `https://ads.example.com/api/internal/v1` |
| ADS_INTERNAL_KEY | 광고 서버 운영자가 발급한 X-Internal-Key. 매체 키나 OpenAI 키가 아님 |
| GOLD_DELTA_URI | `s3://버킷/뿌리/gold/ads/creative_course_daily/v2` |
| S3_STORAGE_OPTIONS | Delta 읽기 옵션 JSON. 기본 `{}`. 서버의 AWS role/profile 사용 가능 |
| STATE_DIR | 모델과 진행 상태의 로컬 폴더. 기본 프로젝트 안 `artifacts` |
| HTTP_TIMEOUT | HTTP 한 요청의 시간 제한. 기본15초 |
| TRAIN_EPOCHS | 어제 정상 자료의 반복 횟수. 기본100, 성능 최적값은 아님 |

S3 호환 저장소는 `S3_STORAGE_OPTIONS`의 `AWS_ENDPOINT_URL` 또는 환경변수로 endpoint를 정한다.
환경변수가 `.env`보다 우선한다. 상대 STATE_DIR와 로컬 Gold 경로는 프로젝트 폴더 기준이다.
API 주소·Gold 경로·별도 endpoint를 바꾸면 **별도 STATE_DIR**를 사용해야 한다.
키만 교체하면 기존 상태를 사용할 수 있다.
비밀 키·실제 `.env`·모델·상태를 저장소에 올리지 않는다.

## 실행과 예약

```sh
.venv/bin/python main.py --dry-run
.venv/bin/python main.py
.venv/bin/python main.py --env-file /secure/weights.env
```

dry-run도 Gold와 현재 광고를 읽고 메모리에서 학습한다.
POST와 모델 버전 생성은 하지 않는다. 환경 결합을 위한 상태 파일과 잠금은 만들 수 있다.
진행 중인 전송이 있으면 dry-run은 보류한다.

외부 스케줄러가 매일 KST22시에 명령을 실행한다. 프로그램이 예약을 자동 등록하지 않는다.
서버가 UTC라면 cron의 `0 13 * * *`가 KST22시다. KST 서버라면 `0 22 * * *`다.
cron에서 Python·main.py·환경 파일·로그 경로는 절대 경로로 지정한다.
현재 디렉터리와 cron 시간대를 확인한다. 상태 폴더를 공유하는 단일 가중치 작성자만 운영한다.

실행 시작 시 KST 어제를 고정한다. 실행일의 다음 KST 자정에 작업을 만료시킨다.
DB의 UTC 시각을 Gold의 KST 날짜와 혼동하지 않는다.
이 프로그램은 광고 서버 DB에 직접 접속하지 않는다.
version·changed_at·운영자 감사·outbox는 광고 서버가 같은 트랜잭션에서 처리한다.

## 입력과 출력

Gold 이름과 구조를 바꾸지 않는다. `*_clicks` 세 컬럼은 이번 입력에서 사용하지 않는다.
confirmed=true, 정상 UUID, category 0~255, CTR·CVR 여섯 값이 유한한0~1인 행만 사용한다.
NULL은 대체하지 않고 제외한다. category=0은 기타이며 정상값이다.
입력 순서는 MLO CTR·CVR, AIO CTR·CVR, SKN CTR·CVR, category bit0~7이다.
bit0은 오른쪽 끝이며 Data Analysis다.

정답은 과정마다 `s=CTR*(1+4*CVR)`를 구한 뒤 세 점수 합으로 나눈 값이다.
클릭만 있으면1점, 전환으로 이어진 클릭은 총5점으로 보는 승인 산식이다.
점수가 전부0이면 정답은 `[0,0,0]`이다.
모델은 입력14 → 은닉32 → 은닉16 → 출력3의 MLPRegressor다.
최초에는 새로 만들고, 이후에는 저장된 모델에 어제 자료로 partial_fit한다.

모델 예측은 확률을 보장하지 않는다.
유한한 출력3개를0~1로 제한하고100을 곱한다. 합이100을 넘으면 비례 축소한다.
반올림 뒤 합이100을 넘으면 초과분을 줄인다. 최종값은 정수이고 각각0~100, 합계100이하다.
광고 서버의 가중치는 노출 배율용 값이다. 배율은 `1+0.5*가중치/100`이다.
실제 광고 선택에 가중치를 반영하는 기능은 이번 범위 밖이다.

학습은 정상 Gold 전체를 사용한다.
전송은 현재 광고 목록과의 교집합이며, profile이 있고 Gold category와 같아야 한다.
광고 이름·소재·이미지·URL은 모델 입력에 사용하지 않는다.
C8 요청 본문에는 ad_group_id와 세 가중치만 담는다.

## 재시도와 재개

| 종료 코드 | 의미 |
|---|---|
| 0 | 완료 또는 이미 완료 |
| 75 | 자료 보류·잠금 경쟁·날짜 만료·전송 상태 변경 |
| 1 | 영구 오류 또는 일시적 실패의 재시도 소진 |

재시도는 실행부 한 곳에서만 한다. 첫 시도를 포함해 최대3회, 대기는 최소600초다.
503의 Retry-After가 더 길면 그만큼 기다린다. 자정을 넘길 대기는 수행하지 않는다.
400·401·403·설정·계약 오류는 반복하지 않는다. 수정 후 같은 명령을 다시 실행한다.
외부 스케줄러에는 별도 즉시 재시도를 겹치지 않는다.

빈 Gold와 미확정 행은 날짜 전체를 보류하고 재시도한다.
정상 입력이0개이면 보류하되 자동 반복하지 않는다. 모델과 학습 완료일은 유지한다.
읽기가 완료된 보류에서도 빈 묶음을 보내 감사 기록을 남긴다.
성공한 빈 감사는 같은 프로세스의 자동 재시도 중 중복 전송하지 않는다.
성공을 확인하지 못한 빈 요청은 다음 Gold 읽기 전에 같은 빈 본문으로 재시도한다.
응답 유실이나 프로세스 재시작 때 감사가 중복될 수 있다. C8에는 회차 키가 없다.

모델과 고정된 후보를 저장한 뒤, 요청 묶음을 inflight로 저장하고 POST한다.
한 묶음은 최대200개·64KiB다. 성공한 묶음마다 진행 위치를 저장한다.
같은 날짜 재실행은 미전송 묶음부터 재개한다. 재학습이나 새 모델 버전 생성은 하지 않는다.
응답 유실이나 성공 후 로컬 저장 실패에는 같은 요청 본문을 재전송한다.
전송 중인 광고의 category가 바뀌거나 목록에서 빠지면 publish_state_changed로 보류한다.
같은 대상일의 자동 게시를 막는다. 반영 여부는 미확정이며 이미 쓴 값을 되돌리지 않는다.
다음 날짜에는 옛 후보를 대체 기록으로 남기고 새 자료를 처리한다.

C8에는 기준version 검사가 없다. 다른 가중치 작성자가 있으면 옛 재시도가 새 값을 덮을 수 있다.
카테고리 재조회도 읽기와 쓰기 사이 변경을 원자적으로 막지는 못한다.

## 모델과 실행 기록

`STATE_DIR/models/v000001.joblib`부터 모델과 학습 이력을 함께 보관한다.
모델 파일은 덮어쓰거나 자동 삭제하지 않는다. `state.json`이 사용할 모델을 가리킨다.
잠금과 파일·폴더 fsync를 사용한다. 로컬 파일시스템의 원자 rename·hard link·fsync를 전제로 한다.
중단 시 참조되지 않는 모델 파일과 건너뛴 버전 번호가 남을 수 있다.
자동으로 가장 큰 번호를 선택하지 않는다.

Python major.minor·sklearn·numpy·joblib 버전이 다르면 joblib 로딩 전에 중단한다.
패키지를 임의로 올리지 않는다. 런타임 전환은 별도 상태 폴더와 재학습으로 진행한다.
joblib은 신뢰 가능한 이 프로그램의 파일만 읽는다.
모델만 옛 파일로 바꾸지 않는다. 모델·학습 이력·전송 상태가 함께 맞아야 한다.
자동 롤백 명령은 제공하지 않는다.

stdout은 JSON 줄 단위이며 `source=mlpregressor`를 포함한다.
실행 결과는 `runs/날짜_completed|deferred|failed|superseded.json`에도 저장한다.
같은 날짜·상태 파일은 최신 결과로 바뀐다. 모든 시도 로그는 외부 로그 수집으로 보관한다.
source와 통계는 로컬 기록에만 넣는다. 광고 서버에 별도 완료 알림을 보내지 않는다.

| 통계 | 의미 |
|---|---|
| planned_rows | 어제 Gold 원본 행 수 |
| valid_rows | 입력 검사를 통과한 행 수 |
| trained_rows | 성공적으로 저장한 모델에 사용한 행 수. 저장 전 실패와 dry-run은0 |
| invalid_rows | 입력 검사 제외 행 수 |
| invalid_ad_group_ids | 식별할 수 있는 제외 광고UUID 목록 |
| unidentified_rows | UUID가 잘못돼 식별할 수 없는 행 수 |
| write_excluded_rows/IDs | 학습은 가능하지만 현재 광고·category 조건으로 전송하지 않은 행 |
| write_counts | 확인한 응답의 created·changed·unchanged와 전송 중 제외 수 |

첫 changed 응답을 잃고 재시도하면 unchanged를 받을 수 있다.
write_counts는 서버에서 처음 실제 변경한 수를 뜻하지 않는다.
학습 모델 저장에 성공해도 API 전송은 실패할 수 있다. 로그의 상태와 trained_rows를 함께 본다.

## 검증과 한계

```sh
.venv/bin/python -m unittest discover -s tests -v
```

테스트는 로컬 Delta·실제 MLP·모의 HTTP를 사용한다. 실제 S3·광고 서버·DB에는 접속하지 않는다.
현재 C8 파일은 첨부된 Draft 계약이다. 실제 제공 여부는 배포 전 별도로 확인해야 한다.
이 모델은 승인 산식을 학습한다. 최적 가중치나 광고 성과 개선을 보장하지 않는다.
클릭수 입력·표본 신뢰도·과거30일 전환율 정정·밴딧은 추후 작업이다.
운영 배포와 스케줄러 등록, GitHub 게시도 수행하지 않는다.
