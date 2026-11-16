# CP01 데이터와 설정 및 산식

## 1. 진입과 종료

진입 조건은 D0 문서 검토 PASS와 전체 문서 최종 검토 PASS다.
계획 작성은 D0 뒤에 수행하지만, 구현은 전체 문서 최종 검토 뒤에만 시작한다.
이 단계는 원격 호출 없이 입력 계약과 수치 계산을 완성한다.

종료 조건은 코드 검토 PASS → 정적·계약 검증 통과 → 해당 테스트 통과다.
테스트 실패로 코드를 수정하면 변경분을 다시 검토받는다.

## 2. 산출물과 책임

- `settings.py`: .env 읽기, 설정 검사, 비밀값 없는 환경 식별 정보.
- `errors.py`: 고정 오류 코드, 일시적 여부, 보류 여부, Retry-After, 제한된 진단 정보.
- `data.py`: Gold snapshot 읽기, 날짜·스키마·행 검사, 입력14 구성과 제외 통계.
- `model.py`: 승인된 정답 산식과 저장용 정수 가중치 변환. 실제 학습은 CP02다.
- `tests/test_data.py`, `tests/test_settings.py`, `tests/test_scores.py`.
- 의존성 정의·잠금 파일, `.env.example`, `.gitignore`.

API 클라이언트, 모델 파일, 예약 실행, API 쓰기는 만들지 않는다.

## 3. 설정 계약

| 설정 | 의미 |
|---|---|
| ADS_INTERNAL_BASE_URL | 광고 서버 `/api/internal/v1` 기본 주소 |
| ADS_INTERNAL_KEY | X-Internal-Key의 값. 불투명한 비밀 문자열 |
| GOLD_DELTA_URI | Gold v2 Delta root. 로컬 경로는 오프라인 검증에 사용 |
| S3_STORAGE_OPTIONS | 선택적인 문자열→문자열 JSON 객체 |
| STATE_DIR | 모델·진행 상태의 전용 디렉터리. 상대 경로는 프로젝트 기준 |
| HTTP_TIMEOUT | 양수의 유한한 초. 기본15초 |
| TRAIN_EPOCHS | 양수 반복 횟수. 기본100회. 최적 성능을 뜻하지 않음 |

환경변수는 .env보다 우선한다. 실제 키를 예제나 로그에 쓰지 않는다.
API는 HTTPS를 사용한다. HTTP는 로컬 모의 시험의 loopback 주소만 허용한다.
API 주소의 사용자정보·질의·fragment와 제어문자를 거절한다.
키의 빈 값과 줄바꿈·제어문자를 거절한다. 키 내부 구조는 해석하지 않는다.
Gold URI에 포함된 인증정보·질의·fragment도 거절한다.

환경 식별은 정규화한 API 기본 주소와 Gold root를 기본으로 한다.
S3 endpoint를 따로 지정하면 그 endpoint도 포함한다. 다른 저장소 혼용을 막기 위한 정보다.
키·AWS 비밀값·전체 storage_options는 식별 정보에 넣지 않는다.
endpoint 주소에 인증정보가 포함되면 저장하지 않고 설정 오류로 거절한다.
키 교체만으로 환경이 달라지지 않는다. CP02가 이 식별 정보를 상태와 대조한다.

## 4. Gold와 입력 계약

대상일은 CP04가 전달한 KST 어제 날짜다. data.py는 날짜를 독자적으로 다시 계산하지 않는다.
DeltaTable 하나를 생성해 해당 snapshot/version과 대상일 행을 함께 반환한다.
Parquet glob을 사용하지 않는다. 날짜와 필요한 열만 읽는다.

입력에 필요한 열10개를 검사한다.
`kst_date(date)`, `ad_group_id(string)`, `category(int)`, `confirmed(boolean)`와 비율6개(double)다.
실제 Delta의 논리형을 검사한다. 잘못된 스키마는 전체 계약 오류다.
클릭수3개는 source 계약에 존재하지만 ML은 읽거나 신뢰도 계산에 사용하지 않는다.
추가 source 열은 무시한다.

입력 순서는 다음으로 고정한다.

```text
mlo_ctr, mlo_cvr, aio_ctr, aio_cvr, skn_ctr, skn_cvr,
category_bit_0, category_bit_1, category_bit_2, category_bit_3,
category_bit_4, category_bit_5, category_bit_6, category_bit_7
```

- category=NULL은 제외한다. 0은 유효한 기타다.
- category는 0~255 정수다. bool·상위 범주 비트는 허용하지 않는다.
- 비율 NULL, bool, 비유한 값,0미만·1초과 값은 제외한다.
- UUID는 계약의 하이픈 형식으로 검사한 뒤 소문자로 정규화한다. URN·괄호 형식은 제외한다.
- 같은 날짜의 정규화 UUID가 중복되면 전체 작업을 중단한다.
- 예상과 다른 날짜가 반환되면 전체 계약 오류다.
- confirmed=false 행이 하나라도 있으면 그 날짜 학습 전체를 보류한다. CP04가 재시도한다.
- confirmed의 잘못된 형은 전체 계약 오류다.

통계는 `planned_rows`, `valid_rows`, `invalid_rows`, 제외 사유별 건수를 반환한다.
planned_rows는 대상일 원본 행 수이며, valid_rows+invalid_rows와 일치해야 한다.
제외 ID는 유효한 UUID만 중복 없이 기록한다. ID를 식별할 수 없는 행 수는 별도로 기록한다.
`trained_rows`는 이 단계에서0이다. CP02의 성공 상태 저장 후 실제 건수로 확정한다.

빈 원본·미확정 데이터·필터 후 유효행0개를 서로 구분한다.
앞의 두 조건은 일시적 보류다. 정상 NULL 제외만으로 유효행0개이면 자동 반복하지 않는 보류다.
어느 경우에도 새 모델이나 학습 완료일을 만들지 않는다.

## 5. 산식과 출력 변환

과정별 점수는 `s=CTR*(1+4*CVR)`다.
전환된 클릭에 클릭1점과 전환 보너스4점을 부여한 기준이다.
정답은 과정별 `s/sum(s)`다. 전부0점이면 `[0,0,0]`이다.
이 산식은 사람의 운영 규칙이며 최적 가중치를 검증한 결과가 아니다.

모델 출력은 확률로 간주하지 않는다.
길이3의 유한한 실수만 받고, 각각0~1로 제한한 뒤100을 곱한다.
합계가100을 넘으면 비율대로 줄인다. 이후 소수점 .5는 올리는 반올림을 적용한다.
반올림 뒤 합계가100을 넘으면 과하게 반올림한 값부터1씩 뺀다.
동률은 MLO→AIO→SKN 순서다. 모두0도 유효하다.
결과는 Python int3개, 각각0~100, 합계100이하여야 한다.

## 6. 검증과 테스트

코드 검토 PASS 이후 아래 순서로 수행한다.

1. Python 컴파일·설정 예제·비밀값 포함 여부를 정적으로 확인한다.
2. 계약의 필드명·순서·형과 함수를 대조한다.
3. 설정의 정상값·잘못된 주소·키 제어문자·환경 정규화를 테스트한다.
4. 실제 로컬 Delta에 이틀·두 광고 자료를 저장하고 대상일 필터와 version을 확인한다.
5. 비트0/7, NULL과0, 잘못된 비율·UUID·중복 키·스키마 오류를 테스트한다.
6. 빈 원본·미확정·유효0행의 서로 다른 판정을 테스트한다.
7. 점수1대5와 정수 합계 조건을 경계값·고정 난수 표본으로 테스트한다.

원격 광고 API와 실제 S3를 부르지 않는다.
통과 증거는 `docs/reviews/CP01-code.md`와 `CP01-validation.md`에 남긴다.
