"""관리자가 소유하는 마스터: 매체·게재 위치(슬롯)·입찰 정책(설계 §4.1)."""
import uuid

from django.db import models
from django.db.models import F, Q
from django.db.models.lookups import Exact
from django.utils import timezone

from foundation.api import MAX_AMOUNT, AppendOnlyModel


# 입찰 정책의 반응 유형별 과금 비율(설계 §10 "구성별 반응 비율 과금", 6-10). 각각 정수 1~98, 합 100.
SHARE_FIELDS = ("impression_share", "click_share", "conversion_share")
MIN_SHARE, MAX_SHARE = 1, 98
MIN_FLOOR = 100  # 하한 최소: 비율 ≥ 1% 와 함께 반응 하나의 과금(내림)이 0 이 되지 않게 한다(100 × 1 ÷ 100 = 1)


class Audience(models.TextChoices):
    """슬롯 성격(설계 §10 "일괄 슬롯"). 결정 하나를 **한 사람**이 보는가, **여러 사람**이 같은 광고를 보는가. 타기팅이 아니다."""

    INDIVIDUAL = "individual", "개인"
    SHARED = "shared", "일괄"


class DisplayFit(models.TextChoices):
    """매체가 소재 이미지를 그리는 방식(설계 §10 "구성 표시 정보"). 화면 정보다 — 판정·과금·결정에 쓰지 않는다."""

    STRETCH = "stretch", "판에 맞춰 늘림"
    COVER = "cover", "판을 채우고 넘치는 곳은 잘림"
    CONTAIN = "contain", "이미지 전체"


DISPLAY_SIZE_MAX = 10000  # 비율 칸(가로·세로) 하나의 상한. 하한은 1


class Media(models.Model):
    media_id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    name = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "masters_media"

    def __str__(self):
        return self.name


class MediaDeactivation(AppendOnlyModel):
    """매체를 끈 시각(설계 §10 "매체 비활성화는 그 전에 만든 결정을 끝낸다", 6-1a). 활성 → 비활성으로 **실제로 바뀔 때만** 한 줄 더한다.
    반응 판정이 "결정 뒤·접수 전에 꺼진 적이 있나"를 이 기록으로 본다(`first_deactivation`). 다시 켜도 지우지 않는다."""

    deactivation_id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    media = models.ForeignKey(Media, on_delete=models.PROTECT, related_name="deactivations", db_index=False)  # db_index=False: masters_media_deact_at_idx 가 이 열로 시작해 FK 색인이 겹친다(계획서 부록 B-1)
    deactivated_at = models.DateTimeField()

    class Meta:
        db_table = "masters_media_deactivation"
        indexes = [models.Index(fields=["media", "deactivated_at"], name="masters_media_deact_at_idx")]


class MediaRateBucket(models.Model):
    """매체 API 요청 제한의 토큰 버킷 하나 = (매체, 경로군) 행 하나(설계 §10 "요청 제한 구현"). 모든 웹 프로세스·호스트가 이 행을 함께 쓴다.
    `take_rate_token` 의 문장 하나(INSERT … ON CONFLICT DO UPDATE)만 쓴다. **갱신 열(`tokens`·`refilled_at`·`granted`)에는 색인을 두지 않는다** —
    매 요청의 UPDATE 가 HOT 갱신으로 같은 페이지 안에서 정리되어 표·색인이 부풀지 않는다."""

    bucket_id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    media = models.ForeignKey(Media, on_delete=models.PROTECT, related_name="+", db_index=False)  # db_index=False: 유일 제약이 이 열로 시작해 FK 색인이 겹친다
    route_group = models.CharField(max_length=16)
    tokens = models.FloatField()  # 마지막 계산 뒤 남은 토큰
    refilled_at = models.DateTimeField()  # 마지막으로 충전을 계산한 시각. 뒤로 가지 않는다(늦게 도착한 이른 시각은 경과 0)
    granted = models.BooleanField()  # 마지막 요청의 통과 여부(문장 하나로 결과를 돌려받으려고 둔다)

    class Meta:
        db_table = "masters_media_rate_bucket"
        constraints = [models.UniqueConstraint(fields=["media", "route_group"], name="masters_rate_bucket_per_group")]


class Slot(models.Model):
    slot_id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    media = models.ForeignKey(Media, on_delete=models.PROTECT, related_name="slots", db_index=False)  # db_index=False: masters_slot_name_per_media 가 이 열로 시작해 FK 색인이 겹친다(계획서 부록 B-1)
    name = models.CharField(max_length=100)
    audience = models.CharField(max_length=20, choices=Audience.choices)  # 만든 뒤 바꿀 수 없다. 기본값을 두지 않는다: 만드는 쪽이 정한다
    is_active = models.BooleanField(default=True)
    # 표시 정보(설계 §10 "구성 표시 정보", SDI-1): 판의 가로:세로 비율과 그리는 방식. 셋 다 NULL = 정보 없음. 규칙은 서비스(`set_slot_display`)와 아래 CHECK 가 같다
    display_width = models.PositiveSmallIntegerField(null=True, blank=True)
    display_height = models.PositiveSmallIntegerField(null=True, blank=True)
    display_fit = models.CharField(max_length=10, choices=DisplayFit.choices, null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "masters_slot"
        constraints = [
            models.UniqueConstraint(fields=["media", "name"], name="masters_slot_name_per_media"),
            models.CheckConstraint(condition=Q(audience__in=Audience.values), name="masters_slot_audience_valid"),
            models.CheckConstraint(condition=Q(display_fit__isnull=True) | Q(display_fit__in=DisplayFit.values), name="masters_slot_display_fit_valid"),
            # 비율은 둘 다 없거나, 둘 다 1~상한이고 방식이 있다. IS NOT NULL 을 따로 적는다 — 비교만 두면 한쪽이 NULL 일 때 CHECK 가 NULL(통과)이 된다
            models.CheckConstraint(
                condition=Q(display_width__isnull=True, display_height__isnull=True)
                | Q(display_width__isnull=False, display_height__isnull=False, display_fit__isnull=False,
                    display_width__gte=1, display_width__lte=DISPLAY_SIZE_MAX, display_height__gte=1, display_height__lte=DISPLAY_SIZE_MAX),
                name="masters_slot_display_size_valid",
            ),
            # 늘림·자름은 판(비율)이 있어야 한다. 이미지 전체(contain)는 비율 없이도 된다(정해진 판 없이 원본 비율)
            models.CheckConstraint(
                condition=~Q(display_fit__in=[DisplayFit.STRETCH, DisplayFit.COVER]) | Q(display_width__isnull=False),
                name="masters_slot_display_frame_has_size",
            ),
        ]

    def save(self, *args, **kwargs):
        """슬롯 성격은 만든 뒤 바꿀 수 없다(집계 기준의 의미가 섞이지 않게, 설계 §10 "일괄 슬롯")."""
        if not self._state.adding:
            current = type(self).objects.filter(pk=self.pk).values("audience").first()
            if current is not None and current["audience"] != self.audience:
                raise ValueError("슬롯의 성격(audience)은 바꿀 수 없다.")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.media_id}/{self.name}"


class MediaKey(models.Model):
    """매체 서버가 `X-Media-Key` 로 보내는 키(설계 §3 C1). **평문은 저장하지 않는다** — 발급할 때 한 번만 돌려준다.

    키 형식은 `<key_id>.<비밀>` 이다. `key_id`(공개 식별자)로 행을 찾고 비밀의 해시를 상수시간으로 비교한다.
    비밀은 256비트 무작위 값이라 비밀번호용 느린 해시가 필요 없다(SHA-256 으로 충분하고 요청마다 비교해도 싸다).
    매체마다 여러 개가 동시에 유효할 수 있다(교체 유예 기간).
    """

    key_id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    media = models.ForeignKey(Media, on_delete=models.PROTECT, related_name="keys")
    secret_hash = models.CharField(max_length=64)
    label = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(null=True, blank=True)  # 교체 유예가 끝나는 시각. 없으면 계속 유효
    revoked_at = models.DateTimeField(null=True, blank=True)
    request_key = models.CharField(max_length=64, null=True, blank=True)  # 발급 요청의 멱등 키(화면 폼 하나 = 키 하나). 같은 키로는 다시 발급하지 않는다

    class Meta:
        db_table = "masters_media_key"
        constraints = [models.UniqueConstraint(fields=["request_key"], name="masters_key_once_per_request")]  # NULL(키 없는 발급)은 서로 다르다


class BidPolicy(AppendOnlyModel):
    """슬롯별 입찰 정책의 한 버전. 수정이 아니라 새 버전 행 추가이고, 현재 값 = 최대 버전이다."""

    bid_policy_id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)  # 버전 행 하나의 식별자이자 PK(결정·이벤트가 가리킨다)
    slot = models.ForeignKey(Slot, on_delete=models.PROTECT, related_name="policies", db_index=False)  # db_index=False: masters_policy_version_per_slot 가 이 열로 시작해 FK 색인이 겹친다(계획서 부록 B-1)
    policy_version = models.PositiveIntegerField()
    floor_amount = models.PositiveBigIntegerField()
    base_amount = models.PositiveBigIntegerField()
    cap_amount = models.PositiveBigIntegerField()
    impression_share = models.PositiveSmallIntegerField()  # 반응 유형별 과금 비율(%) — 과금 = 내림(적용 금액 × % ÷ 100)
    click_share = models.PositiveSmallIntegerField()
    conversion_share = models.PositiveSmallIntegerField()
    changed_by = models.UUIDField()  # FK 가 아니다: 사용자가 바뀌어도 이력은 그대로(감사 로그와 같은 이유)
    changed_at = models.DateTimeField(default=timezone.now)
    reason = models.CharField(max_length=200)

    class Meta:
        db_table = "masters_bid_policy"
        constraints = [
            models.UniqueConstraint(fields=["slot", "policy_version"], name="masters_policy_version_per_slot"),
            models.CheckConstraint(
                condition=Q(floor_amount__lte=F("base_amount"), base_amount__lte=F("cap_amount"),
                            cap_amount__lte=MAX_AMOUNT),
                name="masters_policy_floor_base_cap_order",
            ),
            models.CheckConstraint(condition=Q(floor_amount__gte=MIN_FLOOR), name="masters_policy_floor_min"),
            models.CheckConstraint(
                condition=Q(**{f"{f}__range": (MIN_SHARE, MAX_SHARE) for f in SHARE_FIELDS}), name="masters_policy_share_range",
            ),
            models.CheckConstraint(
                condition=Exact(F("impression_share") + F("click_share") + F("conversion_share"), 100), name="masters_policy_shares_sum_100",
            ),
        ]


SEGMENT_CODE_PATTERN = r"^[a-z][a-z0-9-]{0,31}$"  # 매체가 C1 `segment` 로 보내는 코드 그대로(DRD M7, R-1 의 예외)


class Segment(models.Model):
    """대상 그룹(DRD M7, 설계 §10 "대상 그룹 타기팅" ②): 매체가 정한 사용자 묶음의 카탈로그. **현재 값의 원본은 이 행**이고,
    만들 때와 이름·보관 상태가 바뀔 때마다 같은 트랜잭션에서 판(`SegmentVersion`)에 그때 상태 전체를 한 줄 더한다. 행을 지우지 않는다(보관 = `is_active` 거짓)."""

    segment_id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    media = models.ForeignKey(Media, on_delete=models.PROTECT, related_name="segments", db_index=False)  # db_index=False: masters_segment_code_per_media 가 이 열로 시작해 FK 색인이 겹친다(계획서 부록 B-1)
    code = models.CharField(max_length=32)  # 등록 뒤 바꿀 수 없다(save 가 막는다 — 서비스는 이 열을 고치지 않는다)
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "masters_segment"
        constraints = [
            models.UniqueConstraint(fields=["media", "code"], name="masters_segment_code_per_media"),
            models.CheckConstraint(condition=Q(code__regex=SEGMENT_CODE_PATTERN), name="masters_segment_code_format"),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            current = type(self).objects.filter(pk=self.pk).values("code").first()
            if current is not None and current["code"] != self.code:
                raise ValueError("대상 그룹의 코드(code)는 바꿀 수 없다.")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.media_id}/{self.code}"


class SegmentVersion(AppendOnlyModel):
    """대상 그룹 판(DRD M8): 그룹을 만들 때(판 1)와 이름·보관 상태가 바뀔 때마다 그때 상태 전체. 열 모양은 입찰 정책(M3)과 같지만
    **현재 값은 최대 판이 아니라 `Segment`** 다(결정 경로는 `Segment` 만 읽는다). `ad.segment_changed` 의 원천이다."""

    segment_version_id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    segment = models.ForeignKey(Segment, on_delete=models.PROTECT, related_name="versions", db_index=False)  # db_index=False: masters_segment_version_per_segment 가 이 열로 시작해 FK 색인이 겹친다
    version = models.PositiveIntegerField()
    name = models.CharField(max_length=100)
    is_active = models.BooleanField()
    changed_by = models.UUIDField()  # FK 가 아니다(입찰 정책과 같은 이유). 이벤트에는 싣지 않는다
    changed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "masters_segment_version"
        constraints = [models.UniqueConstraint(fields=["segment", "version"], name="masters_segment_version_per_segment")]
