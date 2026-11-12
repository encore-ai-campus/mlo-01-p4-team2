"""광고 Silver 시험 자료(SL-8 계획 3판 결정 F (가)) — 표준 라이브러리만. 시험(test_rules·spark_adapter)이 함께 쓴다.

- REAL: 실제 Bronze 행(`bronze_values.json` — e2e·lab 덤프에서 고른 key·value 원문, 대상 그룹 전 8쌍). 만든 도구 temp/review/SL-8-runs/make_fixture.py.
- 광고 서버 원본을 파일 경로로 읽는다(Django import 없음): `schemas.py` 는 모듈로 불러 빌더를 쓰고, `api.py` 는 ast 로 읽어
  종류마다 `enqueue` 의 둘째·셋째 인자(원천 ID 필드·대상 키 필드)를 얻는다(리뷰 조건 C4 — 합성 행의 대상 키를 어댑터 표에서 가져오지 않는다).
  원천 ID 는 필드 하나(`fields["x"]`)이거나 schemas 함수 호출(`schemas.profile_source_id(fields["a"], fields["b"])` — 광고 카테고리, ACW-2)이다.
- made(): 빌더 페이로드를 실제 행의 봉투(Debezium 틀·source·키 모양)에 끼운 Bronze 행. 새 세 쌍(ad.decision 3·ad.segment_changed 1·
  ad.ad_group_profile_changed 1)과 안쪽에 segment_ids 가 있는 광고그룹 변경은 실제 Bronze 에 없어 이렇게 만든다(e2e 유입을 다시 돌리면 대상 그룹 판 행은 생기지만
  3판 결정·광고그룹 대상 모양은 생기지 않는다 — e2e_load.py:186, 리뷰 권고 R4).
"""
import ast
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVENTS = HERE.parents[2] / "ads_server" / "events"  # 저장소 뿌리/ads_server/events
REAL = json.loads((HERE / "bronze_values.json").read_text(encoding="utf-8"))
E2E = {"db": "ads_pipeline_e2e", "schema": "public", "table": "events_outbox"}
LAB = {**E2E, "db": "ads_pipeline_lab"}


def _schemas():
    spec = importlib.util.spec_from_file_location("ads_events_schemas", EVENTS / "schemas.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


schemas = _schemas()


def _field(arg):
    """`fields["x"]` → "x". 다른 모양이면 AssertionError."""
    assert isinstance(arg, ast.Subscript) and isinstance(arg.value, ast.Name) and arg.value.id == "fields" and isinstance(arg.slice, ast.Constant), ast.dump(arg)
    return arg.slice.value


def enqueue_fields():
    """api.py 의 `record_*` 가 부르는 `enqueue(schemas.X, <원천 ID>, fields["대상 키"], ...)` → {종류: (원천 ID, 대상 키 필드)}.
    원천 ID 는 `fields["x"]` → "x", `schemas.f(fields["a"], fields["b"])` → ("f", ("a", "b")). 모양이 다른 줄이 있으면 AssertionError(조용히 건너뛰지 않는다)."""
    found = {}
    for node in ast.walk(ast.parse((EVENTS / "api.py").read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "enqueue":
            kind, source, target = node.args[:3]
            assert isinstance(kind, ast.Attribute) and kind.value.id == "schemas", ast.dump(kind)
            if isinstance(source, ast.Call):
                call = source.func
                assert isinstance(call, ast.Attribute) and isinstance(call.value, ast.Name) and call.value.id == "schemas" and not source.keywords, ast.dump(source)
                assert callable(getattr(schemas, call.attr)), call.attr
                source_spec = (call.attr, tuple(_field(arg) for arg in source.args))
            else:
                source_spec = _field(source)
            name = getattr(schemas, kind.attr)
            assert name not in found, name
            found[name] = (source_spec, _field(target))
    return found


ENQUEUE = enqueue_fields()


def source_fields(event_name):
    """원천 ID 를 만드는 페이로드 필드들."""
    spec = ENQUEUE[event_name][0]
    return (spec,) if isinstance(spec, str) else spec[1]


def source_id(event_name, payload):
    """api.py 와 같은 원천 ID — 필드 값이거나 schemas 함수(예 profile_source_id)의 결과."""
    spec = ENQUEUE[event_name][0]
    return payload[spec] if isinstance(spec, str) else getattr(schemas, spec[0])(*(payload[f] for f in spec[1]))


def bronze_doc(name="DECISION", *, op=None, source=None, after=..., top=None, **fields):
    """실제 행의 봉투(dict)를 바꿔 돌려준다. fields 는 after 열, source 는 source 고치기, top 은 봉투 최상위 키 고치기, after=None 이면 after 를 null 로."""
    doc = json.loads(REAL[name]["value"])
    doc["op"] = op or doc["op"]
    doc["source"].update(source or {})
    doc.update(top or {})
    doc["after"].update(fields)
    if after is None:
        doc["after"] = None
    return doc


def made(event_name, payload, *, frame="DECISION", payload_text=None, **fields):
    """빌더 페이로드 → (key, value) 문자열. after: event_id = schemas.event_id_of(종류, 원천 ID), aggregate_id = 대상 키 값(api.py 에서 읽은 필드),
    schema_version = schemas.schema_version_of, payload = JSON 문자열(jsonb 출력처럼 ", "·": "). 봉투·created_at·source 는 실제 행 `frame` 의 것.
    fields 로 after 열을 덮을 수 있다(계약 위반 행을 만들 때)."""
    target_field = ENQUEUE[event_name][1]
    event_id = str(schemas.event_id_of(event_name, source_id(event_name, payload)))
    after = {"event_id": event_id, "event_name": event_name, "schema_version": schemas.schema_version_of(event_name, payload),
             "aggregate_id": payload[target_field], "payload": payload_text or json.dumps(payload, ensure_ascii=False, separators=(", ", ": "))}
    doc = bronze_doc(frame, **{**after, **fields})
    key = json.loads(REAL[frame]["key"])
    key["payload"] = {"event_id": doc["after"]["event_id"]}
    return json.dumps(key, separators=(",", ":")), json.dumps(doc, ensure_ascii=False, separators=(",", ":"))


T = datetime(2026, 11, 9, 1, 2, 3, 456789, tzinfo=timezone.utc)
T_WHOLE = datetime(2026, 11, 9, 1, 2, 4, tzinfo=timezone.utc)  # 소수 0자리(_iso)
U = ["01a1c0d0-0000-7000-8000-%012d" % i for i in range(20)]  # 합성 ID(UUIDv7 모양, 소문자)
SEG_A, SEG_B = U[10], U[11]


def selected(target_segment_ids=None):
    pick = {"campaign_id": U[1], "ad_group_id": U[2], "creative_id": U[3], "charge_shares": {"IMPRESSION": 98, "CLICK": 1, "CONVERSION": 1},
            "bid_id": U[4], "bid_version": 1, "bid_policy_id": U[5], "policy_version": 2, "bid_amount": 300, "applied_amount": 300}
    return pick if target_segment_ids is None else {**pick, "target_segment_ids": target_segment_ids}


def decision(n, *, subject_id="63e4f876-3eda-456f-974b-e4203c414b1e", picked=..., segment=..., active=True, rule="segment/v1"):
    """결정 3판(rule 이 있으면) 페이로드. n 은 decision_id 를 가른다."""
    return schemas.decision_payload(
        decision_id="01a1c0d1-0000-7000-8000-%012d" % n, media_id=U[6], slot_id=U[7], decided_at=T, audience="individual", subject_id=subject_id,
        selected=selected([SEG_B, SEG_A]) if picked is ... else picked, selection_policy="weighted-by-bid/v1", candidate_count=3,
        rejected_counts={}, segment_rule=rule, segment_id=SEG_A if segment is ... else segment, segment_active=active if segment is not None else None)


def segment(n, version, is_active):
    return schemas.segment_changed_payload(segment_id=SEG_A, segment_version_id="01a1c0d2-0000-7000-8000-%012d" % n, version=version, media_id=U[6],
                                           code="mlo", name="MLO 과정", is_active=is_active, changed_at=T_WHOLE if version == 1 else T)


def ad_group(n, change_type, previous_value, new_value):
    return schemas.ad_group_changed_payload(ad_group_id=U[2], campaign_id=U[1], change_id="01a1c0d3-0000-7000-8000-%012d" % n, change_type=change_type,
                                            previous_value=previous_value, new_value=new_value, changed_at=T)


def profile(version, category, weights=(0, 0, 0)):
    """광고 카테고리·가중치 판(ACW-1·ACW-3). 광고 = U[2]."""
    mlo, aio, skn = weights
    return schemas.ad_group_profile_changed_payload(ad_group_id=U[2], version=version, category=category, weight_mlo=mlo, weight_aio=aio, weight_skn=skn,
                                                    changed_at=T)


MADE = {  # 결정 F (가)의 모양(계획 §3 F 끝) — 이름 → (종류, 페이로드)
    "D3_ON": ("ad.decision", decision(1)),  # 그룹 켜짐·광고 있음(target_segment_ids 둘, 정렬)
    "D3_OFF": ("ad.decision", decision(2, active=False, picked=selected([]))),  # 요청 그룹이 꺼짐
    "D3_NO_GROUP": ("ad.decision", decision(3, segment=None, picked=selected([]))),  # 요청에 그룹 없음 — segment_* 둘 null
    "D3_NO_AD": ("ad.decision", decision(4, picked=None)),  # 광고 없음 — selected null
    "D3_NO_SUBJECT": ("ad.decision", decision(5, subject_id=None)),  # subject_id null
    "SEG_FIRST": ("ad.segment_changed", segment(1, 1, True)),  # 첫 판 켜짐(시각 소수 0자리)
    "SEG_OFF": ("ad.segment_changed", segment(2, 2, False)),  # 다음 판 꺼짐
    # 광고그룹 변경 값은 campaigns/services.py:570(CREATED)·:633(SEGMENTS_CHANGED)의 모양
    "AG_CREATED": ("ad.ad_group_changed", ad_group(1, "CREATED", None, {"enabled": True, "slot_ids": [U[7]], "creative_id": None, "segment_ids": [SEG_A, SEG_B]})),
    "AG_SEGMENTS": ("ad.ad_group_changed", ad_group(2, "SEGMENTS_CHANGED", {"segment_ids": [SEG_A]}, {"segment_ids": [SEG_A, SEG_B]})),
    "PROFILE_NEW": ("ad.ad_group_profile_changed", profile(1, None)),  # 광고 만들기 — 분류 전·가중치 0
    "PROFILE_SET": ("ad.ad_group_profile_changed", profile(2, 12, (30, 20, 0))),  # C8 쓰기 뒤 판 — 원천 ID "<광고>/2"
}
