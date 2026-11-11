"""masters 가 밖으로 내보내는 값 객체. 다른 모듈은 모델이 아니라 이것만 받는다(설계 R2, 계획 §6.0-1)."""
import uuid
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class MediaView:
    media_id: uuid.UUID
    name: str
    is_active: bool
    created_at: datetime


@dataclass(frozen=True)
class MediaIdentity:
    """키 인증에 성공한 매체. 매체가 비활성이어도 만들어진다(403 은 호출하는 쪽이 판단한다)."""

    media_id: uuid.UUID
    key_id: uuid.UUID
    media_is_active: bool


@dataclass(frozen=True)
class IssuedKey:
    """발급 결과. **`key` 는 이 한 번만 보인다**(저장하지 않는다)."""

    key_id: uuid.UUID
    media_id: uuid.UUID
    key: str
    created_at: datetime


@dataclass(frozen=True)
class MediaKeyView:
    key_id: uuid.UUID
    media_id: uuid.UUID
    label: str
    created_at: datetime
    expires_at: datetime | None
    revoked_at: datetime | None
    active: bool


@dataclass(frozen=True)
class PolicyView:
    bid_policy_id: uuid.UUID
    slot_id: uuid.UUID
    policy_version: int
    floor: int
    base: int
    cap: int
    impression_share: int  # 반응 유형별 과금 비율(%), 합 100(6-10b)
    click_share: int
    conversion_share: int
    changed_by: uuid.UUID
    changed_at: datetime
    reason: str

    @property
    def charge_shares(self):
        """{반응 유형: 비율(%)} — 과금(`billing`)과 C2 `charge_shares` 가 쓰는 모양(6-10d)."""
        return {"IMPRESSION": self.impression_share, "CLICK": self.click_share, "CONVERSION": self.conversion_share}


@dataclass(frozen=True)
class SlotDisplay:
    """매체가 소재 이미지를 그리는 모양(설계 §10 "구성 표시 정보"). 화면 정보 — 판정·과금·결정에 쓰지 않는다.
    `width`·`height` = 판의 가로:세로 비율(둘 다 있거나 둘 다 None — `contain` 만 None 일 수 있다), `fit` = `stretch`·`cover`·`contain`."""

    width: int | None
    height: int | None
    fit: str


@dataclass(frozen=True)
class SlotView:
    slot_id: uuid.UUID
    media_id: uuid.UUID
    media_name: str
    media_is_active: bool
    name: str
    audience: str  # `individual` | `shared`
    is_active: bool
    policy: PolicyView  # 현재 정책. 슬롯은 항상 정책을 가진다(create_slot 이 함께 만든다)
    segment: "RequestSegment | None" = None  # `get_slot(…, segment_code=)` 이 그 매체 카탈로그에서 찾은 요청 그룹(못 찾았거나 묻지 않았으면 None)
    display: SlotDisplay | None = None  # 표시 정보. 관리자가 정하지 않았으면 None


@dataclass(frozen=True)
class RequestSegment:
    """C1 결정 요청의 그룹 코드를 그 슬롯 매체의 카탈로그(DRD M7)로 푼 값 — 결정이 그 순간의 상태를 남긴다(잠그지 않고 읽는다, 설계 §10 ⑧)."""

    segment_id: uuid.UUID
    is_active: bool


@dataclass(frozen=True)
class SegmentView:
    """대상 그룹의 현재 값(DRD M7)과 그 판 번호(운영 화면이 바꿀 때 `expected_version` 으로 되돌려 보낸다 — 광고주 쪽 읽기는 판을 읽지 않아 `None`)."""

    segment_id: uuid.UUID
    media_id: uuid.UUID
    code: str
    name: str
    is_active: bool
    version: int | None
    created_at: datetime


@dataclass(frozen=True)
class SegmentVersionView:
    """대상 그룹 판 하나(DRD M8) — `ad.segment_changed` 의 원천. 매체·코드는 그룹 행(M7)의 값이다(바뀌지 않는다)."""

    segment_version_id: uuid.UUID
    segment_id: uuid.UUID
    media_id: uuid.UUID
    code: str
    version: int
    name: str
    is_active: bool
    changed_by: uuid.UUID
    changed_at: datetime
