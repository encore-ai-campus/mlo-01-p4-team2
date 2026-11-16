"""한 번 실행하거나 KST 22시 실행을 기다린다."""
import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .client import AdsClient
from .core import Failure
from .model import LayaModel
from .records import JobStore
from .runner import Runner
from .settings import Settings
from .summary import OpenAISummary

KST = ZoneInfo("Asia/Seoul")


def emit(value):
    print(json.dumps(value, ensure_ascii=False, allow_nan=False), flush=True)


def next_run(now):
    local = now.astimezone(KST)
    target = local.replace(hour=22, minute=0, second=0, microsecond=0)
    return target + timedelta(days=1) if target < local else target


def schedule(runner, *, now=lambda: datetime.now(timezone.utc), sleep=time.sleep, output=emit):
    while True:
        target = next_run(now())
        output({"source": "laya", "status": "scheduled", "next_run_at": target.isoformat()})
        while (remaining := (target - now()).total_seconds()) > 0:
            sleep(min(30, remaining))
        result = runner.run()
        output(result)
        if result["status"] == "interrupted":
            return 130


def main(argv=None):
    parser = argparse.ArgumentParser(description="Laya 광고 카테고리 분류 클라이언트")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="미분류 광고를 한 번 처리")
    run.add_argument("--full", action="store_true", help="분류한 광고도 다시 처리")
    commands.add_parser("schedule", help="매일 KST 22시에 실행. OS 예약은 등록하지 않음")
    args = parser.parse_args(argv)
    try:
        settings = Settings.read()
        settings.preflight()
        summary = OpenAISummary(settings.openai_key)
        model = LayaModel(settings.model_dir)
        runner = Runner(AdsClient(settings.ads_base_url, settings.internal_key), summary, model, JobStore(settings.jobs_dir))
        if args.command == "schedule":
            return schedule(runner)
        result = runner.run(full=args.full)
        emit(result)
        return {"completed": 0, "completed_with_failures": 1, "interrupted": 130}.get(result["status"], 2)
    except Failure as error:
        emit({"source": "laya", "status": "failed", "reason": error.code, "counts_final": False})
        return 2
    except KeyboardInterrupt:
        emit({"source": "laya", "status": "interrupted", "reason": "CANCELLED", "counts_final": False})
        return 130
    except Exception:
        emit({"source": "laya", "status": "failed", "reason": "STARTUP_FAILED", "counts_final": False})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
