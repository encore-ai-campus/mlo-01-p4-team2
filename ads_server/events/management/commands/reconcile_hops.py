import fcntl
import io
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from events import hops, ndjson

SPANS = {"hour": timedelta(hours=48), "day": timedelta(days=8)}  # 파이프라인 잡(pipeline/bronze/hop_manifest_job.py SPANS)과 같게 — 기대 since 계산
RC = {"ok": 0, "mismatch": 1, "input_failed": 2, "late": 3, "skipped": 75}
VERDICT = re.compile(r"판정 기록: 확정 (\d+)창 · 미확정 (\d+)창")


class Command(BaseCommand):
    help = (
        "창 대사 한 번(파이프라인 계획 P2-2, 설계 01 D14): 파이프라인 manifest 받기(fetch_hop_manifest --name hop_pipeline_<창>) → 받은 checkpoint.since 로 "
        "outbox manifest(export_hop_manifests) → 대조·확정 기록(verify_hops --pipeline). 결과를 상태 파일 <dir>/reconcile-<창>.json 에 남기고 종료 코드로 알린다: "
        "0 정상, 1 확정 불일치(mismatch), 2 입력 실패(받기·내보내기·입력 검증·--as-of 형식), 3 늦음(받은 since 가 기대와 다르다 — 대개 기대보다 이르다(게시가 늦음), 대조는 했다), 75 같은 창 크기 대사가 돌고 있어 건너뜀. "
        "상태 파일을 쓰기 전에 멈추면(설정 오류·작업 폴더 오류 등) Python·Django 기본값 1 이다 — 확정 불일치와는 상태 파일로 구별한다. "
        "기대 since = 그 창 크기의 지금 경계 − 48시간(hour)·8일(day). outbox 의 창 닫힘 기준 시각(--as-of)은 받은 manifest 의 checkpoint.started_at(파이프라인 잡이 닫힘에 쓴 시각)이다 — "
        "since 와 같이 두 쪽이 같은 범위·같은 닫힘을 본다(잡이 늦은 회차에 그 뒤 닫힌 창이 한쪽에만 나와 거짓 불일치가 되지 않게). "
        "다만 이쪽 지금보다 늦으면 이쪽 지금으로 닫는다(잡 쪽 시계가 앞서도 이쪽 기준으로 아직 여유 안인 창을 확정하지 않게 — 그 창은 한쪽 열림으로 다음 회차에 판정한다)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--window", choices=sorted(SPANS), required=True, help="창 크기: hour(매시) 또는 day(매일)")
        parser.add_argument("--dir", required=True, help="작업 폴더 — <창>/pipeline(받은 manifest)·<창>/outbox·<창>/report 와 상태 파일 reconcile-<창>.json")
        parser.add_argument("--as-of", help="재현용 기준 시각(시간대를 붙인 ISO 8601) — 기대 since 에 쓴다(기본은 지금). outbox 창 닫힘은 받은 manifest 의 started_at 과 이것 중 이른 쪽이다")

    def handle(self, *args, **options):
        window, base = options["window"], Path(options["dir"])
        base.mkdir(parents=True, exist_ok=True)
        with open(base / f".reconcile-{window}.lock", "a+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:  # 앞 회차가 아직 돈다 — 상태 파일은 그 회차의 것으로 둔다
                self.stdout.write(f"skipped: 같은 창 크기({window}) 대사가 돌고 있다")
                sys.exit(RC["skipped"])
            status = self.reconcile(window, base, options["as_of"])
            ndjson.publish_bytes(base / f"reconcile-{window}.json", (json.dumps(status, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))  # 잠금 안 — 상태 파일 = 잠금을 잡은 회차
        self.stdout.write(f"reconcile {window}: {status['status']} rc={status['rc']} since={status['pipeline_since']} 기대={status['expected_since']}"
                          f" 확정={status['confirmed']} 미확정={status['unconfirmed']} 불일치={status['mismatch_lines']} 예외={status['exception_lines']}"
                          + (f" 단계={status['stage']}: {status['message']}" if status["stage"] else ""))
        if status["rc"]:
            sys.exit(status["rc"])

    def reconcile(self, window, base, as_of):
        try:
            now = datetime.fromisoformat(as_of) if as_of else datetime.now(timezone.utc)
        except ValueError:
            raise CommandError("--as-of 는 ISO 8601 시각이어야 합니다.", returncode=2)  # 입력 실패 — 기본값 1(확정 불일치)로 보이지 않게
        if now.tzinfo is None:
            raise CommandError("--as-of 에는 시간대를 붙여야 합니다.", returncode=2)
        now = now.astimezone(timezone.utc)
        size, name, folder = hops.WINDOWS[window], f"hop_pipeline_{window}", base / window
        status = {"window": window, "started_at": datetime.now(timezone.utc).isoformat(), "as_of": now.isoformat(), "finished_at": None, "status": None, "rc": None,
                  "stage": None, "message": None, "expected_since": hops.format_time(hops.window_start(now, size) - SPANS[window]), "pipeline_since": None, "pipeline_as_of": None,
                  "late_by_seconds": None, "pipeline_manifest_sha256": None, "outbox_manifest_sha256": None, "confirmed": None, "unconfirmed": None,
                  "mismatch_lines": None, "exception_lines": None}

        def done(state, stage=None, message=None):
            if isinstance(message, Exception) and not isinstance(message, CommandError):
                message = f"{type(message).__name__}: {message}"  # DB 연결 끊김·읽기 전용(25006) 등 — 다음 회차에 저절로 낫는 입력 실패다(불일치가 아니다)
            status.update(status=state, rc=RC[state], stage=stage, message=None if message is None else " ".join(str(message).split())[:300],
                          finished_at=datetime.now(timezone.utc).isoformat())
            return status

        def run(command, *arguments):  # (출력, 실패) — 실패해도 그때까지의 출력(판정 기록 줄 등)을 버리지 않는다
            out = io.StringIO()
            try:
                call_command(command, *arguments, stdout=out)
                return out.getvalue(), None
            except CommandError as error:
                return out.getvalue(), error

        stage = ["fetch"]  # CommandError 밖의 예외(DB 연결 끊김·25006, manifest 읽기 등)도 그 단계의 입력 실패로 상태 파일에 남긴다(KeyboardInterrupt·SystemExit 는 잡지 않음)
        try:
            return self.steps(window, name, folder, now, status, done, run, stage)
        except Exception as error:  # noqa: BLE001
            return done("input_failed", stage[0], error)

    def steps(self, window, name, folder, now, status, done, run, stage):
        pointer = ndjson.manifest_path(folder / "pipeline", name)
        _, error = run("fetch_hop_manifest", "--out", str(folder / "pipeline"), "--name", name)
        if error:
            return done("input_failed", "fetch", error)
        stage[0] = "since"
        checkpoint = ndjson.read_manifest(pointer).get("checkpoint") or {}
        since, closed_at = checkpoint.get("since"), checkpoint.get("started_at")  # started_at = 잡이 summarize_changes 에 준 now(창 닫힘 기준, P2-1)
        status["pipeline_manifest_sha256"] = ndjson.manifest_sha256(pointer)
        if not isinstance(since, str):
            return done("input_failed", "since", f"받은 manifest 에 checkpoint.since 가 없다: {since!r}")
        status["pipeline_since"] = since
        try:
            cut = datetime.fromisoformat(closed_at)
        except (TypeError, ValueError):
            cut = None
        if cut is None or cut.tzinfo is None:
            return done("input_failed", "since", f"받은 manifest 의 checkpoint.started_at 이 시간대를 붙인 ISO 8601 시각이 아니다: {closed_at!r}")
        status["pipeline_as_of"] = cut.astimezone(timezone.utc).isoformat()
        stage[0] = "export"
        _, error = run("export_hop_manifests", "--out", str(folder / "outbox"), "--window", window, "--since", since, "--as-of", min(cut, now).astimezone(timezone.utc).isoformat())  # 잡 시계가 앞서면 이쪽 지금(상태 파일 pipeline_as_of 는 받은 값 그대로)
        if error:
            return done("input_failed", "export", error)
        status["outbox_manifest_sha256"] = ndjson.manifest_sha256(ndjson.manifest_path(folder / "outbox", "hop_outbox"))
        stage[0] = "verify"
        report_pointer = ndjson.manifest_path(folder / "report", "hop_report")
        previous_report = ndjson.manifest_sha256(report_pointer) if report_pointer.exists() else None
        output, failure = run("verify_hops", "--dir", str(folder / "outbox"), "--pipeline", str(pointer), "--out", str(folder / "report"))
        if not report_pointer.exists() or ndjson.manifest_sha256(report_pointer) == previous_report:  # 대조 보고가 새로 나오지 않았다(그 전에 멈춤 — 판정 기록 한도 초과 등). 지난 회차 보고로 분류하지 않는다
            return done("input_failed", "verify", failure or "대조 보고가 나오지 않았다")
        report = ndjson.read_manifest(report_pointer)
        rows = [json.loads(line) for line in (folder / "report" / report["file"]).read_text(encoding="utf-8").splitlines()]
        status["mismatch_lines"] = sum(1 for row in rows if row["status"] == "mismatch" and not row.get("exception"))
        status["exception_lines"] = sum(1 for row in rows if row.get("exception"))
        if verdict := VERDICT.search(output):
            status["confirmed"], status["unconfirmed"] = int(verdict[1]), int(verdict[2])
        if report["checkpoint"]["errors"]:
            return done("input_failed", "verify", "; ".join(report["checkpoint"]["errors"]))
        if failure is not None:  # 늦음보다 앞선다 — 늦은 회차의 진짜 불일치를 늦음으로 가리지 않는다
            return done("mismatch", "verify", failure)
        lag = (hops.parse_time(status["expected_since"]) - hops.parse_time(since)).total_seconds()
        if lag:
            status["late_by_seconds"] = int(lag)
            return done("late", "since", f"받은 since {since} 가 기대 {status['expected_since']} 와 다르다(파이프라인 게시가 늦거나 아직 없다)")
        return done("ok")
