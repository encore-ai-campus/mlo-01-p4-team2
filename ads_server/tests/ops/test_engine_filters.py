import unittest
from datetime import timedelta
from unittest import mock

from engine.api import UnknownName, decide
from random import Random

from tests.support.engine_factory import NOW, cand, request
from tests.support.cases import Cases


def run(*candidates, extra=()):
    return decide(candidates, request(), Random(1), extra_filters=extra)


class FilterTests(Cases, unittest.TestCase):
    def case_each_filter_rejects_with_reason(self):
        cases = {
            "campaign_not_active": dict(campaign_status="PAUSED"),
            "ad_group_disabled": dict(ad_group_enabled=False),
            "not_started": dict(period_start=NOW + timedelta(seconds=1)),
            "expired": dict(period_end=NOW),
            "below_floor": dict(bid_amount=9),
            "budget_exhausted": dict(remaining_daily_budget=29),
            "insufficient_balance": dict(wallet_balance=29),
        }
        for reason, kw in cases.items():
            result = run(cand(**kw))
            self.assertIsNone(result.selection.chosen, reason)
            self.assertEqual(result.rejected, {reason: 1}, reason)
            self.assertEqual(result.considered, 1)
        for status in ("SUSPENDED", "ENDED"):
            self.assertEqual(run(cand(campaign_status=status)).rejected, {"campaign_not_active": 1})

    def case_open_ended_campaign_passes_period(self):
        self.assertIsNotNone(run(cand(period_end=None)).selection.chosen)

    def case_budget_and_balance_boundary(self):
        self.assertIsNotNone(run(cand(remaining_daily_budget=30, wallet_balance=30)).selection.chosen)  # 같으면 통과
        self.assertEqual(run(cand(remaining_daily_budget=29)).rejected, {"budget_exhausted": 1})  # 1 모자라면 거절
        self.assertEqual(run(cand(wallet_balance=29)).rejected, {"insufficient_balance": 1})

    def case_budget_and_balance_use_applied_amount_not_bid(self):
        # 입찰 80 이지만 상한 50 으로 적용된다: 잔액·예산 50 이면 통과
        result = run(cand(bid_amount=80, cap=50, wallet_balance=50, remaining_daily_budget=50))
        self.assertEqual(result.selection.chosen.applied_amount, 50)

    def case_filter_order_first_failure_wins_and_counts_by_reason(self):
        both = cand(campaign_status="PAUSED", bid_amount=1)  # 상태와 하한 둘 다 걸리지만 먼저 걸린 상태로만 센다
        result = run(both, cand(bid_amount=1), cand(bid_amount=1), cand(), cand(wallet_balance=0))
        self.assertEqual(result.rejected, {"campaign_not_active": 1, "below_floor": 2, "insufficient_balance": 1})
        self.assertEqual(result.considered, 5)
        self.assertEqual(result.selection.trace["candidates"], 1)

    def case_extra_filters_run_after_the_mandatory_ones_and_unknown_names_fail_fast(self):
        odd = lambda candidate, effect, req: "odd_bid" if candidate.bid_amount % 2 else None  # noqa: E731
        with mock.patch.dict("engine.registry._FILTERS", {"no-odd": odd}):
            result = run(cand(campaign_status="PAUSED", bid_amount=31), cand(bid_amount=31), cand(bid_amount=30), extra=("no-odd",))
            self.assertEqual(result.rejected, {"campaign_not_active": 1, "odd_bid": 1})  # 필수 필터가 먼저 — 둘 다 걸리면 필수 사유로 센다
            self.assertEqual(result.selection.chosen.bid_amount, 30)
        for bad in ("no-such-filter", "status"):  # 필수 필터 이름도 설정에 적을 수 없다(뺄 수도 겹쳐 돌릴 수도 없다)
            with self.assertRaises(UnknownName):
                run(extra=(bad,))  # 후보가 없어도 바로 드러난다

    def case_candidate_for_another_slot_is_an_error(self):
        import uuid

        with self.assertRaises(ValueError):
            run(cand(slot_id=uuid.uuid4()))

    def test_filter(self):
        self.run_cases()
