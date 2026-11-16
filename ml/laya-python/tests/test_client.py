import copy
import json
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from laya_python.client import AdsClient
from laya_python.core import Failure
from laya_python.http_client import Response, json_bytes

BASE = "http://127.0.0.1:8765/api/internal/v1"
IDS = [f"00000000-0000-4000-8000-{number:012d}" for number in range(1, 205)]
STAMP = "2026-11-12T10:00:00.000000Z"
REQUEST_ID = "11111111-1111-4111-8111-111111111111"


def response(status, body=None, headers=None):
    return Response(status, {"x-request-id": REQUEST_ID, **(headers or {})}, json_bytes(body or {}))


def profile(ad_id, category=None, version=1):
    return {"ad_group_id": ad_id, "version": version, "category": category,
            "weight_mlo": 0, "weight_aio": 0, "weight_skn": 0, "changed_at": STAMP}


def row(ad_id):
    return {"ad_group_id": ad_id, "name": "ignore this ad name", "updated_at": STAMP,
            "creatives": [{"creative_id": IDS[-1], "title": "creative title", "redirect_url": "https://example.com/ad",
                           "image_url": "https://example.com/ad.png", "updated_at": STAMP}], "profile": None}


def success(items, result="unchanged"):
    return response(200, {"items": [{"ad_group_id": item["ad_group_id"], "result": result,
                                    "profile": profile(item["ad_group_id"], item["category"])} for item in items]})


class Scripted:
    def __init__(self, *outcomes):
        self.outcomes = iter(outcomes)
        self.calls = []

    def __call__(self, method, url, payload, **kwargs):
        self.calls.append((method, url, copy.deepcopy(payload), kwargs))
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class ReadTests(unittest.TestCase):
    def test_pages_keep_state_and_cursor_and_creative_title(self):
        source = row(IDS[0])
        source["extra"] = "ignored"
        transport = Scripted(response(200, {"items": [source], "next_after": IDS[0]}),
                             response(200, {"items": [row(IDS[1])], "next_after": None}))
        pages = list(AdsClient(BASE, "test-key", transport=transport).pages())
        self.assertEqual(pages[0][0].title, "creative title")
        self.assertEqual(pages[1][0].ad_group_id, IDS[1])
        queries = [parse_qs(urlsplit(call[1]).query) for call in transport.calls]
        self.assertEqual(queries[0], {"limit": ["200"], "state": ["unclassified"]})
        self.assertEqual(queries[1]["after"], [IDS[0]])
        self.assertEqual(queries[1]["state"], ["unclassified"])
        self.assertTrue(all(call[3]["headers"] == {"X-Internal-Key": "test-key"} for call in transport.calls))

    def test_full_query_omits_state(self):
        transport = Scripted(response(200, {"items": [], "next_after": None}))
        self.assertEqual(list(AdsClient(BASE, "key", transport=transport).pages(full=True)), [[]])
        self.assertNotIn("state", parse_qs(urlsplit(transport.calls[0][1]).query))

    def test_creative_count_failure_stays_per_ad(self):
        zero, many = row(IDS[0]), row(IDS[1])
        zero["creatives"] = []
        many["creatives"] *= 2
        transport = Scripted(response(200, {"items": [zero, many], "next_after": None}))
        ads = list(AdsClient(BASE, "key", transport=transport).pages())[0]
        self.assertEqual([ad.input_error for ad in ads], ["CREATIVE_COUNT_MISMATCH"] * 2)

    def test_invalid_and_empty_creative_do_not_use_ad_name(self):
        source = row(IDS[0])
        source["creatives"][0]["title"] = ""
        transport = Scripted(response(200, {"items": [source], "next_after": None}))
        ad = list(AdsClient(BASE, "key", transport=transport).pages())[0][0]
        self.assertEqual(ad.input_error, "INVALID_CREATIVE")
        self.assertIsNone(ad.title)

    def test_duplicate_and_nonprogressing_cursor_are_protocol_errors(self):
        bodies = [
            {"items": [row(IDS[0]), row(IDS[0])], "next_after": None},
            {"items": [row(IDS[1])], "next_after": IDS[0]},
            {"items": [], "next_after": IDS[0]},
            {"items": [row(IDS[0])]},
        ]
        for body in bodies:
            with self.subTest(body=body), self.assertRaisesRegex(Failure, "ADS_INVALID_RESPONSE"):
                list(AdsClient(BASE, "key", transport=Scripted(response(200, body))).pages())

    def test_duplicate_across_pages_is_rejected(self):
        transport = Scripted(response(200, {"items": [row(IDS[0])], "next_after": IDS[0]}),
                             response(200, {"items": [row(IDS[0])], "next_after": None}))
        with self.assertRaisesRegex(Failure, "ADS_INVALID_RESPONSE"):
            list(AdsClient(BASE, "key", transport=transport).pages())

    def test_remote_base_and_missing_key_fail_before_transport(self):
        for base, key in (("http://192.168.0.204/api/internal/v1", "key"), (BASE, "")):
            transport = Scripted()
            with self.assertRaises(Failure):
                AdsClient(base, key, transport=transport)
            self.assertEqual(transport.calls, [])


class WriteTests(unittest.TestCase):
    def setUp(self):
        self.items = [{"ad_group_id": IDS[i], "category": i} for i in range(3)]

    def test_pending_is_yielded_before_request_and_success_is_confirmed(self):
        transport = Scripted(success(self.items))
        events = AdsClient(BASE, "key", transport=transport).write_categories(self.items)
        self.assertEqual(next(events), {"kind": "pending", "ids": IDS[:3]})
        self.assertEqual(transport.calls, [])
        confirmed = next(events)
        self.assertEqual(confirmed["kind"], "succeeded")
        self.assertEqual([item["result"] for item in confirmed["items"]], ["unchanged"] * 3)
        self.assertEqual(transport.calls[0][2], {"items": self.items})
        self.assertNotIn("source", transport.calls[0][2])

    def test_created_changed_unchanged_and_unknown_fields(self):
        reply = json.loads(success(self.items).body)
        for item, result in zip(reply["items"], ("created", "changed", "unchanged")):
            item["result"] = result
            item["profile"]["version"] = 1 if result == "created" else 3
            item["new_field"] = "ignored"
        events = list(AdsClient(BASE, "key", transport=Scripted(response(200, reply))).write_categories(self.items))
        self.assertEqual([item["result"] for item in events[-1]["items"]], ["created", "changed", "unchanged"])

    def test_invalid_item_types_duplicates_extra_and_limit_do_not_send(self):
        bad_batches = [[{**self.items[0], "category": value}] for value in (True, 1.0, "1", None, -1, 256)]
        bad_batches += [[self.items[0], self.items[0]], [{**self.items[0], "source": "laya"}],
                        [{"ad_group_id": value, "category": 0} for value in IDS[:201]]]
        for items in bad_batches:
            transport = Scripted()
            with self.subTest(items=items), self.assertRaises(Failure):
                list(AdsClient(BASE, "key", transport=transport).write_categories(items))
            self.assertEqual(transport.calls, [])

    def test_200_items_are_supported_and_body_limit_is_checked(self):
        items = [{"ad_group_id": value, "category": 0} for value in IDS[:200]]
        transport = Scripted(success(items))
        self.assertEqual(len(list(AdsClient(BASE, "key", transport=transport).write_categories(items))[-1]["items"]), 200)
        self.assertLessEqual(len(json_bytes(transport.calls[0][2])), 64 * 1024)
        with patch("laya_python.client.json_bytes", return_value=b"x" * (64 * 1024 + 1)), self.assertRaisesRegex(Failure, "CATEGORY_BATCH_TOO_LARGE"):
            list(AdsClient(BASE, "key", transport=Scripted()).write_categories(self.items))

    def test_400_excludes_error_ids_and_resends_remaining_without_reclassification(self):
        reject = response(400, {"errors": [{"pointer": "/items/1/category"}, {"pointer": "/items/1/ad_group_id"}]})
        remaining = [self.items[0], self.items[2]]
        transport = Scripted(reject, success(remaining))
        events = list(AdsClient(BASE, "key", transport=transport).write_categories(self.items))
        self.assertEqual(events[1]["ids"], [IDS[1]])
        self.assertEqual(events[2]["ids"], [IDS[0], IDS[2]])
        self.assertEqual(transport.calls[1][2], {"items": remaining})

    def test_second_validation_stage_can_reveal_another_error(self):
        transport = Scripted(response(400, {"errors": [{"pointer": "/items/0/category"}]}),
                             response(400, {"errors": [{"pointer": "/items/1/ad_group_id"}]}), success([self.items[1]]))
        events = list(AdsClient(BASE, "key", transport=transport).write_categories(self.items))
        self.assertEqual([event["ids"] for event in events if event["kind"] == "failed"], [[IDS[0]], [IDS[2]]])
        self.assertEqual(transport.calls[-1][2], {"items": [self.items[1]]})

    def test_bad_or_global_pointer_rejects_whole_batch(self):
        errors_list = [[], [{"pointer": "/items"}], [{"pointer": "/items/99/category"}],
                       [{"pointer": "/items/0/category"}, {"pointer": "/items"}], [{"pointer": "/items/01/category"}]]
        for errors in errors_list:
            transport = Scripted(response(400, {"errors": errors}))
            events = list(AdsClient(BASE, "key", transport=transport).write_categories(self.items))
            self.assertEqual(events[-1]["ids"], IDS[:3])
            self.assertEqual(len(transport.calls), 1)

    def test_malformed_200_is_unknown_not_failure(self):
        replies = [response(200, {"items": []}), response(200, {"items": [{"ad_group_id": IDS[1]}]}),
                   Response(200, {"x-request-id": REQUEST_ID}, b"not-json")]
        for reply in replies:
            with self.subTest(reply=reply), self.assertRaisesRegex(Failure, "WRITE_OUTCOME_UNKNOWN"):
                list(AdsClient(BASE, "key", transport=Scripted(reply)).write_categories(self.items))

    def test_empty_audit_request(self):
        transport = Scripted(response(200, {"items": []}))
        events = list(AdsClient(BASE, "key", transport=transport).write_categories([]))
        self.assertEqual(events[-1]["items"], [])
        self.assertEqual(transport.calls[0][2], {"items": []})

    def test_rejected_empty_audit_is_run_error(self):
        with self.assertRaisesRegex(Failure, "ADS_AUDIT_REJECTED"):
            list(AdsClient(BASE, "key", transport=Scripted(response(400))).write_categories([]))

    def test_wrong_saved_category_order_or_profile_is_unknown(self):
        for change in ("category", "order", "version", "request-id"):
            reply = json.loads(success(self.items).body)
            headers = None
            if change == "category":
                reply["items"][0]["profile"]["category"] = 99
            elif change == "order":
                reply["items"].reverse()
            elif change == "version":
                reply["items"][0]["profile"]["version"] = True
            else:
                headers = {"x-request-id": "not-a-uuid"}
            with self.subTest(change=change), self.assertRaisesRegex(Failure, "WRITE_OUTCOME_UNKNOWN"):
                list(AdsClient(BASE, "key", transport=Scripted(response(200, reply, headers))).write_categories(self.items))


class RetryTests(unittest.TestCase):
    def setUp(self):
        self.items = [{"ad_group_id": IDS[0], "category": 12}]

    def test_503_obeys_retry_after_then_unchanged_is_success(self):
        sleeps = []
        transport = Scripted(response(503, headers={"retry-after": "7"}), success(self.items))
        events = list(AdsClient(BASE, "key", transport=transport, sleep=sleeps.append).write_categories(self.items))
        self.assertEqual(sleeps, [7])
        self.assertEqual(events[-1]["kind"], "succeeded")
        self.assertEqual(transport.calls[0][2], transport.calls[1][2])

    def test_transport_retries_10_30_and_stops_at_three(self):
        sleeps = []
        transport = Scripted(*[Failure("HTTP_TRANSPORT_ERROR") for _ in range(3)])
        with self.assertRaisesRegex(Failure, "WRITE_OUTCOME_UNKNOWN"):
            list(AdsClient(BASE, "key", transport=transport, sleep=sleeps.append).write_categories(self.items))
        self.assertEqual(sleeps, [10, 30])
        self.assertEqual(len(transport.calls), 3)

    def test_unknown_context_survives_later_400_401_403_413(self):
        for first in (Failure("HTTP_TRANSPORT_ERROR"), response(503, headers={"retry-after": "0"})):
            for status in (400, 401, 403, 413):
                transport = Scripted(first, response(status, {"errors": [{"pointer": "/items/0/category"}]}))
                events = AdsClient(BASE, "key", transport=transport, sleep=lambda _: None).write_categories(self.items)
                self.assertEqual(next(events)["kind"], "pending")
                with self.subTest(first=first, status=status), self.assertRaisesRegex(Failure, "WRITE_OUTCOME_UNKNOWN"):
                    next(events)
                self.assertEqual(len(transport.calls), 2)

    def test_auth_without_previous_ambiguity_stops_without_retry(self):
        for status in (401, 403):
            transport = Scripted(response(status))
            with self.assertRaisesRegex(Failure, f"ADS_AUTH_{status}"):
                list(AdsClient(BASE, "key", transport=transport).write_categories(self.items))
            self.assertEqual(len(transport.calls), 1)

    def test_get_503_and_transport_retry_are_bounded(self):
        sleeps = []
        transport = Scripted(Failure("HTTP_TRANSPORT_ERROR"), response(503, headers={"retry-after": "9"}),
                             response(200, {"items": [], "next_after": None}))
        list(AdsClient(BASE, "key", transport=transport, sleep=sleeps.append).pages())
        self.assertEqual(sleeps, [10, 9])

    def test_503_without_retry_after_keeps_write_unknown(self):
        transport = Scripted(response(503))
        with self.assertRaisesRegex(Failure, "WRITE_OUTCOME_UNKNOWN"):
            list(AdsClient(BASE, "key", transport=transport).write_categories(self.items))
        self.assertEqual(len(transport.calls), 1)


if __name__ == "__main__":
    unittest.main()
