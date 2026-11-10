"""pipeline/vacuum/control.py — 표준 라이브러리 시험(계획 temp/pipeline/45_vacuum-plan.md r2 §3.7)."""
import unittest

from pipeline.gold import control as gold_control
from pipeline.silver import control as silver_control
from pipeline.vacuum import control

S3 = {"S3_ENDPOINT": "https://s3", "S3_REGION": "r", "S3_ACCESS_KEY_FILE": "/k/a", "S3_SECRET_KEY_FILE": "/k/s"}


class Settings(unittest.TestCase):
    def test_root_is_required(self):
        for environ in ({}, {"VACUUM_LAKE_ROOT": ""}, {"VACUUM_LAKE_ROOT": "  "}):
            with self.subTest(environ=environ), self.assertRaisesRegex(ValueError, "VACUUM_LAKE_ROOT"):
                control.job_settings(environ, "ads")
        for root in (" /lake", "/lake ", "/la`ke", "/"):
            with self.subTest(root=root), self.assertRaisesRegex(ValueError, "VACUUM_LAKE_ROOT"):
                control.job_settings({"VACUUM_LAKE_ROOT": root}, "ads")
        self.assertEqual(control.job_settings({"VACUUM_LAKE_ROOT": "/lake"}, "ads"), ("ads", "/lake", None))

    def test_domain_and_s3(self):
        for domain in ("Ads", "a/b", "", "ads:x"):
            with self.subTest(domain=domain), self.assertRaises(ValueError):
                control.job_settings({"VACUUM_LAKE_ROOT": "/lake"}, domain)
        with self.assertRaisesRegex(ValueError, "S3_ENDPOINT, S3_REGION, S3_ACCESS_KEY_FILE, S3_SECRET_KEY_FILE"):
            control.job_settings({"VACUUM_LAKE_ROOT": "s3a://bucket"}, "ads")
        settings = control.job_settings({"VACUUM_LAKE_ROOT": "s3a://bucket", **S3}, "ads")
        self.assertEqual(settings.s3, {"endpoint": "https://s3", "region": "r", "access_key_file": "/k/a", "secret_key_file": "/k/s"})


class Names(unittest.TestCase):
    def test_generations(self):
        found = control.generations(["g2", "g1", "g12", "g0", "g01", "gx", "g1a", "G1", "events", "_tmp"])
        self.assertEqual(found, ([1, 2, 12], ["G1", "_tmp", "events", "g0", "g01", "g1a", "gx"]))

    def test_table_versions(self):
        self.assertEqual(control.table_versions(["v1", "v3", "v0", "v01", "g1", "v"]), ([1, 3], ["g1", "v", "v0", "v01"]))

    def test_gold_tables(self):
        found = control.gold_tables("/lake", "ads", ["performance_hourly", "corrections", "old_table", "Bad-Name", "1x", "_x", "a-b"])
        self.assertEqual(found, (["old_table", "performance_hourly"], ["1x", "Bad-Name", "_x", "a-b"]))


class Targets(unittest.TestCase):
    def test_paths_come_from_the_rules(self):
        root = "s3a://bucket/"
        self.assertEqual(control.silver_parents(root, "ads"), [f"s3a://bucket/silver/ads/{kind}" for kind in ("events", "errors", "reconcile", "quarantine")])
        self.assertEqual(control.gold_dir(root, "ads"), "s3a://bucket/gold/ads")
        self.assertEqual(control.table_dir(root, "ads", "performance_hourly"), "s3a://bucket/gold/ads/performance_hourly")
        paths = control.targets(root, "ads", [1, 2], {"performance_hourly": [1, 2], "creative_course_daily": [1]})
        expected = [*silver_control.silver_paths(root, "ads", 1)[:4], *silver_control.silver_paths(root, "ads", 2)[:4], silver_control.pointer_path(root, "ads"),
                    gold_control.table_path(root, "ads", "creative_course_daily", 1), gold_control.table_path(root, "ads", "performance_hourly", 1),
                    gold_control.table_path(root, "ads", "performance_hourly", 2), gold_control.corrections_path(root, "ads")]
        self.assertEqual(paths, expected)
        self.assertEqual(len(paths), 13)  # 세대 둘 × 넷 + 포인터 + Gold 판 셋 + 정정
        self.assertFalse([p for p in paths if "checkpoints" in p or "/metaverse" in p or "bronze" in p])
        self.assertTrue(all(p.startswith(("s3a://bucket/silver/ads/", "s3a://bucket/gold/ads/")) for p in paths))

    def test_nothing_found(self):
        self.assertEqual(control.targets("/lake", "ads", [], {}), ["/lake/silver/ads/pointer", "/lake/gold/ads/corrections"])


if __name__ == "__main__":
    unittest.main()
