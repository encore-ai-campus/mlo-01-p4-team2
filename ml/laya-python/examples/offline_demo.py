"""모의 광고 HTTP·모의 OpenAI 응답과 실제 Laya를 연결한다."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from laya_python.client import AdsClient
from laya_python.http_client import Response, json_bytes
from laya_python.model import LayaModel
from laya_python.records import JobStore, utc_now
from laya_python.runner import Runner
from laya_python.summary import read_summary

ROOT = Path(__file__).resolve().parent.parent
IDS = [f"00000000-0000-4000-8000-{number:012d}" for number in range(1, 5)]
PAGES = {
    "rag": ("RAG와 AI 에이전트 실습", "LLM, 임베딩, RAG와 도구 호출 에이전트를 구현하는 강의입니다."),
    "fail": ("읽을 수 없는 광고", None),
    "python": ("Python API 개발", "Python과 FastAPI로 HTTP API를 개발하고 Git과 테스트를 실습하는 강의입니다."),
    "deleted": ("데이터 파이프라인", "Kafka와 Spark로 수집, 정제, ETL을 구현하는 데이터 엔지니어링 강의입니다."),
}


class MockSummary:
    def __call__(self, url):
        body = PAGES[url.rsplit("/", 1)[-1]][1]
        reply = {
            "status": "completed", "output": [
                {"type": "web_search_call", "status": "completed", "action": {"type": "open_page", "url": url}},
                {"type": "message", "content": [{"type": "output_text", "text": json.dumps({"accessible": body is not None, "body": body or ""})}]},
            ],
        }
        return read_summary(Response(200, {}, json_bytes(reply)), url)


def run_demo(*, model_dir=ROOT / "models/laya", jobs_dir=ROOT / "var/jobs"):
    model = LayaModel(model_dir)
    rows = [
        {"ad_group_id": ad_id, "name": "모의 광고", "updated_at": utc_now(), "profile": None,
         "creatives": [{"creative_id": ad_id, "title": title, "redirect_url": f"https://offline.example.test/{slug}",
                        "image_url": "https://offline.example.test/image.png", "updated_at": utc_now()}]}
        for ad_id, (slug, (title, _)) in zip(IDS, PAGES.items())
    ]
    profiles, requests = {}, []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, body):
            data = json_bytes(body)
            self.send_response(status)
            self.send_header("Content-Type", "application/json" if status == 200 else "application/problem+json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Request-ID", str(uuid4()))
            self.end_headers()
            self.wfile.write(data)

        def authenticated(self):
            if self.headers.get("X-Internal-Key") == "offline-demo-key":
                return True
            self.reply(401, {"code": "unauthorized"})
            return False

        def do_GET(self):
            if not self.authenticated():
                return
            path = urlsplit(self.path)
            if path.path != "/api/internal/v1/ad-groups":
                self.reply(404, {})
                return
            query = parse_qs(path.query)
            after = query.get("after", [""])[0]
            remaining = [dict(row, profile=profiles.get(row["ad_group_id"])) for row in rows
                         if row["ad_group_id"] > after
                         and (query.get("state") != ["unclassified"] or row["ad_group_id"] not in profiles)]
            page = remaining[:2]
            requests.append({"method": "GET", "count": len(page)})
            self.reply(200, {"items": page, "next_after": page[-1]["ad_group_id"] if len(remaining) > 2 else None})

        def do_POST(self):
            if not self.authenticated():
                return
            if self.path != "/api/internal/v1/profiles/categories":
                self.reply(404, {})
                return
            items = json.loads(self.rfile.read(int(self.headers["Content-Length"])))["items"]
            requests.append({"method": "POST", "count": len(items)})
            # 조회 후 광고가 삭제된 경우를 모의한다.
            errors = [{"pointer": f"/items/{index}/ad_group_id", "message": "모의 삭제 광고"}
                      for index, item in enumerate(items) if item["ad_group_id"] == IDS[-1]]
            if errors:
                self.reply(400, {"code": "invalid_request", "errors": errors})
                return
            result = []
            for item in items:
                ad_id = item["ad_group_id"]
                before = profiles.get(ad_id)
                unchanged = before is not None and before["category"] == item["category"]
                profile = before if unchanged else {
                    "ad_group_id": ad_id, "version": before["version"] + 1 if before else 1, "category": item["category"],
                    "weight_mlo": 0, "weight_aio": 0, "weight_skn": 0, "changed_at": utc_now(),
                }
                profiles[ad_id] = profile
                result.append({"ad_group_id": ad_id, "result": "unchanged" if unchanged else "changed" if before else "created", "profile": profile})
            self.reply(200, {"items": result})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = AdsClient(f"http://127.0.0.1:{server.server_port}/api/internal/v1", "offline-demo-key")
        result = Runner(client, MockSummary(), model, JobStore(jobs_dir)).run()
        return {"source": "laya", "mode": "offline_demo", "advertiser_api": "mock_loopback", "summary_api": "mock",
                "classifier": "actual_laya", "device": str(model.classifier.device),
                "input_lengths": model.last_input_lengths, "http_requests": requests, "result": result}
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    print(json.dumps(run_demo(), ensure_ascii=False), flush=True)
