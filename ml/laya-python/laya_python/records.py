"""JSON 기록과 로컬 실행 잠금. 실행 중 작업은 자동 재개하지 않는다."""
import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .core import Failure, require_uuid
from .http_client import json_bytes


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


class JobStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self._locked = False

    @contextmanager
    def run_lock(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        with (self.directory / ".run.lock").open("ab") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self._locked = True
            try:
                yield
            finally:
                self._locked = False
                fcntl.flock(lock, fcntl.LOCK_UN)

    def recover_interrupted(self):
        if not self._locked:
            raise Failure("JOB_LOCK_REQUIRED")
        for path in self.directory.glob("*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                if record["job_id"] != path.stem or record["source"] != "laya":
                    raise ValueError
                if record["status"] == "running":
                    record.update(status="interrupted", reason="PROCESS_RESTARTED", counts_final=False, finished_at=utc_now())
                    self.write(record)
            except (OSError, ValueError, TypeError, KeyError):
                raise Failure("JOB_RECORD_INVALID") from None

    def create(self, *, full=False):
        if not self._locked:
            raise Failure("JOB_LOCK_REQUIRED")
        record = {
            "source": "laya", "job_id": str(uuid4()), "status": "running", "mode": "full" if full else "unclassified",
            "started_at": utc_now(), "total": 0, "processed": 0, "succeeded": 0, "failed": 0,
            "counts_final": False, "failures": [], "pending_write_ids": [],
            "write_results": {"created": 0, "changed": 0, "unchanged": 0},
        }
        self.write(record)
        return record

    def write(self, record):
        temporary = None
        try:
            require_uuid(record["job_id"])
            record["source"] = "laya"
            record["updated_at"] = utc_now()
            data = json_bytes(record)
            self.directory.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.directory, prefix=".job-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.directory / (record["job_id"] + ".json"))
        except (OSError, ValueError, TypeError, KeyError, Failure):
            raise Failure("JOB_RECORD_WRITE_FAILED") from None
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
