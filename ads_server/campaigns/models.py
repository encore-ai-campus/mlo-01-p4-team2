"""광고주가 소유하는 운영 데이터: 캠페인·광고그룹·소재·입찰·재검토 요청(설계 §4.5~4.6)과 캠페인·광고그룹의 변경 로그."""
import uuid

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from foundation.api import MAX_AMOUNT, AppendOnlyModel


class CampaignStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "집행 중"
    PAUSED = "PAUSED", "일시 중지"
    SUSPENDED = "SUSPENDED", "관리자 중단"
    ENDED = "ENDED", "종료"


class ReviewStatus(models.TextChoices):
    OPEN = "OPEN", "대기"
    RELEASED = "RELEASED", "해제됨"
    REJECTED = "REJECTED", "반려됨"
    CLOSED_ENDED = "CLOSED_ENDED", "캠페인 종료로 닫힘"


class Campaign(models.Model):
    campaign_id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="campaigns")
    name = models.CharField(max_length=100)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField(null=True, blank=True)  # 끝나는 시각(미포함). 없으면 계속 진행
    daily_budget = models.PositiveBigIntegerField()
    status = models.CharField(max_length=20, choices=CampaignStatus.choices, default=CampaignStatus.ACTIVE)  # 쓰는 곳은 transitions.py 뿐
    ended_from = models.CharField(max_length=20, choices=CampaignStatus.choices, null=True, blank=True)
    suspended_reason = models.CharField(max_length=500, blank=True)
    suspended_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)
    request_key = models.CharField(max_length=64, null=True, blank=True)  # 캠페인 만들기 화면 폼 하나 = 캠페인 하나(같은 키로는 다시 만들지 않는다, 6-9d)

    class Meta:
        db_table = "campaigns_campaign"
        indexes = [models.Index(fields=["status", "ends_at"])]
        constraints = [
            models.UniqueConstraint(fields=["request_key"], name="campaigns_campaign_once_per_request"),  # NULL(키 없이 만든 캠페인)은 서로 다르다
            models.CheckConstraint(condition=Q(daily_budget__gt=0, daily_budget__lte=MAX_AMOUNT), name="campaigns_campaign_budget_range"),
            models.CheckConstraint(condition=Q(ends_at__isnull=True) | Q(ends_at__gte=F("starts_at")), name="campaigns_campaign_period_order"),
        ]


class AdGroup(models.Model):
    ad_group_id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    campaign = models.ForeignKey(Campaign, on_delete=models.PROTECT, related_name="ad_groups")
    name = models.CharField(max_length=100)
    enabled = models.BooleanField(default=True)
    # 선택된 소재 — 결정에 나가는 소재다. 연결(`AdGroupCreative`)된 것 중에서만 고른다(서비스가 지킨다). 없으면 후보가 아니다(6-9c)
    creative = models.ForeignKey("Creative", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    request_key = models.CharField(max_length=64, null=True, blank=True)  # 기존 캠페인에 광고 만들기 폼 하나 = 광고그룹 하나(6-9c)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)  # 화면이 본 값과 대조해 옛 폼(이름·게재 위치)이 그 사이의 변경을 덮지 않게(3-2d)

    class Meta:
        db_table = "campaigns_ad_group"
        constraints = [models.UniqueConstraint(fields=["request_key"], name="campaigns_ad_group_once_per_request")]  # NULL 은 서로 다르다


class AdGroupSlot(models.Model):
    """광고그룹이 고른 구성(DRD O3). `creative` = 이 구성에서 나갈 소재(설계 §10 "구성별 노출 소재") — NULL 이면 광고그룹의 선택 소재(`AdGroup.creative`)를 쓴다.
    구성 목록을 바꾸면 빠진 구성의 행만 지우므로 남는 구성의 지정은 그대로다. 소재 쪽은 `PROTECT` — 소재는 지우지 않고 보관한다."""

    ad_group_slot_id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    ad_group = models.ForeignKey(AdGroup, on_delete=models.CASCADE, related_name="slot_links", db_index=False)  # db_index=False: campaigns_ad_group_slot_unique 가 이 열로 시작해 FK 색인이 겹친다(계획서 부록 B-1)
    slot = models.ForeignKey("masters.Slot", on_delete=models.PROTECT, related_name="+")
    creative = models.ForeignKey("Creative", on_delete=models.PROTECT, related_name="slot_selections", null=True, blank=True)  # 색인: 보관 제한이 소재로 찾는다

    class Meta:
        db_table = "campaigns_ad_group_slot"
        constraints = [models.UniqueConstraint(fields=["ad_group", "slot"], name="campaigns_ad_group_slot_unique")]


class AdGroupSegment(models.Model):
    """광고그룹의 대상 그룹 T(DRD O11, 설계 §10 "대상 그룹 타기팅" ③): 0개 이상, **없으면 전체가 대상**. 구성 연결(`AdGroupSlot`)과 같은 모양이다.
    목록을 바꾸면 연결 행을 바꾸고 이력은 변경 로그(`SEGMENTS_CHANGED`)에 남는다. 그룹 쪽은 `PROTECT` — 그룹은 지우지 않고 보관한다(R-6)."""

    ad_group_segment_id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    ad_group = models.ForeignKey(AdGroup, on_delete=models.CASCADE, related_name="segment_links", db_index=False)  # db_index=False: campaigns_ad_group_segment_unique 가 이 열로 시작해 FK 색인이 겹친다(계획서 부록 B-1)
    segment = models.ForeignKey("masters.Segment", on_delete=models.PROTECT, related_name="+")

    class Meta:
        db_table = "campaigns_ad_group_segment"
        constraints = [models.UniqueConstraint(fields=["ad_group", "segment"], name="campaigns_ad_group_segment_unique")]


class Creative(models.Model):
    """광고주 소유의 소재 목록(6-9c, 설계 §10 "소재 목록"). 광고그룹에는 `AdGroupCreative` 로 연결하고, 광고그룹이 그중 하나를 선택한다.
    화면의 "삭제"는 보관(`archived_at`)이다 — 행은 남는다(감사 기록·연결 기록이 참조). 끝나지 않은 캠페인의 광고그룹이 선택한 소재는 보관하지 못한다(6-24 — 연결만 남은 소재는 보관할 수 있다).
    소재 끄기는 없다 — 광고를 멈추는 것은 광고그룹 ON/OFF 하나다(6-9b)."""

    creative_id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="creatives")
    title = models.CharField(max_length=200)
    redirect_url = models.CharField(max_length=2048)
    image_name = models.CharField(max_length=80)  # assets 의 `<sha256>.<ext>`. 이 열로 찾는 질의가 없다 — 서빙 판정은 CreativeImage.image_name 을 본다(부록 B-3)
    image_bytes = models.PositiveIntegerField()
    archived_at = models.DateTimeField(null=True, blank=True)  # 보관(화면의 "삭제"). 보관한 소재는 개수 상한에서 빠지고 이미지 보유량에는 남는다
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "campaigns_creative"


class AdGroupCreative(AppendOnlyModel):
    """광고그룹에 연결한 소재(6-9c). **끊지 않는다**(추가만) — 연결 기록이 ① 소재 목록의 "연결된 광고"(보관 가능 여부는 선택으로 본다 — 6-24) ②
    이미지 공개 판정(연결된 적 있는 광고그룹의 캠페인이 살아 있으면 공개)의 근거다. 선택을 바꿔도 이전 결정의 이미지가 깨지지 않는다."""

    link_id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    ad_group = models.ForeignKey(AdGroup, on_delete=models.PROTECT, related_name="creative_links", db_index=False)  # db_index=False: campaigns_ad_group_creative_unique 가 이 열로 시작해 FK 색인이 겹친다(계획서 부록 B-1)
    creative = models.ForeignKey(Creative, on_delete=models.PROTECT, related_name="ad_group_links")
    linked_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "campaigns_ad_group_creative"
        constraints = [models.UniqueConstraint(fields=["ad_group", "creative"], name="campaigns_ad_group_creative_unique")]


class CreativeImage(AppendOnlyModel):
    """소재가 지금까지 **보유한 이미지 파일**(현재 + 과거). 쿼터와 서빙 가능 여부를 이 관계로 판단한다.

    이전 이미지는 지우지 않는다(과거 결정이 URL 을 참조한다). 그래서 보유량에 포함하지 않으면 교체를 반복해 쿼터를 우회할 수 있고,
    서빙 판정에서 빼면 교체 직후 과거 결정의 이미지가 404 가 된다. `Creative.image_name` 은 그중 **현재** 것을 가리킨다.
    """

    creative_image_id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    creative = models.ForeignKey(Creative, on_delete=models.PROTECT, related_name="images", db_index=False)  # db_index=False: campaigns_creative_image_unique 가 이 열로 시작해 FK 색인이 겹친다(계획서 부록 B-1)
    image_name = models.CharField(max_length=80)
    image_bytes = models.PositiveIntegerField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "campaigns_creative_image"
        constraints = [models.UniqueConstraint(fields=["creative", "image_name"], name="campaigns_creative_image_unique")]
        # 서빙 판정(is_asset_servable)이 이름으로 찾는다. db_index 대신 여기 두면 PostgreSQL 의 LIKE 전용 `_like` 색인이 따로 생기지 않는다(부록 B-5)
        indexes = [models.Index(fields=["image_name"], name="campaigns_cimage_name_idx")]


class Bid(AppendOnlyModel):
    """입찰의 한 버전. 하한 미만·상한 초과도 그대로 저장한다(설계 §4.2). 현재 값 = 최대 버전."""

    bid_id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)  # 버전 행 하나의 식별자이자 PK(결정·이벤트가 가리킨다)
    ad_group = models.ForeignKey(AdGroup, on_delete=models.PROTECT, related_name="bids", db_index=False)  # db_index=False: campaigns_bid_version_per_ad_group 가 이 열로 시작해 FK 색인이 겹친다(계획서 부록 B-1)
    bid_version = models.PositiveIntegerField()
    amount = models.PositiveBigIntegerField()
    created_by = models.UUIDField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "campaigns_bid"
        constraints = [
            models.UniqueConstraint(fields=["ad_group", "bid_version"], name="campaigns_bid_version_per_ad_group"),
            models.CheckConstraint(condition=Q(amount__gt=0, amount__lte=MAX_AMOUNT), name="campaigns_bid_amount_range"),
        ]


CAMPAIGN_CHANGES = ("CREATED", "STATUS_CHANGED", "BUDGET_CHANGED", "PERIOD_CHANGED")
AD_GROUP_CHANGES = ("CREATED", "ENABLED_CHANGED", "SLOTS_CHANGED", "CREATIVE_SELECTED", "SEGMENTS_CHANGED", "SLOT_CREATIVE_SELECTED")


class ChangeTarget(models.TextChoices):
    CAMPAIGN = "campaign"
    AD_GROUP = "ad_group"


class ChangeLog(AppendOnlyModel):
    """캠페인·광고그룹의 변경 한 번 = 한 줄(계획 docs/plans/00 §5.2, D2·D10). 이 행의 ID 가 `ad.campaign_changed`·`ad.ad_group_changed` 의 원천 ID 라
    같은 대상을 두 번 바꿔도 이벤트가 충돌하지 않는다. 쓰는 곳은 `changes.record_change` 하나다(로그와 이벤트를 같은 트랜잭션에)."""

    change_id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    target_type = models.CharField(max_length=16, choices=ChangeTarget.choices)
    target_id = models.UUIDField()  # FK 가 아니다: 이력 행은 값으로 가리킨다(Decision·AuditLog 와 같다)
    campaign_id = models.UUIDField()  # 광고그룹 행도 소속 캠페인을 채운다 — 광고주 범위로 좁힐 때
    change_type = models.CharField(max_length=32)
    previous_value = models.JSONField(null=True)  # 바뀐 업무 필드만(형식은 events.change_value). CREATED 는 NULL
    new_value = models.JSONField()
    changed_at = models.DateTimeField()

    class Meta:
        db_table = "campaigns_change_log"
        indexes = [models.Index(fields=["target_type", "changed_at"], name="campaigns_chg_type_at_idx")]  # 정합성 원천 스트림이 종류·창마다 읽는다
        constraints = [
            models.CheckConstraint(
                condition=Q(target_type=ChangeTarget.CAMPAIGN, change_type__in=CAMPAIGN_CHANGES) | Q(target_type=ChangeTarget.AD_GROUP, change_type__in=AD_GROUP_CHANGES),
                name="campaigns_change_log_kind",
            ),
            models.CheckConstraint(
                condition=Q(change_type="CREATED", previous_value__isnull=True) | (~Q(change_type="CREATED") & Q(previous_value__isnull=False)),
                name="campaigns_change_log_previous_unless_created",
            ),
        ]


class ReviewRequest(models.Model):
    """재검토 요청. 만든 뒤에는 상태·답변만 닫히고(transitions.py) 지워지지 않는다."""

    review_request_id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    # FK 색인을 끄지 않는다: 같은 열 하나짜리 부분 유일 색인(아래 제약)이 있어, db_index 를 끄는 마이그레이션이 그 색인까지 지운다(계획서 §9, P1-7)
    campaign = models.ForeignKey(Campaign, on_delete=models.PROTECT, related_name="review_requests")
    message = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=20, choices=ReviewStatus.choices, default=ReviewStatus.OPEN)
    response_note = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "campaigns_review_request"
        # 캠페인당 열린 요청 1건(조건부 유일 제약). 상태를 바꾸는 코드(닫기·일괄 종료)는 이 제약을 신경 쓰지 않는다 — 닫힌 행은 조건에서 빠진다
        constraints = [models.UniqueConstraint(fields=["campaign"], condition=Q(status=ReviewStatus.OPEN), name="campaigns_one_open_review_per_campaign")]
