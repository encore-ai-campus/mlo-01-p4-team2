"""정합성 점검(`check_ledger`)은 **한 읽기 스냅샷**에서 지갑과 원장을 비교한다(검토 DB-1c `7aac929`·`6fc9664` 보완 — 설계 §10).

기본 격리 수준(`READ COMMITTED`)은 SELECT 마다 새 스냅샷이라, 점검이 여러 조회를 비교하는 동안 정상 과금이 커밋되면 서로 다른 시점의 값을 비교해
거짓 불일치를 보고한다. 실제 연결을 스레드마다 하나씩 쓰고, 점검이 지갑을 읽은 직후(연결 훅)에 다른 연결이 과금을 커밋하게 해 순서를 고정한다.
(6-16f 전에는 이 파일이 캠페인 일 소진액 표와 재계산의 경쟁도 다뤘다 — 소진액은 이제 원장에서 세므로 그 시험들은 없어졌다.)
"""
import uuid

from django.db import connection, transaction
from django.db.models import F
from django.test import TransactionTestCase, override_settings, tag

from billing import api
from billing.models import Wallet
from tests.support.concurrency import WAIT, Worker
from tests.support.factories import NOW, WHOLE, make_advertiser
from tests.support.cases import Cases


@tag("security")
class ConsistentLedgerCheckTests(Cases, TransactionTestCase):
    """검토 DB-1c(7aac929) P2: 정합성 검사가 검사 도중 커밋된 정상 과금을 불일치로 오인했다."""

    def setUp(self):
        self.adv = make_advertiser()
        with override_settings(SIGNUP_GRANT_POINTS=10_000):
            api.grant_signup(self.adv, now=NOW)
        self.camp = uuid.uuid4()

    def charge(self, amount, ref, now=NOW):
        return api.charge_for_reaction(self.adv.pk, self.camp, "IMPRESSION", WHOLE, amount, ref, now=now)

    def tamper(self):
        Wallet.objects.filter(owner_id=self.adv.pk).update(balance=F("balance") - 3)  # 원장 없이 잔액만 바뀐 상태

    def case_a_charge_committed_in_the_middle_of_the_check_is_not_reported_as_a_mismatch(self):
        self.charge(10, "m:r1")
        fired, workers = [], []

        def hook(execute, sql, params, many, context):
            result = execute(sql, params, many, context)
            if 'FROM "billing_wallet"' in sql and not fired:  # 지갑을 읽은 직후 — 원장은 아직 읽지 않았다
                fired.append(1)
                worker = Worker(lambda: self.charge(20, "m:r2"))  # 정상 과금이 커밋된다(잔액·원장이 함께 30 줄어듦)
                worker.start()
                worker.join(WAIT)
                workers.append(worker)
            return result

        with connection.execute_wrapper(hook):
            problems = api.check_ledger()
        self.assertTrue(fired)
        self.assertEqual(workers[0].errors, [])
        self.assertEqual(problems, [])  # 검사 전체가 하나의 읽기 스냅샷이라 서로 다른 시점의 값을 비교하지 않는다
        self.assertEqual(api.check_ledger(), [])

    def case_inside_an_existing_read_committed_transaction_the_check_refuses_instead_of_guessing(self):
        """검토 7aac929→6fc9664: `atomic()` 이라고 다른 연결의 커밋이 막히지 않는다. 스냅샷을 보장할 수 없으면 조용히 건너뛰지 않고 거절한다."""
        self.charge(10, "m:r1")
        with transaction.atomic(), self.assertRaises(api.ConsistentReadUnavailable) as caught:
            api.check_ledger()
        self.assertIn("바꿀 수 없", str(caught.exception))
        self.assertIn("quiescent=True", str(caught.exception))

    def case_no_session_isolation_level_lets_an_existing_transaction_through(self):
        """세션 기본 격리 수준(`default_transaction_isolation`)과 무관하게, 이미 시작한 트랜잭션 안에서는 `quiescent=True` 가 아니면 거절한다.
        (MySQL 때의 '다음 트랜잭션 하나만 바꾸는 SET TRANSACTION' 경우는 PostgreSQL 에 없다 — 그 시험은 이것으로 합쳤다.)"""
        self.charge(10, "m:r1")
        for level in ("read committed", "repeatable read", "serializable"):
            with connection.cursor() as cursor:
                cursor.execute(f"SET default_transaction_isolation = '{level}'")
            try:
                with transaction.atomic(), self.assertRaises(api.ConsistentReadUnavailable, msg=level):
                    api.check_ledger()
            finally:
                with connection.cursor() as cursor:
                    cursor.execute("RESET default_transaction_isolation")

    def case_the_check_reads_in_one_repeatable_read_read_only_transaction(self):
        """`SET TRANSACTION` 을 트랜잭션 밖에서 하면 PostgreSQL 은 경고만 내고 무시한다 — 실제로 그 트랜잭션이 RR·읽기 전용인지 본다."""
        with api.consistent_read(), connection.cursor() as cursor:
            cursor.execute("SELECT current_setting('transaction_isolation'), current_setting('transaction_read_only')")
            self.assertEqual(cursor.fetchone(), ("repeatable read", "on"))

    def case_quiescent_is_the_explicit_way_to_check_inside_a_transaction_without_concurrent_writers(self):
        self.charge(10, "m:r1")
        with transaction.atomic():
            self.assertEqual(api.check_ledger(quiescent=True), [])
            self.tamper()
            self.assertTrue(any("잔액" in p for p in api.check_ledger(quiescent=True)))

    def case_the_check_still_reports_a_real_mismatch_inside_its_snapshot(self):
        self.charge(10, "m:r1")
        self.tamper()
        self.assertTrue(any("잔액" in p for p in api.check_ledger()))

    def test_consistent_ledger_check(self):
        self.run_cases()
