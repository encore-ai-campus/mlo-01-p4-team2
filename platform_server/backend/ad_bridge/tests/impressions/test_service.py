"""공유 노출의 대기 저장·중복·작업자 전송·재시도를 가짜 광고 응답과 실제 저장소로 확인한다."""

from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
from uuid import uuid4

from django.test import TestCase

from ad_bridge.api.errors import AdsHTTPError
from ad_bridge.decisions.store import remember_decision
from ad_bridge.delivery.worker import dispatch_due_impressions
from ad_bridge.impressions import store
from ad_bridge.impressions.service import queue_impression
from ad_bridge.results.service import refresh_result
from ad_bridge.tests.support import received_reaction, shared_decision


class SharedImpressionServiceTests(TestCase):
    def setUp(self):
        self.decision = shared_decision()
        remember_decision(self.decision)
        self.owner, self.subject = uuid4(), uuid4()
        self.now = datetime(2026, 10, 15, 1, 0, 1, tzinfo=timezone.utc)
        self.client = Mock()
        self.client.report_impression.side_effect = received_reaction

    def queue(self, *, owner=None, subject=None):
        return queue_impression(
            store=store, decision_id=self.decision.decision_id,
            owner_id=owner or self.owner, subject_id=subject or self.subject,
            occurred_at="2026-10-15T01:00:01.123000Z", now=lambda: self.now,
        )

    def dispatch(self, at=None):
        return dispatch_due_impressions(client=self.client, store=store, now=lambda: at or self.now)

    def test_queue_never_calls_the_ads_server(self):
        record, created = self.queue()
        self.assertTrue(created)
        self.assertEqual((record.delivery_state, record.attempts, record.next_attempt_at),
                         ("UNCONFIRMED", 0, self.now))
        self.client.report_impression.assert_not_called()
        self.assertEqual(self.dispatch()["acknowledged"], 1)
        self.client.report_impression.assert_called_once()

    def test_duplicate_notification_sends_only_first_request(self):
        first, created = self.queue()
        duplicate, duplicate_created = self.queue(subject=uuid4())
        self.assertTrue(created)
        self.assertFalse(duplicate_created)
        self.assertEqual(duplicate, first)
        self.dispatch()
        self.client.report_impression.assert_called_once()
        request = self.client.report_impression.call_args.kwargs
        self.assertEqual(request["subject_id"], str(self.subject))
        self.assertNotIn("owner_id", request)

    def test_each_viewer_sends_their_own_pseudonym(self):
        first, _ = self.queue()
        other_subject = uuid4()
        second, _ = self.queue(owner=uuid4(), subject=other_subject)
        self.assertNotEqual(first.reaction_id, second.reaction_id)
        self.assertEqual(self.dispatch()["acknowledged"], 2)
        self.assertCountEqual([c.kwargs["subject_id"] for c in self.client.report_impression.call_args_list],
                              [str(self.subject), str(other_subject)])

    def test_shared_retry_preserves_original_payload_after_secret_change(self):
        self.client.report_impression.side_effect = AdsHTTPError(503)
        first, _ = self.queue()
        self.assertEqual(self.dispatch()["unconfirmed"], 1)
        request = self.client.report_impression.call_args.kwargs.copy()
        retry = store.get_impression(decision_id=self.decision.decision_id, owner_id=self.owner)
        self.assertEqual(retry.delivery_state, "UNCONFIRMED")
        self.queue(subject=uuid4())
        self.client.report_impression.assert_called_once()
        self.client.report_impression.side_effect = received_reaction
        counts = self.dispatch(retry.next_attempt_at + timedelta(seconds=1))
        self.assertEqual(counts["acknowledged"], 1)
        self.assertEqual(self.client.report_impression.call_args.kwargs, request)

    def test_result_lookup_uses_viewers_saved_reaction(self):
        first, _ = self.queue()
        other, _ = self.queue(owner=uuid4(), subject=uuid4())
        self.dispatch()
        self.client.get_reaction.return_value = received_reaction(
            decision_id=first.decision_id, reaction_id=first.reaction_id,
            subject_id=first.subject_id, occurred_at=first.occurred_at,
        )
        result = refresh_result(
            client=self.client, store=store, decision_id=self.decision.decision_id, owner_id=self.owner,
        )
        self.assertEqual(result.reaction_id, first.reaction_id)
        self.assertNotEqual(result.reaction_id, other.reaction_id)
        self.client.get_reaction.assert_called_once_with(
            decision_id=first.decision_id, reaction_id=first.reaction_id,
        )
