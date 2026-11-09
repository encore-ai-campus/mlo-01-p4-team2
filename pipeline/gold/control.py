"""Gold 잡의 드라이버 쪽 판단(계획 temp/pipeline/41_ads-gold-plan.md r3d — 결정 AG1~AG10, 리뷰 r3c C3): 표준 라이브러리와 pipeline.* 만 쓴다(Spark 없이 시험).

- 시각은 모두 UTC epoch 정수다(시간 칸 = epoch 초 // 3600, 기준 시각 = epoch 마이크로초). collect 한 timestamp 가 드라이버 프로세스 시간대의
  naive 값이 되는 일을 피한다(Silver reconcile 과 같은 까닭).
- 미확정 시간(AG5 (가)): Silver 대사 창 w 가 `mismatch` 면 w 와 직전 시간(판정 지연 상한 — 보류 300초 + 다시 판정 30초가 한 시간 안).
  창이 없는 줄 가운데 Gold 숫자에 닿는 것은 `unplaced` 뿐이다(리뷰 r2 코드 Step 참고 ① — 겹침은 그 ID 창도 mismatch, 목록-Bronze·오류-Bronze 는
  본 표를 바꾸지 않음). `unplaced` 가 mismatch 면 어느 시간인지 몰라 모든 칸이 미확정이다.
- 기준 시각(C3 + 리뷰 r3d 권고 1): min(고정한 본 표의 최대 produced_at, 그 대사 회차의 reconciled_at) — 둘 다 고정한 판에서 나와 같은 판 → 같은 값.
  직전 Gold 판의 기준 시각보다 뒤로 가지 않는다(본 표의 최대 행이 격리로 빠져도 이미 쓴 칸이 "없어짐"으로 나갔다 돌아오지 않게).
"""
import hashlib
import re
from collections import namedtuple
from pathlib import Path

from pipeline.silver import control as silver_control

HOUR = 3600
KST_OFFSET = 9 * HOUR  # KST 는 일광 절약 시간이 없다 — 늘 +9시간
DAY = 24 * HOUR
WINDOW, UNPLACED, MISMATCH = "window", "unplaced", "mismatch"  # Silver reconcile 결과 줄의 kind·status(pipeline/silver/reconcile.py 와 같은 글자)
REASONS = ("generation_changed", "gold_release_changed", "silver_changed")  # AG8 (가) — 이 순서로 가린다
_ENV = ("GOLD_LAKE_ROOT", "GOLD_CODE_ROOT")
_NAME = re.compile(r"[a-z0-9-]+")
Unconfirmed = namedtuple("Unconfirmed", "everything hours any_window")
JobSettings = namedtuple("JobSettings", "domain root code_root")


def unconfirmed(rows):
    """대사 결과 줄 (kind, 창 시작 epoch 시간 또는 None, status) → Unconfirmed(everything — 모든 칸 미확정, hours — 미확정 epoch 시간 집합,
    any_window — 창 mismatch 가 하나라도 있나(C1·C2 의심 시간을 더할지)."""
    hours, everything, any_window = set(), False, False
    for kind, hour, status in rows:
        if status != MISMATCH:
            continue
        if kind == WINDOW:
            hours.update((hour, hour - 1))
            any_window = True
        elif kind == UNPLACED:
            everything = True
    return Unconfirmed(everything, frozenset(hours), any_window)


def kst_day(hour):
    """epoch 시간 → KST 날짜의 epoch 일 번호(1970-01-01 = 0)."""
    return (hour * HOUR + KST_OFFSET) // DAY


def cutoff(max_produced_us, reconciled_us, previous_us):
    """기준 시각(epoch 마이크로초 또는 None — 사건이 없음). 모듈 설명."""
    present = [value for value in (max_produced_us, reconciled_us) if value is not None]
    value = min(present) if len(present) == 2 else None  # 본 표가 비면 최대 produced_at 이 없다 — 쓸 칸도 없다
    known = [v for v in (value, previous_us) if v is not None]
    return max(known) if known else None


def reason(before, after):
    """정정 이유(AG8 (가)) — 두 회차 기록(커밋 메타데이터 dict)을 견준다."""
    if before["silver_generation"] != after["silver_generation"]:
        return REASONS[0]
    if before["gold_release"] != after["gold_release"]:
        return REASONS[1]
    return REASONS[2]


def job_settings(environ, domain):
    """Gold 잡 설정: 환경변수, 기본값 없음 — 하나라도 없거나 비면 ValueError(모두 적음)."""
    if not _NAME.fullmatch(str(domain)):
        raise ValueError(f"domain 은 [a-z0-9-]+ 다: {domain!r}")
    missing = [name for name in _ENV if not environ.get(name, "").strip()]
    if missing:
        raise ValueError(f"Gold 잡 설정이 없다(기본값 없음): {', '.join(missing)}")
    silver_control._lake_path("GOLD_LAKE_ROOT", environ["GOLD_LAKE_ROOT"])
    return JobSettings(domain, environ["GOLD_LAKE_ROOT"], environ["GOLD_CODE_ROOT"])


def table_path(root, domain, name, version):
    """Gold 표 자리(계획 41 §2.2): `<root>/gold/<원천>/<표>/v<판>`."""
    return f"{root.rstrip('/')}/gold/{domain}/{name}/v{version}"


def corrections_path(root, domain):
    """정정 기록 표(AG4): `<root>/gold/<원천>/corrections` — 원천의 Gold 표 모두가 한 표에(줄마다 표 이름)."""
    return f"{root.rstrip('/')}/gold/{domain}/corrections"


def gold_release(root=None):
    """Gold 코드 판: `gold@` + SHA-256 앞 12자(silver_release 와 같은 방식). 입력은 Gold 가 import 하는 코드 — `pipeline/gold/` 아래 *.py,
    `pipeline/silver/` 바로 아래 *.py(포인터·계약·틀 — 어댑터는 Gold 가 부르지 않음), `pipeline/common/hop_rules.py`. 정렬한 상대 경로마다 경로·길이·바이트."""
    root = Path(root) if root is not None else Path(__file__).resolve().parents[2]
    gold = sorted((root / "pipeline" / "gold").rglob("*.py"))
    if not gold:
        raise FileNotFoundError(f"{root / 'pipeline' / 'gold'} 아래에 *.py 가 없다 — 코드 뿌리 설정을 확인한다")
    files = gold + sorted((root / "pipeline" / "silver").glob("*.py")) + [root / "pipeline" / "common" / "hop_rules.py"]
    digest = hashlib.sha256()
    for path in files:
        data = path.read_bytes()
        digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0" + str(len(data)).encode("ascii") + b"\0" + data)
    return "gold@" + digest.hexdigest()[:12]
