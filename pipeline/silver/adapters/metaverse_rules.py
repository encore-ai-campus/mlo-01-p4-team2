"""메타버스 outbox 사건의 계약 확인과 내용 해시(계획 15 §2.2, SL-4). 표준 라이브러리와 pipeline.common 만 쓴다 — Spark 없이 시험하고,
executor 가 어댑터의 Python UDF(`adapter_result`)에서 import 한다.

계약은 메타버스 `backend/eventing/events.py`(c802671 — EO-1·EO-2, 22종)를 옮겼다: `TOPIC_BY_TYPE`(:25-48)·`EXTRA_FIELDS`(:49-75)·
`_BASE_FIELDS`(:76)·`SCHEMA_VERSION_BY_TYPE`(:77)·`CATALOG_UUID_FIELDS`(:80-83)·`_CHOICES`(:84-96)·`_COUNTS`(:98-99)·`_CODES`(:101-106)·
`_uuid_ok`(:132-139)·`validate_raw_data`(:142-190). 바꾼 점:
- 예외 대신 사유 문자열을 돌려준다. 사유에는 키 이름만 적고 값은 적지 않는다(원본도 내용을 기록하지 않는다).
- 필드 집합을 (종류, 판) 표 `FIELDS` 에서 받는다. 표 = 지금 발행자의 판(`SCHEMA_VERSION_BY_TYPE`, 나머지 1.0) + 수신측이 유지하는
  `quiz_answered` 1.0(`KAFKA_TOPICS.md:149` — 1.1 필드에서 `question_type` 을 뺀 것, `choice_uuid` 는 null 불가). 모두 23줄(`KAFKA_TOPICS.md:17`).
그 밖의 계약: `topic` 이 그 종류의 앱 토픽인가, `message_key` = `raw_data.user_uuid` 인가.

내용 해시: `content_sha256 = sha256(hop_rules.canonical_json(내용 8열 객체))`(15 §2.2, SL-3 결정 4). 8열은 `CONTENT_COLUMNS`(04 §25 —
배달 2열 `published_at`·`retry_count` 는 뺀다). JSON 열(`raw_data`·`payload_json`)은 해석한 값, 시각(`occurred_at`·`created_at`)은
UTC 마이크로초 6자리 문자열 `YYYY-MM-DDTHH:MM:SS.ffffffZ` 다. 행을 outbox 값(시각은 시간대가 있는 datetime)으로 받으므로, 메타버스 대사(MVR-1)가
DB 행으로 같은 함수를 부르면 바이트가 같은 결과가 나온다.
"""
import hashlib
import json
import math
import re
import uuid
from datetime import datetime, timezone

from pipeline.common import hop_rules

TOPIC_BY_TYPE = {
    "session_started": "raw.account",
    "session_ended": "raw.account",
    "chat_sent": "raw.chat",
    "post_created": "raw.board",
    "post_updated": "raw.board",
    "post_deleted": "raw.board",
    "post_read": "raw.board",
    "comment_created": "raw.comment",
    "comment_updated": "raw.comment",
    "comment_deleted": "raw.comment",
    "content_open_requested": "raw.content_usage",
    "quiz_answered": "raw.content_usage",
    "point_granted": "raw.points",
    "movement_observed": "raw.movement",
    "floor_transferred": "raw.movement",
    "event_progress_completed": "raw.event",
    "game_play_started": "raw.event",
    "game_play_ended": "raw.event",
    "manager_greeted": "raw.event",
    "manager_poked": "raw.event",
    "story_action_recorded": "raw.event",
    "learning_session_completed": "raw.event",
}
EXTRA_FIELDS = {
    "session_started": {"session_uuid"},
    "session_ended": {"session_uuid", "end_reason"},
    "chat_sent": {"message_uuid", "channel_code", "floor_uuid"},
    "post_created": {"post_uuid", "revision", "zone_uuid"},
    "post_updated": {"post_uuid", "revision", "zone_uuid"},
    "post_deleted": {"post_uuid", "revision", "zone_uuid"},
    "post_read": {"post_uuid", "revision", "zone_uuid"},
    "comment_created": {"comment_uuid", "post_uuid", "parent_comment_uuid", "revision"},
    "comment_updated": {"comment_uuid", "post_uuid", "parent_comment_uuid", "revision"},
    "comment_deleted": {"comment_uuid", "post_uuid", "parent_comment_uuid", "revision"},
    "content_open_requested": {"content_uuid", "category_uuid", "domain", "content_kind", "zone_uuid", "action"},
    "quiz_answered": {"attempt_uuid", "question_uuid", "content_uuid", "question_type", "choice_uuid", "is_correct"},
    "point_granted": {"ledger_uuid", "attempt_uuid", "delta", "balance_after"},
    "movement_observed": {"floor_uuid", "zone_uuid", "x", "y", "input_kind"},
    "floor_transferred": {"from_floor_uuid", "to_floor_uuid", "zone_uuid"},
    "event_progress_completed": {"progress_uuid", "event_definition_uuid", "event_kind", "completion_order"},
    "game_play_started": {"play_uuid", "game_uuid", "event_definition_uuid"},
    "game_play_ended": {"play_uuid", "game_uuid", "event_definition_uuid", "status", "duration_ms"},
    "manager_greeted": {"interaction_uuid", "event_definition_uuid", "manager_code"},
    "manager_poked": {"interaction_uuid", "event_definition_uuid", "manager_code", "poke_count"},
    "story_action_recorded": {"story_record_uuid", "story_key", "action_key", "action_kind"},
    "learning_session_completed": {"learning_session_id", "subject", "question_type", "difficulty", "total_questions", "correct_count"},
}
BASE_FIELDS = {"user_uuid", "session_uuid", "class_group_uuid"}
SCHEMA_VERSION_BY_TYPE = {"quiz_answered": "1.1"}
FIELDS = {(event_type, SCHEMA_VERSION_BY_TYPE.get(event_type, "1.0")): frozenset(BASE_FIELDS | extra) for event_type, extra in EXTRA_FIELDS.items()}
FIELDS[("quiz_answered", "1.0")] = frozenset(BASE_FIELDS | EXTRA_FIELDS["quiz_answered"]) - {"question_type"}  # KAFKA_TOPICS.md:149
CATALOG_UUID_FIELDS = frozenset({  # 카탈로그·정의 참조는 v4 또는 v5(결정적 ID — CS 학습 카탈로그 등), 행마다 만드는 ID 는 v4
    "content_uuid", "category_uuid", "zone_uuid", "floor_uuid", "from_floor_uuid", "to_floor_uuid", "class_group_uuid",
    "event_definition_uuid", "game_uuid",
})
_CHOICES = {
    "end_reason": {"LOGOUT", "SESSION_EXPIRED"},
    "channel_code": {"GLOBAL"},
    "domain": {"LEARNING", "REST"},
    "content_kind": {"EXTERNAL_LINK", "QUIZ"},
    "action": {"OPEN_REQUESTED"},
    "input_kind": {"KEYBOARD"},
    "question_type": {"MULTIPLE_CHOICE", "FILL_BLANK"},
    "event_kind": {"MAIN", "EASTER_EGG"},
    "status": {"SUCCESS", "FAILED", "INTERRUPTED", "FINISHED"},
    "subject": {"PYTHON", "SQL", "CS"},
    "difficulty": {"BEGINNER", "INTERMEDIATE", "ADVANCED"},
}
_COUNTS = {"completion_order": 1, "duration_ms": 0, "poke_count": 1, "learning_session_id": 1, "total_questions": 1, "correct_count": 0}  # 최솟값(모두 2**63 미만)
_CODES = {  # 앱이 정하는 코드: 모양과 열 길이만 본다(새 코드에 판을 올리지 않게 — action_kind 도 값 목록이 아니다)
    "manager_code": re.compile(r"manager-[a-z0-9_.:-]{1,56}"),
    "story_key": re.compile(r"[a-z0-9_.:-]{1,40}"),
    "action_key": re.compile(r"[a-z0-9_.:-]{1,100}"),
    "action_kind": re.compile(r"[a-z0-9_.:-]{1,40}"),
}
CONTENT_COLUMNS = ("outbox_uuid", "event_type", "topic", "message_key", "raw_data", "occurred_at", "payload_json", "created_at")
_UTC_RE = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]{1,6}))?Z")  # contracts._UTC_RE 와 같은 모양


def raw_data_reason(event_type, schema_version, raw_data):
    """`validate_raw_data` 를 옮긴 것. 반환: None(맞음) 또는 사유. (종류, 판)이 표에 없어도 사유다(어댑터는 그 전에 unknown_schema 로 보낸다)."""
    expected = FIELDS.get((event_type, schema_version))
    if expected is None:
        return "계약 표에 없는 (종류, 판)"
    if not isinstance(raw_data, dict):
        return "raw_data 가 JSON 객체가 아니다"
    if set(raw_data) != expected:
        missing, extra = sorted(expected - set(raw_data)), sorted(set(raw_data) - expected)
        return f"raw_data 필드가 계약과 다르다(빠짐 {json.dumps(missing, ensure_ascii=False)}, 더 있음 {json.dumps(extra, ensure_ascii=False)})"
    nullable = {"zone_uuid", "parent_comment_uuid"}
    if event_type == "chat_sent":
        nullable.add("floor_uuid")
    fill_blank = event_type == "quiz_answered" and raw_data.get("question_type") == "FILL_BLANK"  # 1.0 에는 question_type 이 없다
    if fill_blank:
        nullable.add("choice_uuid")
    if event_type == "event_progress_completed":
        nullable |= {"completion_order", "session_uuid"}  # 비동기 완료(광고 노출)는 접속을 모른다
    if event_type in {"game_play_started", "game_play_ended"}:
        nullable |= {"event_definition_uuid", "duration_ms"}
    for key, value in raw_data.items():
        if value is None and key in nullable:
            continue
        if key.endswith("_uuid"):
            try:
                parsed = uuid.UUID(value) if isinstance(value, str) else None
            except ValueError:
                parsed = None
            valid = parsed is not None and parsed.version in ((4, 5) if key in CATALOG_UUID_FIELDS else (4,)) and str(parsed) == value
        elif key == "revision":
            valid = type(value) is int and value >= 1
        elif key in {"delta", "balance_after"}:
            valid = type(value) is int and (0 < value < 2**63 if key == "delta" else 0 <= value < 2**63)
        elif key in _COUNTS:
            valid = type(value) is int and _COUNTS[key] <= value < 2**63
        elif key in _CODES:
            valid = isinstance(value, str) and _CODES[key].fullmatch(value) is not None
        elif key == "is_correct":
            valid = type(value) is bool
        elif key in {"x", "y"}:
            valid = type(value) in (int, float) and value >= 0
            if valid:
                try:
                    valid = math.isfinite(value)
                except OverflowError:
                    valid = False
        else:
            valid = isinstance(value, str) and value in _CHOICES.get(key, set())
        if not valid:
            return f"raw_data.{key} 값이 계약에 맞지 않는다"
    if fill_blank and raw_data["choice_uuid"] is not None:
        return "raw_data.choice_uuid 는 FILL_BLANK 에서 null 이어야 한다"
    if event_type == "learning_session_completed" and raw_data["correct_count"] > raw_data["total_questions"]:
        return "raw_data.correct_count 가 total_questions 보다 크다"
    return None


def contract_reason(event_type, schema_version, topic, message_key, raw_data):
    """outbox 한 행의 계약. 반환: None(맞음) 또는 사유."""
    if topic != TOPIC_BY_TYPE.get(event_type):
        return "topic 이 그 종류의 앱 토픽이 아니다"
    reason = raw_data_reason(event_type, schema_version, raw_data)
    if reason is None and message_key != raw_data["user_uuid"]:
        reason = "message_key 가 raw_data.user_uuid 와 다르다"
    return reason


def utc_micros(moment):
    """시간대가 있는 datetime → `YYYY-MM-DDTHH:MM:SS.ffffffZ`(UTC). 시간대가 없으면 ValueError(지역 시각으로 조용히 읽히지 않게).
    strftime 을 쓰지 않는다 — Spark Python worker 안에서 호출당 약 60µs(드라이버의 수십 배, 2026-11-04 cProfile)라 해시 UDF 비용의 대부분이었다."""
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(f"시간대가 없는 시각: {moment!r}")
    m = moment.astimezone(timezone.utc)
    return f"{m.year:04d}-{m.month:02d}-{m.day:02d}T{m.hour:02d}:{m.minute:02d}:{m.second:02d}.{m.microsecond:06d}Z"


def parse_utc(text):
    """Debezium ZonedTimestamp(UTC, 소수 0~6자리) → 시간대가 있는 datetime. 모양이 다르거나 없는 날짜면 ValueError."""
    match = _UTC_RE.fullmatch(text)
    if match is None:
        raise ValueError(f"UTC 시각 모양이 아니다: {text!r}")
    *parts, fraction = match.groups()
    return datetime(*map(int, parts), int((fraction or "").ljust(6, "0")), tzinfo=timezone.utc)  # 없는 날짜·시각은 ValueError


def content_sha256(row):
    """outbox 행(`CONTENT_COLUMNS` 를 키로 갖는 dict — JSON 열은 해석한 값, 시각은 시간대가 있는 datetime)의 내용 해시(소문자 16진 64자)."""
    content = {name: row[name] for name in CONTENT_COLUMNS}
    content["occurred_at"], content["created_at"] = utc_micros(row["occurred_at"]), utc_micros(row["created_at"])
    return hashlib.sha256(hop_rules.canonical_json(content).encode("utf-8")).hexdigest()


def adapter_result(outbox_uuid, event_type, schema_version, topic, message_key, raw_data, payload_json, occurred_at, created_at):
    """어댑터 UDF 의 몸통. 받는 값은 Spark 가 꺼낸 문자열이다(JSON 열은 원문, 시각은 Debezium 문자열).
    반환: (계약 사유 또는 None, content_sha256 또는 None) — 사유가 있으면 해시는 None. 어떤 입력에도 예외를 내지 않는다:
    Spark 는 조건식 안의 Python UDF 도 모든 행에 부르므로, 앞 순서에서 이미 오류로 간 행(값 없음·JSON 아님)도 여기 온다."""
    values = (outbox_uuid, event_type, schema_version, topic, message_key, raw_data, payload_json, occurred_at, created_at)
    if not all(isinstance(value, str) for value in values):
        return "꺼낸 값이 없다", None
    try:
        raw, envelope = json.loads(raw_data), json.loads(payload_json)
        reason = contract_reason(event_type, schema_version, topic, message_key, raw)
        if reason is not None:
            return reason, None
        row = {"outbox_uuid": outbox_uuid, "event_type": event_type, "topic": topic, "message_key": message_key, "raw_data": raw,
               "occurred_at": parse_utc(occurred_at), "payload_json": envelope, "created_at": parse_utc(created_at)}
        return None, content_sha256(row)
    except (ValueError, RecursionError) as error:  # JSON 아님·NaN(엄격 검사가 먼저 decode 로 보냄)·시각 모양
        return f"값을 해석하지 못했다: {type(error).__name__}", None
