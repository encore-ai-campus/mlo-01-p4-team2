"""실제 저장소로 사용자별 회차·소유권·동일 소재 결정 통합을 검증한다."""

from datetime import datetime, timezone, timedelta
from unittest.mock import Mock, call
from uuid import uuid4

from django.core import signing
from django.test import TestCase, override_settings

from ad_bridge import placements
from ad_bridge.api.errors import AdsHTTPError, AdsTransportError
from ad_bridge.api.types import Ad, Decision
from ad_bridge.models import PlacementBatch, IssuedDecision
from ad_bridge.clicks import click_target
from ad_bridge.placements import get_batch, public_batch, transfer_token, transfer_identity


@override_settings(ADS_ROOFTOP_SLOT_ID="10000000-0000-0000-0000-000000000001",
                   ADS_PORTAL_LEFT_SLOT_ID="10000000-0000-0000-0000-000000000002",
                   ADS_PORTAL_RIGHT_SLOT_ID="10000000-0000-0000-0000-000000000003")
class PlacementTests(TestCase):
    def setUp(self):
        self.owner, self.subject, self.creative = uuid4(), uuid4(), str(uuid4())
        self.now = datetime(2026, 10, 15, 1, 0, 1, tzinfo=timezone.utc)
        self.client = Mock()
        self.client.create_decision.side_effect = self.decision
        placements._last_logged.clear()

    def decision(self, slot_id, subject_id=None, segment=None):
        return Decision(str(uuid4()), slot_id, "slot", "shared" if subject_id is None else "individual",
            "2026-10-15T01:00:00Z", Ad(self.creative, "광고", "https://ads.test/image.png",
                "https://example.com", f"https://ads.test/c/click?s={subject_id}" if subject_id is not None else "https://ads.test/c/click?s={subject_id}"), None)

    def batch(self, **kwargs):
        return get_batch(client=self.client, owner_id=self.owner, subject_id=self.subject, now=self.now, **kwargs)

    def test_same_user_reuses_round_and_next_boundary_issues_once(self):
        first = self.batch()
        self.now += timedelta(seconds=8)
        second = self.batch()
        self.assertEqual(first.key, second.key)
        self.assertEqual(first.payload, second.payload)
        self.client.create_decision.assert_called_once_with(
            slot_id="10000000-0000-0000-0000-000000000001", subject_id=self.subject,
        )
        decision = IssuedDecision.objects.get(pk=first.payload[0]["decision_id"])
        self.assertEqual((decision.audience, decision.owner_id, decision.subject_id),
                         ("individual", self.owner, self.subject))
        self.assertEqual(first.owner_id, self.owner)
        self.now += timedelta(seconds=1)
        third = self.batch()
        self.assertNotEqual(first.key, third.key)
        self.assertEqual(self.client.create_decision.call_count, 2)
        self.assertEqual(third.expires_at, self.now + timedelta(seconds=10))

    def test_different_users_get_distinct_decisions_in_the_same_round(self):
        first_owner, first_subject = self.owner, self.subject
        first = self.batch()
        self.owner, self.subject = uuid4(), uuid4()
        second = self.batch()
        self.assertNotEqual(first.key, second.key)
        self.assertNotEqual(first.payload[0]["decision_id"], second.payload[0]["decision_id"])
        self.assertEqual(first.expires_at, second.expires_at)
        self.assertEqual(first.payload[0]["ad"]["creative_id"], second.payload[0]["ad"]["creative_id"])
        self.assertEqual(self.client.create_decision.call_args_list, [
            call(slot_id="10000000-0000-0000-0000-000000000001", subject_id=first_subject),
            call(slot_id="10000000-0000-0000-0000-000000000001", subject_id=self.subject),
        ])
        self.assertEqual(IssuedDecision.objects.filter(owner_id=first_owner, subject_id=first_subject).count(), 1)
        self.assertEqual(IssuedDecision.objects.filter(owner_id=self.owner, subject_id=self.subject).count(), 1)
        self.assertEqual(self.batch().payload, second.payload)
        self.assertEqual(self.client.create_decision.call_count, 2)

    def test_same_creative_keeps_each_slots_own_decision(self):
        # 명세 50 §4: 같은 소재여도 합치지 않는다. 슬롯마다 자기 결정으로 노출·클릭한다.
        transfer = str(uuid4())
        batch = self.batch(transfer_id=transfer)
        self.assertEqual(len(batch.payload), 2)
        self.assertEqual(batch.payload[0]["ad"]["creative_id"], batch.payload[1]["ad"]["creative_id"])
        self.assertNotEqual(batch.payload[0]["decision_id"], batch.payload[1]["decision_id"])
        self.assertEqual([entry["slot_id"] for entry in batch.payload],
                         ["10000000-0000-0000-0000-000000000002", "10000000-0000-0000-0000-000000000003"])
        self.assertEqual(IssuedDecision.objects.count(), 2)
        self.assertEqual(self.batch(transfer_id=transfer).payload, batch.payload)
        self.assertEqual(self.client.create_decision.call_count, 2)
        self.batch(transfer_id=str(uuid4()))
        self.assertEqual(self.client.create_decision.call_count, 4)

    def test_different_creatives_keep_separate_decisions(self):
        def different(**kwargs):
            self.creative = str(uuid4())
            return self.decision(**kwargs)
        self.client.create_decision.side_effect = different
        batch = self.batch(transfer_id=str(uuid4()))
        self.assertNotEqual(batch.payload[0]["decision_id"], batch.payload[1]["decision_id"])

    def test_pending_claim_does_not_start_a_second_external_request(self):
        PlacementBatch.objects.create(key=f"roof:{self.owner}:{int(self.now.timestamp())//10*10}",
                                      owner_id=self.owner, expires_at=self.now+timedelta(seconds=9))
        self.assertEqual(self.batch().state, "PENDING")
        self.client.create_decision.assert_not_called()

    def test_ambiguous_failure_is_not_reissued_within_the_same_round(self):
        self.client.create_decision.side_effect = AdsTransportError("timeout")
        self.assertEqual(self.batch().state, "FAILED")
        self.assertEqual(self.batch().state, "FAILED")
        self.assertEqual(self.client.create_decision.call_count, 1)

    def test_portal_owner_cannot_be_substituted(self):
        transfer = str(uuid4())
        self.batch(transfer_id=transfer)
        self.owner = uuid4()
        with self.assertRaises(ValueError):
            self.batch(transfer_id=transfer)

    def test_public_payload_has_viewer_click_url_without_internal_owner(self):
        payload = public_batch(self.batch(), self.subject, owner_id=self.owner)
        self.assertIn(str(self.subject), payload["ads"][0]["ad"]["click_url"])
        entry = payload["ads"][0]
        self.assertEqual(click_target(entry['ad']['click_token'], entry['decision_id'], self.owner),
                         entry['ad']['click_url'])
        self.assertNotIn(str(self.owner), str(payload))
        self.assertNotIn("redirect_url", str(payload))

    def test_transfer_token_is_bound_to_user_and_rejects_tampering(self):
        transfer = uuid4()
        token = transfer_token(1, transfer)
        self.assertEqual(transfer_identity(token, 1), str(transfer))
        for value, user in ((token, 2), (token + "x", 1)):
            with self.assertRaises(signing.BadSignature):
                transfer_identity(value, user)

    def test_request_during_external_issue_reuses_pending_reservation(self):
        pending = []
        def issue(**kwargs):
            pending.append(self.batch())
            return self.decision(**kwargs)
        self.client.create_decision.side_effect = issue
        ready = self.batch()
        self.assertEqual(pending[0].key, ready.key)
        self.assertEqual(pending[0].state, "PENDING")
        self.assertEqual(ready.state, "READY")
        self.assertEqual(self.client.create_decision.call_count, 1)

    def test_roof_owner_mismatch_is_rejected_without_issuing(self):
        key = f"roof:{self.owner}:{int(self.now.timestamp())//10*10}"
        for stored_owner in (None, uuid4()):
            with self.subTest(owner=stored_owner):
                PlacementBatch.objects.update_or_create(key=key, defaults={
                    "owner_id": stored_owner, "expires_at": self.now + timedelta(seconds=9),
                })
                with self.assertRaises(ValueError):
                    self.batch()
        self.client.create_decision.assert_not_called()

    def test_public_batch_rejects_another_owner_and_binds_click_to_owner(self):
        batch = self.batch()
        other = uuid4()
        with self.assertRaises(ValueError):
            public_batch(batch, self.subject, owner_id=other)
        payload = public_batch(batch, self.subject, owner_id=self.owner)
        entry = payload["ads"][0]
        with self.assertRaises(signing.BadSignature):
            click_target(entry["ad"]["click_token"], entry["decision_id"], other)
        self.assertNotEqual(payload["batch_id"], batch.key)
        self.assertEqual(payload["batch_id"], public_batch(self.batch(), self.subject, owner_id=self.owner)["batch_id"])

    def test_secret_rotation_reuses_saved_subject_and_click_url(self):
        original_subject = self.subject
        first = self.batch()
        self.subject = uuid4()
        second = self.batch()
        payload = public_batch(second, self.subject, owner_id=self.owner)
        self.assertEqual(first.payload, second.payload)
        self.assertEqual(self.client.create_decision.call_count, 1)
        self.assertIn(str(original_subject), payload["ads"][0]["ad"]["click_url"])
        self.assertNotIn(str(self.subject), payload["ads"][0]["ad"]["click_url"])
        self.assertEqual(IssuedDecision.objects.get(pk=first.payload[0]["decision_id"]).subject_id, original_subject)

    def test_legacy_shared_batch_is_preserved_and_not_reused(self):
        legacy = PlacementBatch.objects.create(
            key=f"roof:{int(self.now.timestamp())//10*10}", owner_id=None,
            state="READY", payload=[{"legacy": True}], expires_at=self.now + timedelta(seconds=9),
        )
        batch = self.batch()
        self.assertNotEqual(batch.key, legacy.key)
        self.assertEqual(self.client.create_decision.call_count, 1)
        legacy.refresh_from_db()
        self.assertEqual(legacy.payload, [{"legacy": True}])
        self.assertIsNone(legacy.owner_id)

    def test_invalid_identity_does_not_reserve_or_issue(self):
        for owner, subject in ((None, self.subject), (self.owner, None), ("invalid", self.subject)):
            with self.subTest(owner=owner, subject=subject), self.assertRaises(ValueError):
                get_batch(client=self.client, owner_id=owner, subject_id=subject, now=self.now)
        self.assertEqual(PlacementBatch.objects.count(), 0)
        self.client.create_decision.assert_not_called()

    def test_failed_round_does_not_block_another_user_or_next_round(self):
        self.client.create_decision.side_effect = AdsTransportError("timeout")
        failed = self.batch()
        original_owner, original_subject = self.owner, self.subject
        self.client.create_decision.side_effect = self.decision
        self.owner, self.subject = uuid4(), uuid4()
        self.assertEqual(self.batch().state, "READY")
        self.owner, self.subject = original_owner, original_subject
        self.assertEqual(self.batch().key, failed.key)
        self.assertEqual(self.batch().state, "FAILED")
        self.now += timedelta(seconds=9)
        self.assertEqual(self.batch().state, "READY")
        self.assertEqual(self.client.create_decision.call_count, 3)


    def test_segment_is_sent_with_every_individual_slot(self):
        self.batch(transfer_id=str(uuid4()), segment="mlo")
        self.batch(segment="mlo")
        for request in self.client.create_decision.call_args_list:
            self.assertEqual(request.kwargs["segment"], "mlo")
        self.assertEqual(self.client.create_decision.call_count, 3)

    def test_no_segment_means_no_key_at_all(self):
        self.batch()
        self.assertNotIn("segment", self.client.create_decision.call_args.kwargs)

    def test_one_failed_portal_slot_keeps_the_other_slots_ad(self):
        calls = []
        def first_fails(**kwargs):
            calls.append(kwargs["slot_id"])
            if len(calls) == 1:
                raise AdsTransportError("timeout")
            return self.decision(**kwargs)
        self.client.create_decision.side_effect = first_fails
        with self.assertLogs("ad_bridge.placements", "WARNING") as logs:
            batch = self.batch(transfer_id=str(uuid4()))
        self.assertEqual(batch.state, "READY")
        self.assertEqual(batch.payload[0], {"decision_id": None, "ad": None})
        self.assertIsNotNone(batch.payload[1]["ad"])
        payload = public_batch(batch, self.subject, owner_id=self.owner)
        self.assertEqual(payload["ads"][0], {"decision_id": None, "ad": None})
        self.assertIn("slot=10000000-0000-0000-0000-000000000002", logs.output[0])
        self.assertIn("AdsTransportError", logs.output[0])
        self.assertNotIn(str(self.subject), "\n".join(logs.output))
        self.assertNotIn(str(self.owner), "\n".join(logs.output))

    def test_failure_log_is_throttled_per_kind(self):
        self.client.create_decision.side_effect = AdsHTTPError(404, code="not_found", request_id="r1")
        with self.assertLogs("ad_bridge.placements", "WARNING") as logs:
            for _ in range(3):
                self.owner, self.subject = uuid4(), uuid4()
                self.batch()
        self.assertEqual(len(logs.output), 1)
        self.assertIn("status=404 code=not_found request_id=r1", logs.output[0])

    def test_busy_when_all_decision_slots_are_taken_writes_nothing(self):
        limit = placements._decision_slots()
        taken = 0
        while limit.acquire(blocking=False):
            taken += 1
        try:
            self.assertIsNone(self.batch())
            self.assertIsNone(self.batch(transfer_id=str(uuid4())))
        finally:
            for _ in range(taken):
                limit.release()
        self.assertEqual(PlacementBatch.objects.count(), 0)
        self.client.create_decision.assert_not_called()
        self.assertEqual(self.batch().state, "READY")
        body = placements.busy_batch()
        self.assertEqual((body["state"], body["ads"]), ("BUSY", []))

    def test_existing_reservation_is_returned_even_when_busy(self):
        ready = self.batch()
        limit = placements._decision_slots()
        taken = 0
        while limit.acquire(blocking=False):
            taken += 1
        try:
            self.assertEqual(self.batch().payload, ready.payload)
        finally:
            for _ in range(taken):
                limit.release()
        self.assertEqual(self.client.create_decision.call_count, 1)

    def test_decision_slot_setting_falls_back_to_default(self):
        for raw, expected in (("", 8), ("3", 3), ("0", 8), ("abc", 8), ("65", 8)):
            with self.subTest(raw=raw), override_settings(ADS_DECISION_CONCURRENCY=raw):
                placements._slots = None
                limit = placements._decision_slots()
                taken = 0
                while limit.acquire(blocking=False):
                    taken += 1
                for _ in range(taken):
                    limit.release()
                self.assertEqual(taken, expected)
        placements._slots = None
