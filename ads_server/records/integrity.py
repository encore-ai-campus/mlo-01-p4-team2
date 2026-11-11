"""이벤트 누락 점검(계획 4-2): 원본 행(결정·접수·판정·원장 CHARGE·입찰 정책 버전·입찰 버전·캠페인·광고그룹 변경 로그·대상 그룹 판·광고 카테고리 판)마다 이벤트가 outbox 나 MongoDB 에 있는지 본다.

**원본 행을 먼저 읽고 이벤트를 나중에 조회한다**: 이벤트는 원본 행과 같은 트랜잭션에 쓰이므로, 읽은 원본 행의 이벤트는 그 뒤 조회에서 반드시 보인다.
순서를 거꾸로 하면 점검 도중 새로 생긴 행이 거짓 누락으로 보인다.
"""
from itertools import batched

from billing import api as billing
from campaigns import api as campaigns
from events import api as events
from masters import api as masters

from .models import Decision, Reaction

CHUNK = 2000


def _sources(stats):
    """(원천 종류, 원천 ID, 이벤트 이름) 을 훑는다. `stats` 에 원천 종류별로 **실제로 훑은 행 수**를 센다(점검 범위의 증거)."""
    for decision_id in Decision.objects.values_list("decision_id", flat=True).iterator(chunk_size=CHUNK):
        stats["decision"] = stats.get("decision", 0) + 1
        yield "decision", decision_id, events.NAMES["decision"]
    for inbox_id, status in Reaction.objects.values_list("inbox_id", "status").iterator(chunk_size=CHUNK):
        stats["reaction"] = stats.get("reaction", 0) + 1
        yield "reaction", inbox_id, events.NAMES["reaction_received"]
        if status != "RECEIVED":
            yield "reaction", inbox_id, events.NAMES["reaction"]
    for entry_id in billing.charge_entry_ids():
        stats["ledger_charge"] = stats.get("ledger_charge", 0) + 1
        yield "ledger_charge", entry_id, events.NAMES["charge"]
    for p in masters.policy_versions():
        stats["bid_policy"] = stats.get("bid_policy", 0) + 1
        yield "bid_policy", p.bid_policy_id, events.NAMES["bid_policy_changed"]
    for b in campaigns.bid_versions():
        stats["bid"] = stats.get("bid", 0) + 1
        yield "bid", b.bid_id, events.NAMES["bid_added"]
    for v in campaigns.change_rows(campaigns.ChangeTarget.CAMPAIGN):
        stats["campaign_change"] = stats.get("campaign_change", 0) + 1
        yield "campaign_change", v.change_id, events.NAMES["campaign_changed"]
    for v in campaigns.change_rows(campaigns.ChangeTarget.AD_GROUP):
        stats["ad_group_change"] = stats.get("ad_group_change", 0) + 1
        yield "ad_group_change", v.change_id, events.NAMES["ad_group_changed"]
    for v in masters.segment_versions():
        stats["segment_version"] = stats.get("segment_version", 0) + 1
        yield "segment_version", v.segment_version_id, events.NAMES["segment_changed"]
    for v in campaigns.profile_rows():
        stats["ad_group_profile"] = stats.get("ad_group_profile", 0) + 1
        yield "ad_group_profile", events.profile_source_id(v.ad_group_id, v.version), events.NAMES["ad_group_profile_changed"]  # 원천 ID 가 (광고, 판) 문자열이다


def event_gaps(stats=None):
    """이벤트가 없는 원본 행을 NDJSON 레코드(dict)로 낸다. MongoDB 에 닿지 못하면 예외가 난다(없다고 잘못 말하지 않는다).

    범위는 **호출한 쪽의 읽기 스냅샷**이 정한다(`billing.api.consistent_read` 안에서 부르면 그 스냅샷에 보이는 행 전체). `stats` 에 훑은 행 수가 남는다.
    """
    stats = {} if stats is None else stats
    for kind in ("decision", "reaction", "ledger_charge", "bid_policy", "bid", "campaign_change", "ad_group_change", "segment_version", "ad_group_profile"):  # _sources 가 내는 종류 전부 — 0건이어도 칸이 있다
        stats.setdefault(kind, 0)
    for batch in batched(_sources(stats), CHUNK):
        ids = {events.event_id_for(name, source_id): (kind, source_id, name) for kind, source_id, name in batch}
        for event_id in events.missing_event_ids(list(ids)):
            kind, source_id, name = ids[event_id]
            yield {"kind": "missing_event", "event_name": name, "source_kind": kind, "source_id": str(source_id), "event_id": str(event_id)}
