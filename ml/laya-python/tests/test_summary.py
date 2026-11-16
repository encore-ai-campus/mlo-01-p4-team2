import json
import os
import unittest
from unittest.mock import patch

from laya_python.core import Failure
from laya_python.http_client import Response, json_bytes
from laya_python.summary import OPENAI_URL, OpenAISummary, read_summary

URL = "https://example.com/ad"


def summary_response(body="RAG와 에이전트 강의입니다.", *, accessible=True, url=URL, action="open_page", status="completed"):
    return Response(200, {}, json_bytes({"status": status, "output": [
        {"type": "web_search_call", "status": "completed", "action": {"type": action, "url": url}},
        {"type": "message", "content": [{"type": "output_text", "text": json.dumps({"accessible": accessible, "body": body})}]},
    ]}))


class MockSummary:
    """명시적 시험용 제공자. 키와 네트워크를 사용하지 않는다."""
    def __init__(self, responses):
        self.responses = responses

    def __call__(self, url):
        return read_summary(self.responses[url], url)


class SummaryTests(unittest.TestCase):
    def test_real_provider_requires_key_before_request(self):
        calls = []
        with self.assertRaisesRegex(Failure, "OPENAI_API_KEY_MISSING"):
            OpenAISummary("", transport=lambda *args, **kwargs: calls.append(args))
        self.assertEqual(calls, [])

    def test_explicit_mock_ignores_missing_or_present_key(self):
        for key in ("", "must-never-be-used"):
            with patch.dict(os.environ, {"OPENAI_API_KEY": key}):
                mock = MockSummary({URL: summary_response()})
                self.assertEqual(mock(URL), "RAG와 에이전트 강의입니다.")

    def test_request_uses_fixed_responses_model_low_and_web_read(self):
        calls = []

        def transport(*args, **kwargs):
            calls.append((args, kwargs))
            return summary_response()

        summary = OpenAISummary("test-only-key", transport=transport)
        self.assertEqual(summary(URL), "RAG와 에이전트 강의입니다.")
        (method, url, payload), kwargs = calls[0]
        self.assertEqual((method, url), ("POST", OPENAI_URL))
        self.assertEqual(payload["model"], "gpt-6-sol")
        self.assertEqual(payload["reasoning"], {"effort": "low"})
        self.assertEqual(payload["tools"], [{"type": "web_search", "external_web_access": True}])
        self.assertEqual(payload["tool_choice"], "required")
        self.assertFalse(payload["store"])
        self.assertEqual(json.loads(payload["input"]), {"url": URL})
        self.assertEqual(kwargs["headers"], {"Authorization": "Bearer test-only-key"})
        self.assertNotIn("X-Internal-Key", kwargs["headers"])
        self.assertEqual(payload["text"]["format"]["schema"]["required"], ["accessible", "body"])

    def test_search_only_or_wrong_page_is_not_evidence(self):
        for reply in (summary_response(action="search"), summary_response(url="https://example.com/other")):
            with self.assertRaisesRegex(Failure, "URL_READ_UNVERIFIED"):
                read_summary(reply, URL)

    def test_open_page_must_complete(self):
        body = json.loads(summary_response().body)
        body["output"][0]["status"] = "failed"
        with self.assertRaisesRegex(Failure, "URL_READ_UNVERIFIED"):
            read_summary(Response(200, {}, json_bytes(body)), URL)

    def test_missing_page_or_incomplete_response_is_failure(self):
        with self.assertRaisesRegex(Failure, "URL_READ_FAILED"):
            read_summary(summary_response("", accessible=False), URL)
        for status in ("incomplete", "failed", "in_progress"):
            with self.assertRaisesRegex(Failure, "SUMMARY_INCOMPLETE"):
                read_summary(summary_response(status=status), URL)

    def test_body_is_plain_text_and_tail_is_cut_at_2000_chars(self):
        body = "앞" * 2000 + "제거할뒷부분"
        result = read_summary(summary_response(body), URL)
        self.assertEqual(result, "앞" * 2000)
        self.assertEqual(len(result), 2000)

    def test_empty_body_is_failure(self):
        with self.assertRaisesRegex(Failure, "SUMMARY_EMPTY"):
            read_summary(summary_response(" \n "), URL)

    def test_malformed_and_wrong_types_are_safe_failures(self):
        replies = [Response(200, {}, b"not json"), Response(200, {}, b"[]"), summary_response(body=12), summary_response(accessible="true")]
        body = json.loads(summary_response().body)
        body["output"][1]["content"][0]["text"] = "not json secret"
        replies.append(Response(200, {}, json_bytes(body)))
        for reply in replies:
            with self.assertRaisesRegex(Failure, "SUMMARY_INVALID") as caught:
                read_summary(reply, URL)
            self.assertNotIn("secret", str(caught.exception))

    def test_http_and_transport_errors_do_not_expose_secrets(self):
        with self.assertRaisesRegex(Failure, "SUMMARY_API_FAILED"):
            read_summary(Response(429, {}, b"secret"), URL)

        def transport(*args, **kwargs):
            raise Failure("HTTP_TRANSPORT_ERROR")

        with self.assertRaisesRegex(Failure, "SUMMARY_TRANSPORT_FAILED"):
            OpenAISummary("test-only-key", transport=transport)(URL)

    def test_bad_url_stops_before_transport(self):
        calls = []
        with self.assertRaisesRegex(Failure, "INVALID_URL"):
            OpenAISummary("test-only-key", transport=lambda *args, **kwargs: calls.append(args))("file:///tmp/private")
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
