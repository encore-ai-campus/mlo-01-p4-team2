"""페이지별 최대 두 요약과 직렬 추론, 저장 확인 후 집계."""
from concurrent.futures import ThreadPoolExecutor

from .core import Failure, category_mask
from .records import utc_now


class Runner:
    def __init__(self, client, summary, model, records):
        self.client, self.summary, self.model, self.records = client, summary, model, records

    def run(self, *, full=False):
        with self.records.run_lock():
            self.records.recover_interrupted()
            record = self.records.create(full=full)
            try:
                sent_results = False
                with ThreadPoolExecutor(max_workers=2) as pool:
                    for ads in self.client.pages(full=full):
                        record["total"] += len(ads)
                        self.records.write(record)
                        items = []
                        for start in range(0, len(ads), 2):
                            pair = ads[start:start + 2]
                            futures = {ad.ad_group_id: pool.submit(self.summary, ad.redirect_url)
                                       for ad in pair if ad.input_error is None}
                            for ad in pair:
                                if ad.input_error:
                                    self._failure(record, ad.ad_group_id, "input", ad.input_error)
                                    self.records.write(record)
                                    continue
                                try:
                                    body = futures[ad.ad_group_id].result()
                                except Exception as error:
                                    self._failure(record, ad.ad_group_id, "summary", error.code if isinstance(error, Failure) else "SUMMARY_FAILED")
                                    self.records.write(record)
                                    continue
                                try:
                                    category = category_mask(self.model.predict(ad.title, body))
                                except Exception as error:
                                    self._failure(record, ad.ad_group_id, "model", error.code if isinstance(error, Failure) else "LAYA_INFERENCE_FAILED")
                                    self.records.write(record)
                                    continue
                                items.append({"ad_group_id": ad.ad_group_id, "category": category})
                        if items:
                            sent_results = True
                            self._write(record, items)
                    if not sent_results:
                        self._write(record, [])
                record["status"] = "completed_with_failures" if record["failed"] else "completed"
                record["counts_final"] = record["processed"] == record["total"] and not record["pending_write_ids"]
            except Failure as error:
                if error.code == "JOB_RECORD_WRITE_FAILED":
                    raise
                record.update(status="failed", reason=error.code, counts_final=False)
                if error.request_id:
                    record["request_id"] = error.request_id
                if error.code in {"ADS_AUTH_401", "ADS_AUTH_403"}:
                    record["pending_write_ids"] = []
            except KeyboardInterrupt:
                record.update(status="interrupted", reason="CANCELLED", counts_final=False)
            except Exception:
                record.update(status="failed", reason="RUN_FAILED", counts_final=False)
            record["finished_at"] = utc_now()
            self.records.write(record)
            return record

    @staticmethod
    def _failure(record, ad_id, stage, reason, request_id=None):
        failure = {"ad_group_id": ad_id, "stage": stage, "reason": reason}
        if request_id:
            failure["request_id"] = request_id
        record["failures"].append(failure)
        record["failed"] += 1
        record["processed"] += 1

    def _write(self, record, items):
        for event in self.client.write_categories(items):
            if event["kind"] == "pending":
                record["pending_write_ids"] = event["ids"]
            elif event["kind"] == "succeeded":
                for item in event["items"]:
                    record["succeeded"] += 1
                    record["processed"] += 1
                    record["write_results"][item["result"]] += 1
                record["pending_write_ids"] = []
            else:
                for ad_id in event["ids"]:
                    self._failure(record, ad_id, "write", event["reason"], event["request_id"])
                rejected = set(event["ids"])
                record["pending_write_ids"] = [ad_id for ad_id in record["pending_write_ids"] if ad_id not in rejected]
            self.records.write(record)
