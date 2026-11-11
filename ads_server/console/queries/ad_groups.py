"""광고그룹 상세 화면용 조립(Step 3-2d — 각 서비스 `api` 의 값을 합치기만 한다)."""
from dataclasses import dataclass

from campaigns import api as campaigns
from masters import api as masters


@dataclass(frozen=True)
class AdGroupPage:
    group: object  # AdGroupView
    campaign_name: str  # 속한 캠페인(소유는 `get_ad_group` 이 확인했다 — 이름만 읽는다)
    bids: list  # BidView, 최신 버전부터
    images: list  # CreativeView — 고를 수 있는 내 이미지(보관하지 않은 소재 전부)와, 보관했어도 지금 선택된 것(기본·구성별 — 6-11: 화면은 고르기만 한다)
    deletable: frozenset  # 그중 지금 삭제(보관)할 수 있는 소재 ID — 끝나지 않은 캠페인의 광고가 쓰지 않는 것(설계 §10 "광고주 화면 정리" ①, 서비스가 다시 본다)
    linked: dict  # {slot_id: SlotView} 지금 고른 위치(꺼진 위치 포함)
    choices: list  # 고를 수 있는 위치(켜진 개인 구성 — 과금 단위는 없다, 6-10d. 일괄 구성은 화면에서 고를 수 없다 — TG-S4)
    effects: tuple  # 현재 입찰의 위치별 결과(SlotEffect), 입찰이 없으면 빈 튜플
    segments: dict  # {segment_id: SegmentView} 지금 연결된 대상 그룹(보관 포함)과 고른 구성의 매체에 속한 그룹
    media_names: dict  # {media_id: 매체 이름} 지금 고른 구성의 매체

    @property
    def slot_creatives(self):
        """{slot_id: creative_id} 구성별로 고른 소재(설계 §10 "구성별 노출 소재"). 없는 구성은 기본 소재(`group.creative_id`)가 나간다."""
        return dict(self.group.slot_creative_ids)

    @property
    def segment_choices(self):
        """고를 수 있는 대상 그룹: 고른 구성의 매체에 속한 살아 있는 것 + 이미 연결된 것(보관됐어도 — 체크를 풀어 뺄 수 있게)."""
        linked = set(self.group.segment_ids)
        return [g for g in self.segments.values() if g.segment_id in linked or (g.is_active and g.media_id in self.media_names)]

    @property
    def segment_rows(self):
        """화면 한 줄씩: (매체 이름 — 고른 구성에 그 매체가 없으면 빈 문자열, 그룹). `segment_choices` 와 같은 순서."""
        return [(self.media_names.get(g.media_id, ""), g) for g in self.segment_choices]

    @property
    def archived_only(self):
        """대상 그룹을 골랐는데 모두 보관됐다 — 어느 요청에도 맞지 않아 광고가 나가지 않는다."""
        linked = [self.segments[s] for s in self.group.segment_ids if s in self.segments]
        return bool(linked) and not any(g.is_active for g in linked)

    @property
    def uncovered_media(self):
        """대상 그룹을 골랐는데 고른 구성의 매체 중 그 매체의 **살아 있는** 그룹이 하나도 없는 매체(그 매체의 구성에는 이 광고가 나가지 않는다, 설계 §10 "대상 그룹 타기팅" ⑤).
        보관된 그룹은 덮지 않는다 — 보관 = 그 그룹 없음이라 그 매체의 요청은 맞는 그룹이 없다(리뷰 R1)."""
        if not self.group.segment_ids:
            return []
        covered = {self.segments[s].media_id for s in self.group.segment_ids if s in self.segments and self.segments[s].is_active}
        return sorted(name for media_id, name in self.media_names.items() if media_id not in covered)

    @property
    def inactive_linked(self):
        """고른 구성 중 지금 꺼진 것(구성이나 매체가 꺼짐 — 일괄도 꺼졌으면 여기다). 위치를 다시 저장하면 빠진다(서비스는 꺼진 위치를 고를 수 없게 한다)."""
        shown = {str(s.slot_id) for s in self.choices} | {str(s.slot_id) for s in self.shared_linked}
        return [s for slot_id, s in self.linked.items() if str(slot_id) not in shown]

    @property
    def shared_linked(self):
        """이미 고른 **켜진** 일괄 구성(TG-S4 — 화면에서는 더 고를 수 없다, 사용 중지). 그대로 나가지만 구성을 다시 저장하면 빠진다.
        켜짐은 `list_slots(active_only=True)` 와 같은 기준(구성과 매체가 모두 켜짐). 꺼진 일괄은 `inactive_linked` 로 간다 — 나가지 않으므로(리뷰 R1)."""
        return [s for s in self.linked.values() if s.audience == masters.Audience.SHARED and s.is_active and s.media_is_active]


def individual_slots():
    """광고주가 고를 수 있는 구성: 켜진 **개인** 구성만(설계 §10 "일괄 슬롯" — 일괄은 화면에서만 사용 중지, 서비스·C1 은 그대로, TG-S4)."""
    return [s for s in masters.list_slots(active_only=True) if s.audience == masters.Audience.INDIVIDUAL]


def ad_group_page(user, ad_group_id):
    """남의 광고그룹은 서비스가 404."""
    group = campaigns.get_ad_group(user, ad_group_id)
    linked = masters.get_slots_with_policy(group.slot_ids)
    effects = campaigns.bid_effects(group.current_bid.amount, list(group.slot_ids), slots=linked) if group.current_bid else ()  # 위치 정보와 같은 읽기
    owned = campaigns.list_owned_creatives(user)
    images = [o.creative for o in owned]
    chosen = [group.creative_id] if group.creative_id is not None else []
    chosen += [c for _, c in group.slot_creative_ids if c not in chosen]
    for creative_id in reversed(chosen):  # 보관한 소재가 아직 선택돼 있으면(끝난 캠페인) 그것도 보인다
        if creative_id not in {c.creative_id for c in images}:
            images.insert(0, campaigns.get_creative(user, creative_id))
    media_names = {s.media_id: s.media_name for s in linked.values()}
    segments = {g.segment_id: g for g in masters.segments(segment_ids=group.segment_ids, media_ids=media_names)}
    return AdGroupPage(
        group=group, campaign_name=campaigns.campaign_names([group.campaign_id])[group.campaign_id], bids=campaigns.list_bids(user, group.ad_group_id),
        images=images, deletable=frozenset(o.creative.creative_id for o in owned if o.deletable), linked=linked,
        choices=individual_slots(), effects=effects, segments=segments, media_names=media_names,
    )
