# Laya 1,024토큰 최종 분류 모델

v1·v2·v3 데이터로 학습한 최종 3에폭 체크포인트입니다.
문서 한 건에 라벨 여러 개를 선택할 수 있습니다.

## 현재 PC에서 실행

PowerShell을 열고 아래 명령을 실행합니다.

```powershell
cd "$env:USERPROFILE\Desktop\laya-model"
.\run.cmd
```

위 명령은 `examples/input.json`의 예제 문서를 분류합니다.
현재 PC에서는 옆 폴더 `machine-learning`의 기존 Python 환경을 사용합니다.
패키지 안에 `.venv`를 만들면 해당 환경을 먼저 사용합니다.

일반 텍스트 파일은 제목과 함께 전달합니다.
파일은 UTF-8로 저장합니다.

```powershell
.\run.cmd --title "문서 제목" --body-file "C:\문서\본문.txt" --output result.json
```

README 같은 Markdown 파일에는 `--markdown`을 붙입니다.
학습 때와 같은 방식으로 팀 소개, 표, 코드 블록 등을 정리합니다.

```powershell
.\run.cmd --title "프로젝트 소개" --body-file "C:\프로젝트\README.md" --markdown --output result.json
```

JSON 입력은 `title`과 `body` 문자열을 담습니다.

```json
{"title": "프로젝트 소개", "body": "Kafka와 Spark로 실시간 데이터 파이프라인을 구성합니다."}
```

```powershell
.\run.cmd --input-json input.json --output result.json
.\run.cmd --input-json input.json --device cpu
```

`--device auto`가 기본값입니다.
CUDA를 사용할 수 있으면 GPU를 사용하고, 그 외에는 CPU를 사용합니다.
출력의 `scores`는 0~1 범위의 라벨 점수입니다.
점수가 0.5 이상인 라벨을 `categories`에 담습니다.
각 라벨을 독립적으로 판단하므로 점수 합은 1이 될 필요가 없습니다.

## Python 코드에서 사용

이 폴더를 작업 경로 또는 Python 모듈 검색 경로에 추가합니다.
서비스에서는 분류기를 한 번 생성하고 재사용합니다.

```python
from classifier import LayaClassifier

classifier = LayaClassifier(device="auto")
result = classifier.predict(
    title="실시간 데이터 처리",
    body="Kafka로 이벤트를 수집하고 Spark로 변환한 뒤 MySQL에 적재합니다.",
)
print(result["categories"])
print(result["scores"])
```

Markdown 문자열에는 `classifier.predict(title, body, markdown=True)`를 사용합니다.
기본 모델 경로는 이 파일과 같은 폴더의 `model/`입니다.

## 다른 Python 환경에서 설치

검증에 사용한 Python 버전은 3.12입니다.
Python을 준비한 뒤 아래 명령으로 독립 실행 환경을 만듭니다.
설치 과정에는 인터넷 연결이 필요합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -X utf8 predict.py --input-json examples/input.json --device cpu
```

이 PC와 같은 CUDA 12.8 PyTorch 구성을 설치할 때는 아래 순서를 사용합니다.
NVIDIA 드라이버가 설치된 환경에서 실행합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install torch==2.10.0 --index-url https://download.pytorch.org/whl/cu128
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\run.cmd --input-json examples/input.json --device cuda
```

의존성을 설치하면 모델 추론은 로컬 파일만 사용합니다.

## 입력과 모델 정보

| 설정 | 값 |
|---|---|
| 원본 모델 | `convaiinnovations/laya-multilingual` |
| 최종 체크포인트 | `train-20261110T172451-8ade416a / epoch-003` |
| 최대 입력 길이 | 질문·선택지를 포함해 1,024토큰 |
| 제목 길이 | 앞 240자 |
| 본문 길이 | 앞 2,000자 |
| 질문별 최대 길이 | 256토큰 |
| 라벨 선택 기준 | 점수 0.5 이상 |

본문을 2,000자로 정리한 뒤, 질문과 선택지를 합쳐 토큰 길이에 맞춥니다.
따라서 문서 후반의 내용은 모델 입력에서 잘릴 수 있습니다.

라벨 정의와 질문 원문은 `model/task.json`에 있습니다.
학습 정보와 테스트 지표는 `MODEL_INFO.json`에 있습니다.
테스트 919건 기준 Micro F1은 79.02%, Macro F1은 77.13%입니다.
Micro 정밀도는 79.98%, Micro 재현율은 78.08%입니다.
지표는 저장된 AI 라벨 기준입니다.

## 파일 구성

- `model/`에 최종 가중치, 인코더 설정, 토크나이저, 라벨 정의가 있습니다.
- `classifier.py`에 Python 분류기가 있습니다.
- `predict.py`에 명령줄 실행 코드가 있습니다.
- `preprocess.py`에 Markdown 정리 코드가 있습니다.
- `run.cmd`에 Windows 실행 명령이 있습니다.
- `requirements.txt`에 검증한 의존성 버전이 있습니다.
- `examples/`에 예제 입력과 검증 결과가 있습니다.
- `MODEL_INFO.json`에 모델 출처와 학습 정보가 있습니다.
