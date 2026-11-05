"""매체 API 요청 제한의 공유 토큰 버킷(설계 §10 "요청 제한 구현", masters M6). 모든 프로세스가 같은 DB 행을 쓴다 — 시각은 시험이 정한다."""
from django.core.exceptions import ImproperlyConfigured
from django.test import TransactionTestCase

from masters import api as masters
from masters.models import MediaRateBucket
from tests.support.cases import Cases
from tests.support.concurrency import run_together
from tests.support.factories import at, make_admin


class TokenBucketTests(Cases, TransactionTestCase):  # 동시성 경우가 실제 연결 여럿을 쓴다
    def setUp(self):
        self.admin = make_admin()
        self.media = self.new_media("매체")

    def new_media(self, name):
        return masters.create_media(self.admin, name).media_id

    def take(self, when=0.0, group="decisions", rate=2, burst=3, media=None):
        return masters.take_rate_token(media or self.media, group, rate, burst, now=at(when))

    def case_burst_then_blocked_with_integer_retry_after_of_at_least_one(self):
        self.assertEqual([self.take() for _ in range(3)], [None, None, None])
        wait = self.take()
        self.assertIsInstance(wait, int)
        self.assertEqual(wait, 1)  # 초당 2개: 토큰 1개까지 0.5초 → 올림해 1초
        self.assertEqual(MediaRateBucket.objects.count(), 1)  # (매체, 경로군) 행 하나

    def case_refill_at_the_configured_rate_and_never_beyond_burst(self):
        [self.take() for _ in range(3)]
        self.assertIsNotNone(self.take())
        self.assertEqual([self.take(1.0), self.take(1.0)], [None, None])  # 1초에 2개 충전
        self.assertIsNotNone(self.take(1.0))
        self.assertEqual([self.take(101.0) for _ in range(3)], [None, None, None])  # 오래 쉬어도 버스트(3)를 넘지 않는다
        self.assertIsNotNone(self.take(101.0))

    def case_retry_after_grows_with_the_deficit_and_rounds_up(self):
        slow = dict(rate=0.25, burst=1)  # 초당 0.25 = 4초에 하나
        self.assertIsNone(self.take(0, **slow))
        self.assertEqual(self.take(0, **slow), 4)
        self.assertEqual(self.take(3.9, **slow), 1)  # 0.1초 남음 → 올림 1
        self.assertIsNone(self.take(4.1, **slow))

    def case_buckets_are_independent_per_media_and_group(self):
        [self.take() for _ in range(3)]
        self.assertIsNotNone(self.take())
        self.assertIsNone(self.take(group="reads"))  # 같은 매체의 다른 경로군
        self.assertIsNone(self.take(media=self.new_media("다른 매체")))  # 다른 매체

    def case_clock_going_backwards_does_not_grant_tokens(self):
        [self.take(50) for _ in range(3)]
        self.assertIsNotNone(self.take(0))  # 50초 전 시각: 경과 0
        self.assertIsNotNone(self.take(50))  # 마지막 시각도 50초 그대로(과거로 돌아가 다시 충전하지 않는다)

    def case_a_late_request_with_an_earlier_time_does_not_lose_tokens_either(self):
        three = dict(rate=1, burst=3)
        self.assertIsNone(self.take(10, **three))  # 남은 토큰 2
        self.assertEqual([self.take(8, **three), self.take(8, **three)], [None, None])  # 2초 이른 시각: 경과 0 — 음수 경과로 토큰을 깎지 않는다
        self.assertIsNotNone(self.take(10, **three))

    def case_reordered_requests_never_charge_the_same_elapsed_time_twice(self):
        """시각을 먼저 읽은 요청이 **늦게** 도착해도 마지막 갱신 시각이 과거로 돌아가면 안 된다.

        rate=1, burst=1: 0초에 한 번, 1초 시점에 한 번 → 1초 구간에서 허용은 딱 2건. 늦게 도착한 0.5초 요청이 시각을 되돌리면 3건이 통과한다.
        """
        one = dict(rate=1, burst=1)
        passed = [self.take(0, **one) is None, self.take(1, **one) is None, self.take(0.5, **one) is None, self.take(1, **one) is None]
        self.assertEqual(passed, [True, True, False, False])

    def case_concurrent_takes_never_exceed_the_burst(self):
        def worker():
            return [self.take(0, rate=1, burst=20) for _ in range(10)]  # 같은 시각: 충전 없이 버스트만

        results, errors = run_together([worker] * 8)
        self.assertEqual(errors, [])
        self.assertEqual(sum(r.count(None) for r in results), 20)  # 연결 8개 × 10번 = 80번 중 정확히 버스트만큼만 통과
        self.assertEqual(MediaRateBucket.objects.count(), 1)  # 처음 생기는 행에 동시에 와도 하나(유일 제약 충돌 없이 갱신 경로로)

    def case_misconfigured_limits_are_refused_before_touching_the_bucket(self):
        for rate, burst in ((0, 3), (-1, 3), (True, 3), (2, 0), (2, 1.5), (2, True)):
            with self.assertRaises(ImproperlyConfigured, msg=(rate, burst)):
                self.take(rate=rate, burst=burst)
        self.assertEqual(MediaRateBucket.objects.count(), 0)

    def test_token_bucket(self):
        self.run_cases()
