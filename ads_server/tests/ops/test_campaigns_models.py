import uuid
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase, tag

from campaigns.models import AdGroup, AdGroupProfile, Bid, Campaign, ReviewRequest
from foundation.models import AppendOnlyError
from tests.support.factories import DAY, NOW, make_advertiser
from tests.support.cases import Cases

USER_ID = uuid.uuid4()


def campaign(owner, **kw):
    values = dict(owner=owner, name="c", starts_at=NOW, ends_at=NOW + DAY, daily_budget=1000)
    values.update(kw)
    return Campaign.objects.create(**values)


class CampaignConstraintTests(Cases, TestCase):
    def setUp(self):
        self.owner = make_advertiser()

    def case_budget_and_period_constraints_in_db(self):
        for kw in (dict(daily_budget=0), dict(daily_budget=2**53), dict(ends_at=NOW - timedelta(seconds=1))):
            with self.assertRaises(IntegrityError, msg=kw), transaction.atomic():
                campaign(self.owner, **kw)
        campaign(self.owner, ends_at=None)
        campaign(self.owner, ends_at=NOW)  # 시작과 같은 종료(시작 전 조기 종료)는 허용

    def case_only_one_open_review_request_per_campaign(self):
        c = campaign(self.owner)
        ReviewRequest.objects.create(campaign=c)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ReviewRequest.objects.create(campaign=c)
        ReviewRequest.objects.filter(campaign=c).update(status="REJECTED")
        ReviewRequest.objects.create(campaign=c)  # 닫힌 뒤에는 다시 열 수 있다
        ReviewRequest.objects.create(campaign=campaign(self.owner))  # 다른 캠페인은 영향 없음

    def test_campaign_constraint(self):
        self.run_cases()


@tag("security")
class BidAndAdGroupModelTests(Cases, TestCase):
    def setUp(self):
        self.group = AdGroup.objects.create(campaign=campaign(make_advertiser()), name="g")

    def bid(self, **kw):
        values = dict(ad_group=self.group, bid_version=1, amount=30, created_by=USER_ID)
        values.update(kw)
        return Bid(**values)

    def case_bid_constraints_in_db(self):
        for kw in (dict(amount=0), dict(amount=2**53)):
            with self.assertRaises(IntegrityError, msg=kw), transaction.atomic():
                self.bid(**kw).save()
        self.bid(amount=1).save()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.bid(amount=2).save()  # 같은 (광고그룹, 버전)

    def case_bid_is_append_only_and_cannot_be_overwritten(self):
        self.bid().save()
        row = Bid.objects.get(ad_group=self.group, bid_version=1)
        row.amount = 99
        with self.assertRaises(AppendOnlyError):
            row.save()
        with self.assertRaises(AppendOnlyError):
            row.delete()
        with self.assertRaises(AppendOnlyError):
            Bid.objects.filter(ad_group=self.group).update(amount=99)
        with self.assertRaises((IntegrityError, AppendOnlyError)), transaction.atomic():
            self.bid(pk=row.pk, amount=999).save()
        with self.assertRaises(AppendOnlyError):
            Bid.objects.bulk_create([self.bid(pk=row.pk, amount=999)], update_conflicts=True, update_fields=["amount"], unique_fields=["bid_id"])
        self.assertEqual(Bid.objects.get(pk=row.pk).amount, 30)

    def case_profile_values_are_bounded_in_db(self):
        """카테고리 NULL 또는 0~255, 가중치 각 0~100, 세 가중치 합 ≤ 100 은 DB 가 막는다(설계 §10 "광고 카테고리·과정 가중치", DRD O12).
        PostgreSQL 은 CHECK 를 이름 순으로 보므로 합이 smallint 를 넘는 값도 합 CHECK 의 넘침(DataError)이 아니라 범위 CHECK 위반이다. 광고 하나에 한 줄이다."""
        def profile(**kw):
            return AdGroupProfile.objects.create(**{"ad_group": self.group, "created_at": NOW, "changed_at": NOW, **kw})

        for kw in (dict(), dict(category=0), dict(category=255, weight_mlo=34, weight_aio=33, weight_skn=33), dict(weight_mlo=100), dict(weight_skn=100)):
            with transaction.atomic():
                profile(**kw).delete()  # 지금 상태 한 줄 — 추가 전용이 아니다
        bad = {"category_range": [dict(category=-1), dict(category=256)],
               "weight_range": [dict(weight_mlo=30000, weight_aio=30000),  # 처음에 — 합 CHECK 가 먼저 검사되면(이름 순이 바뀌면) 여기서 넘침 오류(DataError)가 난다
                                dict(weight_mlo=-1), dict(weight_mlo=101), dict(weight_aio=-1), dict(weight_aio=101), dict(weight_skn=-1), dict(weight_skn=101)],
               "weight_sum": [dict(weight_mlo=50, weight_aio=50, weight_skn=1)]}
        for name, cases in bad.items():
            for kw in cases:
                with self.subTest(kw), self.assertRaises(IntegrityError) as e, transaction.atomic():
                    profile(**kw)
                self.assertIn(f"campaigns_ad_group_profile_{name}", str(e.exception))
        profile()
        with self.assertRaises(IntegrityError), transaction.atomic():
            profile()  # 같은 광고에 둘째 줄
        with self.assertRaises(ProtectedError):
            self.group.delete()

    def test_bid_and_ad_group_model(self):
        self.run_cases()

