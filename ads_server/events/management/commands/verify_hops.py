from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from events import hopcheck, ndjson
from foundation.api import latest_audit_detail, record_audit

SCHEMA_VERSION = 1
HOP_FILES = ("hop_source", "hop_outbox", "hop_mongo")


class Command(BaseCommand):
    help = (
        "구간별 정합 manifest(export_hop_manifests 가 낸 폴더)를 대조한다: 원본→outbox, outbox→MongoDB, 원본→MongoDB. 각 manifest 의 SHA-256·행 수와 세대 사슬을 검증하고, "
        "최신 manifest 가 감사 로그의 고정점과 같은지 본다. --pipeline 으로 파이프라인 쪽이 같은 규칙(hop_rules.py)으로 낸 manifest 를 outbox 구간과(로컬 병행 기간에는 MongoDB 구간과도) 대조한다. "
        "확정 불일치·사슬 오류·고정점 불일치가 있으면 비정상 종료한다(중계 지연 후보는 보고만 하고 다시 확인한다). "
        "--pipeline 이면 창마다의 판정(확정·미확정)을 감사 로그 events.hop_windows_confirmed 에 남긴다 — outbox 정리는 창마다 마지막 판정이 확정일 때만 한다."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dir", required=True, help="export_hop_manifests 의 출력 폴더")
        parser.add_argument("--out", help="대조 결과(NDJSON + manifest)를 낼 폴더")
        parser.add_argument("--pipeline", help="파이프라인 쪽 구간 manifest 경로(outbox 구간과, 로컬 병행 기간에는 MongoDB 구간과도 대조)")
        parser.add_argument("--no-anchor", action="store_true", help="감사 로그의 고정점과 대조하지 않는다(다른 환경에서 받은 폴더를 볼 때)")

    def handle(self, *args, **options):
        directory = Path(options["dir"])
        errors, hops = [], {}
        for name in HOP_FILES:
            path = ndjson.manifest_path(directory, name)
            try:
                hops[name] = hopcheck.load_hop(path, name, expected_kind=name)
            except hopcheck.HopInputError as error:
                errors.append(f"{name}: {error}")
                continue
            errors += [f"{name}: 세대 사슬 — {p}" for p in ndjson.verify_chain(path)]
            if hops[name].unresolved_charges:
                examples = ", ".join(hops[name].manifest["checkpoint"].get("unresolved_charge_examples", []))
                errors.append(f"{name}: 창에 놓을 수 없는 과금 원장 줄 {hops[name].unresolved_charges}건(반응이 없거나 판정 전, 예: {examples}) — 줄 대조에 나오지 않는다")
        if not options["no_anchor"] and not errors:
            anchors = (latest_audit_detail("events.hop_manifests_exported", out=str(directory.resolve())) or {}).get("anchors")  # 폴더마다의 고정점
            if anchors is None:
                errors.append("감사 로그에 이 폴더의 고정점이 없다(이 폴더로 export_hop_manifests 를 실행한 적이 없거나 다른 환경의 폴더다 — --no-anchor)")
            else:
                for name in HOP_FILES:
                    if anchors.get(name) != ndjson.manifest_sha256(ndjson.manifest_path(directory, name)):
                        errors.append(f"{name}: 최신 manifest 가 감사 로그의 고정점과 다르다(폴더가 마지막 실행 뒤에 바뀌었다)")
        rows = []
        if len(hops) == len(HOP_FILES):
            source, outbox, mongo = (hops[n] for n in HOP_FILES)
            try:
                rows += hopcheck.compare(source, outbox) + hopcheck.compare(outbox, mongo) + hopcheck.compare(source, mongo, unsent_from=outbox)
            except hopcheck.HopInputError as error:
                errors.append(str(error))
        outbox_errors, pipeline_error = len(errors), None  # 여기까지는 광고 서버 쪽(원본·outbox·MongoDB) 입력 오류
        if options["pipeline"]:
            try:
                pipeline = hopcheck.load_hop(options["pipeline"], "pipeline", expected_kind="hop_pipeline")
            except hopcheck.HopInputError as error:
                errors.append(f"pipeline: {error}")
                pipeline_error = str(error)
            else:
                # outbox → 파이프라인이 본 쌍(DRD §5.6, 설계 01 D11): CDC 는 sent_at 을 채우지 않으므로 미전송을 지연 근거로 쓰지 않는다.
                # MongoDB → 파이프라인도 지연 후보가 없다(outbox 미전송은 MongoDB 에도 없다). 파이프라인 쪽 입력 실패는 두 쌍에 같으므로 한 번만 적는다
                for upstream in ("hop_outbox", "hop_mongo"):
                    if upstream in hops:
                        try:
                            rows += hopcheck.compare(hops[upstream], pipeline, use_unsent=False)
                        except hopcheck.HopInputError as error:
                            errors.append(f"pipeline: {error}")
                            pipeline_error = str(error)
                            break
        counts = dict(Counter(row["status"] for row in rows))
        for line in errors:
            self.stdout.write(f"FAIL {line}")
        for row in rows:
            if row["status"] in (hopcheck.MISMATCH, hopcheck.LAG):
                self.stdout.write(f"{'FAIL' if row['status'] == hopcheck.MISMATCH else 'LAG '} {row['pair']} {row['window_start']} {row['event_name']} {row['fields']}")
        self.stdout.write(f"결과: {counts or '비교한 줄 없음'}")
        if options["pipeline"]:
            self.stdout.write(self.record_verdicts(options, directory, hops, rows, outbox_errors, pipeline_error))
        if options["out"]:
            checkpoint = {"counts": counts, "errors": errors, "inputs": {n: h.manifest["sha256"] for n, h in hops.items()}}
            manifest = ndjson.write(Path(options["out"]), "hop_report", rows, "hop_report", SCHEMA_VERSION, checkpoint=checkpoint, now=datetime.now(timezone.utc))
            self.stdout.write(f"hop_report: rows={manifest['rows']} file={manifest['file']}")
        if errors or counts.get(hopcheck.MISMATCH):
            raise CommandError(f"구간 대조 실패 — 오류 {len(errors)}건, 확정 불일치 {counts.get(hopcheck.MISMATCH, 0)}건")

    def record_verdicts(self, options, directory, hops, rows, outbox_errors, pipeline_error):
        """창 확정 판정을 감사 로그에 남긴다(설계 01 D8 구현 규칙 1·S3 (가)·D17). 이 DB 의 outbox 와 이어진 판정만 정리 근거가 된다 —
        outbox 쪽이 검증되지 않았으면(--no-anchor, 고정점·사슬·입력 오류) 남기지 않는다(그 오류는 위에서 FAIL 로 나오고 명령이 실패한다).
        파이프라인 쪽을 판정할 수 없으면 outbox 범위의 모든 창을 미확정으로 남긴다 — 확정 전에는 지우지 않는다(D8)."""
        if options["no_anchor"]:
            return "판정 기록: 남기지 않음 — --no-anchor(이 DB 의 내보내기와 이어지지 않는 폴더)"
        if outbox_errors:
            return f"판정 기록: 남기지 않음 — 광고 서버 쪽 입력 오류 {outbox_errors}건(위 FAIL)"
        outbox, with_mongo = hops["hop_outbox"], settings.EVENTS_HOP_CONFIRM_WITH_MONGO
        if pipeline_error:
            confirmed, unconfirmed = [], hopcheck.outbox_windows(outbox)
        else:
            confirmed, unconfirmed = hopcheck.judge_windows(rows, with_mongo)
        if not confirmed and not unconfirmed:
            return "판정 기록: 판정한 창 없음"
        # 판정에 쓴 manifest(포인터 파일)의 SHA-256 — 감사 고정점·세대 사슬과 같은 값
        inputs = {name: ndjson.manifest_sha256(ndjson.manifest_path(directory, name)) for name in ("hop_outbox", *(("hop_mongo",) if with_mongo else ()))}
        if not pipeline_error:
            inputs["pipeline"] = ndjson.manifest_sha256(options["pipeline"])
        detail = hopcheck.verdict_detail(outbox.window, outbox.since, confirmed, unconfirmed, inputs, with_mongo,
                                         reason=f"파이프라인 쪽을 판정할 수 없다: {pipeline_error}" if pipeline_error else None)
        if record_audit(None, hopcheck.CONFIRM_ACTION, detail=detail).detail.get("truncated"):  # 크기 한도를 넘어 잘렸다 — 판정이 사라지지 않게 전 범위를 미확정 한 구간으로(입력 해시 없이 — 늘 들어가게)
            record_audit(None, hopcheck.CONFIRM_ACTION, detail=hopcheck.verdict_detail(
                outbox.window, outbox.since, [], hopcheck.span(confirmed + unconfirmed, outbox.window), {}, with_mongo,
                reason="판정 기록이 감사 detail 크기 한도를 넘었다 — 전 범위를 미확정으로"))
            raise CommandError("판정 기록이 감사 detail 크기 한도를 넘어 전 범위를 미확정으로 남겼다")
        return f"판정 기록: 확정 {len(confirmed)}창 · 미확정 {len(unconfirmed)}창(창 {outbox.window}{', MongoDB 쌍 포함' if with_mongo else ''})"
