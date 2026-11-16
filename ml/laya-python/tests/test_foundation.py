import json
import os
import tempfile
import threading
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from laya_python.core import CATEGORIES, Failure, category_mask, require_uuid, validate_url
from laya_python.http_client import Response, json_bytes, request_json
from laya_python.settings import Settings, load_env, validate_ads_base

REQUEST_ID = "11111111-1111-4111-8111-111111111111"


@contextmanager
def local_server():
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            calls.append(self.path)
            status = {"/error": 400, "/redirect": 302}.get(self.path, 200)
            self.send_response(status)
            self.send_header("X-Request-ID", REQUEST_ID)
            self.send_header("Location", "/destination")
            self.end_headers()
            self.wfile.write(b'{"message":"received"}')

        def do_POST(self):
            calls.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.do_GET()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class ContractTests(unittest.TestCase):
    def test_bits_follow_names_and_include_threshold(self):
        scores = {name: 0.0 for name in reversed(CATEGORIES)}
        scores["LLM & RAG"] = 0.5
        scores["AI Agents"] = 0.7
        self.assertEqual(category_mask(scores), 12)
        self.assertEqual(category_mask(dict.fromkeys(CATEGORIES, 0.0)), 0)
        self.assertEqual(category_mask(dict.fromkeys(CATEGORIES, 1.0)), 255)

    def test_invalid_scores_are_not_other(self):
        for value in (True, None, "0.6", float("nan"), float("inf"), -0.1, 1.1):
            with self.subTest(value=value), self.assertRaises(Failure):
                category_mask({**dict.fromkeys(CATEGORIES, 0.0), CATEGORIES[0]: value})
        with self.assertRaises(Failure):
            category_mask({})

    def test_huge_integer_scores_fail_with_safe_code(self):
        for value in (10 ** 1000, -(10 ** 1000)):
            with self.subTest(positive=value > 0), self.assertRaises(Failure) as caught:
                category_mask({**dict.fromkeys(CATEGORIES, 0.0), CATEGORIES[0]: value})
            self.assertEqual(caught.exception.code, "INVALID_CATEGORY_SCORES")

    def test_uuid_format_is_exact(self):
        self.assertEqual(require_uuid(REQUEST_ID), REQUEST_ID)
        for value in (None, 1, REQUEST_ID.replace("-", ""), REQUEST_ID + "\n"):
            with self.subTest(value=value), self.assertRaises(Failure):
                require_uuid(value)

    def test_url_rejects_credentials_and_controls(self):
        self.assertEqual(validate_url("https://example.com/a?q=1"), "https://example.com/a?q=1")
        for value in (None, "file:///tmp/a", "https://user:secret@example.com", "http://example.com\n", "http://example.com:bad"):
            with self.subTest(value=value), self.assertRaises(Failure):
                validate_url(value)

    def test_local_ads_base_rejects_remote_dns_and_query(self):
        for host in ("127.0.0.1", "[::1]"):
            self.assertEqual(validate_ads_base(f"http://{host}:8765/api/internal/v1/"), f"http://{host}:8765/api/internal/v1")
        for value in ("http://192.168.0.204/api/internal/v1", "http://localhost/api/internal/v1", "http://127.1/api/internal/v1",
                      "http://127.0.0.1/other", "http://127.0.0.1/api/internal/v1?host=other"):
            with self.subTest(value=value), self.assertRaises(Failure):
                validate_ads_base(value)

    def test_json_rejects_nonfinite_and_preserves_int(self):
        self.assertEqual(json_bytes({"category": 12}), b'{"category":12}')
        with self.assertRaises(Failure):
            json_bytes({"category": float("nan")})


class SettingsTests(unittest.TestCase):
    def test_env_keeps_existing_values_and_quoted_values(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"ADS_INTERNAL_KEY": "existing"}, clear=True):
            root = Path(directory)
            (root / ".env").write_text("# test\nADS_INTERNAL_KEY=replace\nOPENAI_API_KEY=''\nLAYA_MODEL_DIR=./models/laya\n")
            settings = Settings.read(root)
            self.assertEqual(settings.internal_key, "existing")
            self.assertEqual(settings.openai_key, "")
            self.assertEqual(settings.model_dir, (root / "models/laya").resolve())
            self.assertNotIn("existing", repr(settings))
            settings.preflight(require_openai=False)
            with self.assertRaisesRegex(Failure, "OPENAI_API_KEY_MISSING"):
                settings.preflight()

    def test_invalid_env_line_hides_value(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("wrong secret-value\n")
            with self.assertRaises(Failure) as caught:
                load_env(path)
            self.assertEqual(str(caught.exception), "INVALID_ENV_LINE_1")

    def test_missing_internal_key_fails_in_mock_mode_too(self):
        settings = Settings("http://127.0.0.1/api/internal/v1", "", "", Path("model"), Path("jobs"))
        with self.assertRaisesRegex(Failure, "ADS_INTERNAL_KEY_MISSING"):
            settings.preflight(require_openai=False)

    def test_untrusted_request_id_is_not_logged(self):
        response = Response(400, {"x-request-id": "not-a-uuid-secret"}, b"secret")
        self.assertIsNone(response.request_id)
        self.assertNotIn("secret", repr(response))
        self.assertIsNone(Failure("BAD", request_id="secret").request_id)


class HTTPTests(unittest.TestCase):
    def test_error_body_and_request_id_are_retained(self):
        with local_server() as (base, _):
            response = request_json("GET", base + "/error")
        self.assertEqual(response.status, 400)
        self.assertEqual(response.request_id, REQUEST_ID)
        self.assertEqual(json.loads(response.body), {"message": "received"})

    def test_redirect_is_not_followed(self):
        with local_server() as (base, calls):
            response = request_json("GET", base + "/redirect", headers={"X-Internal-Key": "test-only"})
        self.assertEqual(response.status, 302)
        self.assertEqual(calls, ["/redirect"])

    def test_proxy_environment_is_ignored(self):
        proxies = {name: "http://192.0.2.1:9" for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")}
        proxies.update(NO_PROXY="", no_proxy="")
        with local_server() as (base, calls), patch.dict(os.environ, proxies):
            response = request_json("POST", base + "/ok", {"items": []})
        self.assertEqual(response.status, 200)
        self.assertEqual(calls, [{"items": []}, "/ok"])

    def test_transport_error_hides_url_and_key(self):
        with patch("laya_python.http_client.build_opener") as opener:
            opener.return_value.open.side_effect = OSError("secret URL and key")
            with self.assertRaises(Failure) as caught:
                request_json("GET", "http://127.0.0.1/no-secret", headers={"X-Internal-Key": "secret"})
        self.assertEqual(str(caught.exception), "HTTP_TRANSPORT_ERROR")


if __name__ == "__main__":
    unittest.main()
