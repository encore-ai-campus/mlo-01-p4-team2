# Laya 광고 카테고리 분류

광고 서버에서 광고를 받아 URL을 요약하고, 실제 Laya 모델로 분류합니다.
분류 결과는 광고 서버의 카테고리 API로 보냅니다.
DB·Outbox·version·changed_at은 광고 서버가 처리합니다.

현재 광고 API는 **127.0.0.1 또는 ::1의 모의 서버만 허용**합니다.
실서버 `192.168.0.204`에 접속하지 않습니다.
OpenAI 키는 빈 값이며 실제 OpenAI 호출도 시험하지 않았습니다.

## 실행 준비

프로젝트 디렉터리에서 아래 명령을 실행합니다.
Python 3.12를 사용합니다. 의존성 버전은 제공받은 모델 요구 파일을 따릅니다.

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
cp .env.example .env
```

`.env.example`의 키는 모두 비어 있습니다.
`.env`보다 이미 설정된 환경변수가 우선합니다.

| 변수 | 역할 |
|---|---|
| `ADS_API_BASE_URL` | `/api/internal/v1`을 포함한 모의 광고 서버 주소 |
| `ADS_INTERNAL_KEY` | 광고 API의 `X-Internal-Key` 값 |
| `OPENAI_API_KEY` | OpenAI 전용 키. 현재 빈 값 |
| `LAYA_MODEL_DIR` | 기본값 `models/laya` |

키·모델·작업 기록·가상환경은 Git에서 제외합니다.
`.gitignore`는 이미 추적한 비밀값이나 강제 추가를 막지는 못합니다.

## 외부 호출 없는 전체 시연

```sh
.venv/bin/python -m examples.offline_demo
```

이 시연은 모의 광고 HTTP 서버와 모의 OpenAI 응답을 사용합니다.
Laya 가중치와 토크나이저는 실제 파일을 사용합니다.
환경변수에 OpenAI 키가 있어도 시연에서는 사용하지 않습니다.

광고 4개 중 2개를 저장하고, 실패 2개를 광고 ID와 함께 기록합니다.
실패 사례는 URL 읽기 실패와 조회 후 광고 삭제입니다.
삭제된 광고의 400 오류를 제외한 뒤 나머지를 다시 보냅니다.
모의 광고 서버의 저장소는 메모리이며 실제 DB를 변경하지 않습니다.

## 실제 요약 경로와 예약 진입점

```sh
.venv/bin/python -m laya_python run
.venv/bin/python -m laya_python run --full
.venv/bin/python -m laya_python schedule
```

`run`은 미분류 광고를, `run --full`은 조회 대상 전체를 처리합니다.
현재 빈 키 설정에서는 광고 조회 전에 실행을 중단합니다.
실제 요약 경로는 OpenAI Responses API의 `gpt-6-sol`, 추론 `low`를 사용합니다.
`web_search`의 지정 URL 열람 근거를 확인합니다. 요약 본문은 최대 2,000자입니다.

`schedule`은 실행 중인 프로세스에서 매일 KST 22시를 기다립니다.
KST 22시는 UTC 13시입니다. OS 예약 작업을 등록하지 않습니다.
로컬 프로세스가 동시에 실행되면 파일 잠금에서 기다립니다.
요약은 최대 2개, Laya 추론은 한 번에 하나씩 수행합니다.

## 저장과 결과

- 정상 결과는 최대 200개씩 보냅니다. 요청에는 광고 ID와 정수 category만 넣습니다.
- `created`·`changed`·`unchanged` 모두 저장 확인 성공입니다.
- 400 응답이 지목한 광고를 제외하고 나머지를 다시 보냅니다.
- 일시 오류는 최초 호출을 포함해 최대 3회 재시도합니다.
- 저장 여부가 불확실하면 `pending_write_ids`를 남기고 실행을 중단합니다.
- 실패를 카테고리 0으로 저장하지 않습니다. 0은 분류에 성공한 기타 광고입니다.

진행 기록과 최종 결과는 `var/jobs/<job_id>.json`에 있습니다.
최상위 `source`는 항상 `laya`입니다. 광고 API 요청에는 `source`를 넣지 않습니다.
결과에는 성공·실패 건수와 실패 광고 ID가 있습니다.
본문·제목·URL·키는 기록하지 않습니다.

`counts_final=false`이면 최종 집계를 확정할 수 없습니다.
이전 실행이 중단됐다면 다음 시작 때 `interrupted`로 표시합니다.
기존 집계와 미확인 광고 ID를 보존하며 자동 재개하지 않습니다.
광고 서버 저장과 로컬 JSON 교체는 하나의 트랜잭션이 아닙니다.

종료 코드는 성공 0, 광고별 실패 포함 1, 실행 오류 2, 중단 130입니다.

## 모델 파일

모델 파일은 Git에 포함하지 않습니다. 받은 파일을 아래 구조로 준비합니다.
원본을 고치지 않고 `models/laya/`에 실행 사본을 둡니다.

```text
models/laya/
├── model.safetensors
├── task.json
├── manifest.json
├── rl_agent_config.json
├── encoder/config.json
└── tokenizer/
    ├── tokenizer.json
    ├── tokenizer_config.json
    └── special_tokens_map.json
```

| 원본 | 용도 |
|---|---|
| `/Users/sindong-yeob/Downloads/laya-model/` | 설정·task·manifest와 분류 코드 |
| `/Users/sindong-yeob/Downloads/model.safetensors` | 학습된 가중치 |
| 사용자 첨부 `tokenizer.json` | 토크나이저 |

task와 가중치의 고정 해시, manifest의 모든 파일 해시를 검사합니다.
원본 질문과 A/B 선택지를 그대로 사용합니다.
각 입력은 질문·선택지를 포함해 1,024토큰 이하입니다.
카테고리별 점수가 0.5 이상이면 해당 비트를 켭니다.
분류기는 한 번 로드하여 재사용합니다. CPU 또는 CUDA를 사용합니다.
`vendor/laya_export`에 원본 코드·출처·라이선스를 보존합니다.

## 시험

```sh
.venv/bin/python -m unittest discover -s tests -v
LAYA_RUN_MODEL_TEST=1 .venv/bin/python -m unittest tests.test_integration -v
```

기본 시험은 모의 응답과 시험용 모델 객체를 사용합니다.
실제 가중치를 읽는 시험은 명시한 환경변수로 별도 실행합니다.
실제 모델 자체 시험은 `tests.test_model.ActualModelTests`입니다.

| 단계 | 검토 결과 | 시험 |
|---|---|---|
| P1 설정·전송 | 통과 | 15개 통과 |
| P2 C8 클라이언트 | 통과 | 24개 통과 |
| P3 요약 | 통과 | 11개 통과 |
| P4 실제 모델 | 통과 | 7개 통과 |
| P5 JSON·전체 흐름 | 통과 | 12개 통과 |
| P6 실행·통합 | 통과 | CLI 5개·실제 모델 통합 1개 통과 |

기본 시험은 75개 중 71개를 통과했습니다. 실제 모델 시험 4개는 별도 실행 대상으로 건너뜁니다.
별도 통합시험은 CPU에서 광고 4개 중 성공 2개·실패 2개를 확인했습니다.
실패 2개는 시연에서 의도한 사례입니다. 마지막 8개 입력 길이는 128~153토큰이었습니다.

[라우팅 계약](docs/ROUTING.md), [상세 구현 계획](docs/IMPLEMENTATION.md), [광고 서버 C8 원문](docs/contracts/internal.openapi.yaml)을 따릅니다.
실제 광고 서버·OpenAI 연동과 광고 분류 정확도는 검증하지 않았습니다.
