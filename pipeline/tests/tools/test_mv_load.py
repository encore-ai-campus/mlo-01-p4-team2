"""메타버스 더미 도구(mv_load·mv_simclock)의 순수 시험 — 앱·DB 없이 돈다(계획 MD-1 §6)."""
import copy
import os
import random
import sys
import tempfile
import unittest
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import mv_load as m  # noqa: E402
import mv_simclock  # noqa: E402

CODES = [f"game-{i}" for i in range(17)]
DAY = date(2026, 11, 1)


def make(n=100, days=3, seed=1, distribution=False):
    return m.plan(n, days, seed, DAY, CODES, distribution=distribution)


def acts(p, user, act):
    return [a for a in p["actions"] if a["user"] == user and a["act"] == act]


class PlanTest(unittest.TestCase):
    def test_same_seed_same_plan(self):
        self.assertEqual(make(seed=7), make(seed=7))
        self.assertNotEqual(make(seed=7)["actions"], make(seed=8)["actions"])

    def test_default_scale_is_strict_1500(self):
        p = make()
        self.assertEqual(sum(len(a["kinds"]) for a in p["actions"]), 1500)
        per_user = {u: Counter(k for a in p["actions"] if a["user"] == u for k in a["kinds"]) for u in range(100)}
        self.assertTrue(all(c == Counter(dict.fromkeys(m.KINDS, 1)) for c in per_user.values()))
        users = p["users"]
        self.assertEqual(sorted(Counter(u["day"] for u in users).values()), [33, 33, 34])
        self.assertEqual(Counter(u["end"] for u in users), {"logout": 80, "expire": 20})
        self.assertEqual(Counter(u["content"] for u in users), {"game": 50, "learning": 50})
        self.assertEqual(Counter(u["cohort"] for u in users), {**dict.fromkeys(m.COHORTS, 15), "unassigned": 10})
        ends = Counter(a["act"] for a in p["actions"] if a["act"] in ("logout", "expire"))
        self.assertEqual(ends, {"logout": 80, "expire": 20})

    def test_rules_hold_over_seeds(self):
        for n, days in ((100, 3), (6, 2)):
            for seed in range(25):
                with self.subTest(n=n, seed=seed):
                    self.assertEqual(m.problems(make(n, days, seed)["actions"], True, m.PLAN), [])

    def test_pairs_form_one_ring_per_day_with_overlapping_sessions(self):
        p = make()
        for day in range(3):
            members = {u["idx"] for u in p["users"] if u["day"] == day}
            partners = {a["user"]: a["partner"] for a in p["actions"] if a["act"] == "read" and a["user"] in members}
            self.assertEqual(set(partners), members)
            self.assertEqual(set(partners.values()), members)  # 모든 글이 한 번 읽힘
            self.assertTrue(all(i != j for i, j in partners.items()))
            # 원을 따라가면 하루 사람을 모두 한 번 돈다(원 하나)
            seen, cur = [], next(iter(members))
            while cur not in seen:
                seen.append(cur)
                cur = partners[cur]
            self.assertEqual(len(seen), len(members))
        for a in p["actions"]:  # 세션은 6시간 이하
            if a["act"] in ("logout", "expire"):
                self.assertLessEqual(a["t"] - acts(p, a["user"], "login")[0]["t"], 6 * 3600)

    def test_each_day_crosses_both_midnights(self):
        p = make()
        for day in range(3):
            utc = kst = 0
            for u in (u["idx"] for u in p["users"] if u["day"] == day):
                start = acts(p, u, "login")[0]["t"]
                end = (acts(p, u, "logout") or acts(p, u, "expire"))[0]["t"]
                utc += datetime.fromtimestamp(start, timezone.utc).date() != datetime.fromtimestamp(end, timezone.utc).date()
                kst += datetime.fromtimestamp(start, m.KST).date() != datetime.fromtimestamp(end, m.KST).date()
            self.assertGreaterEqual(utc, 1, day)
            self.assertGreaterEqual(kst, 1, day)

    def test_small_run_crosses_both_midnights_over_the_run(self):
        p = make(6, 2)
        utc = kst = 0
        for u in range(6):
            start, end = acts(p, u, "login")[0]["t"], (acts(p, u, "logout") or acts(p, u, "expire"))[0]["t"]
            utc += datetime.fromtimestamp(start, timezone.utc).date() != datetime.fromtimestamp(end, timezone.utc).date()
            kst += datetime.fromtimestamp(start, m.KST).date() != datetime.fromtimestamp(end, m.KST).date()
        self.assertGreaterEqual(utc, 1)
        self.assertGreaterEqual(kst, 1)
        self.assertLessEqual(p["end"], datetime(2026, 11, 3, 2, tzinfo=m.KST).timestamp())  # 어제(11-02) 밤 세션은 오늘 새벽에 끝난다

    def test_learning_open_uses_the_quiz_subject_and_type(self):
        p = make()
        for u in p["users"]:
            learn = acts(p, u["idx"], "learn")[0]
            if u["content"] == "learning":
                opened = acts(p, u["idx"], "open_learning")[0]
                self.assertEqual((opened["subject"], opened["qtype"]), (learn["subject"], learn["qtype"]))
            else:
                self.assertIn(acts(p, u["idx"], "open_game")[0]["code"], CODES)

    def test_distribution_mode_adds_wrong_answers_moves_and_sessions(self):
        p = make(distribution=True)
        self.assertEqual(m.problems(p["actions"], False, m.PLAN), [])
        kinds = Counter(k for a in p["actions"] for k in a["kinds"])
        self.assertGreater(kinds["quiz_answered"], kinds["point_granted"])  # 오답이 있다
        self.assertGreater(kinds["movement_observed"], 100)
        self.assertGreater(kinds["session_started"], 100)
        self.assertEqual(kinds["post_created"], 100)  # 글·댓글은 처음 세션에서만

    def test_crossings_reject_configs_where_j0_would_fail(self):
        utc, kst = m.crossings(m.plan_spans(make()["actions"]))
        self.assertGreaterEqual(min(utc, kst), 3)  # 하루마다 두 자정
        self.assertGreaterEqual(min(m.crossings(m.plan_spans(make(6, 2)["actions"]))), 1)
        self.assertEqual(m.crossings(m.plan_spans(make(6, 1)["actions"]))[1], 0)  # 하루 24명 미만·1일이면 한국 자정이 없다 → main 이 시드 전에 거부

    def test_needs_two_users_a_day(self):
        with self.assertRaises(ValueError):
            m.plan(3, 2, 1, DAY, CODES)


class ProblemsTest(unittest.TestCase):
    """규칙 검사가 어긋남을 잡는가(장부 시각에도 같은 검사를 쓴다)."""

    def setUp(self):
        self.p = make(6, 2)

    def first(self, act, user=None):
        return next(a for a in self.p["actions"] if a["act"] == act and (user is None or a["user"] == user))

    def test_transfer_after_30s(self):
        move = self.first("move")
        self.first("transfer", move["user"])["t"] = move["t"] + 31
        self.assertTrue(any("층 전환" in x for x in m.problems(self.p["actions"], True, m.LIVE)))

    def test_post_deleted_before_partner_comment_delete(self):
        reader = self.first("comment_delete")
        self.first("post_delete", reader["partner"])["t"] = reader["t"] - 1
        self.assertTrue(any("글 삭제 순서" in x for x in m.problems(self.p["actions"], True, m.PLAN)))

    def test_long_gap_without_keepalive(self):
        rows = [a for a in self.p["actions"] if a["act"] != "keepalive"]
        self.assertTrue(any("세션 만료 위험" in x for x in m.problems(rows, True, m.PLAN)))

    def test_duplicate_kind(self):
        rows = self.p["actions"] + [dict(self.first("read"), kinds=["post_read"])]
        self.assertTrue(any("종류 수" in x for x in m.problems(rows, True, m.PLAN)))

    def test_expiry_without_enough_idle(self):
        exp = self.first("expire")
        last = max(a["t"] for a in self.p["actions"] if a["user"] == exp["user"] and a["act"] in m.TOUCH)
        exp["t"] = last + 29 * 60
        self.assertTrue(any("쉰 시간" in x for x in m.problems(self.p["actions"], True, m.LIVE)))

    def test_ledger_times_use_t0_t1(self):
        rows = copy.deepcopy(self.p["actions"])
        for r in rows:
            r["t0"], r["t1"] = r["t"], r["t"] + 2
        self.assertEqual(m.problems(rows, True, m.LIVE), [])


class FakeHttp:
    """대본대로 답하는 브라우저. 대본: [(메서드, 경로 앞부분, 상태 또는 None(연결 끊김), 본문)] — 처음 맞는 줄을 쓰고 지운다."""

    def __init__(self, script):
        self.script, self.sent = list(script), []

    def __call__(self, base):
        return self

    def csrf(self):
        return "tok"

    def request(self, method, path, json_body=None, form=None):
        self.sent.append((method, path, json_body))
        for i, (mt, prefix, status, body) in enumerate(self.script):
            if mt == method and path.startswith(prefix):
                del self.script[i]
                if status is None:
                    raise OSError("끊김")
                return status, body, None
        raise AssertionError(f"대본 밖 요청 {method} {path}")

    def count(self, method, prefix):
        return sum(1 for mt, p, _ in self.sent if mt == method and p.startswith(prefix))


class FakeDB:
    def __init__(self, fail=()):
        self.outbox, self.calls, self.fail = Counter(), [], list(fail)  # fail: 셸 호출마다 (예외를 낼지, 그때 커밋됐는지)

    def session_uuid(self, u):
        return "s-new"

    def answer(self, question_uuid, right):
        return ("right" if right else "wrong"), "choice"

    def count(self, u, kind):
        return self.outbox[(u["idx"], kind)]

    def last_ref(self, u, kind, key):
        return "m-committed"

    def game_uuid(self, code):
        return "g-uuid"

    def _shell(self, u, kind, value):
        self.calls.append(kind)
        if self.fail:
            raise_it, committed = self.fail.pop(0)
            if committed:
                self.outbox[(u["idx"], kind)] += 1
            if raise_it:
                raise m.Transient("끊김")
        self.outbox[(u["idx"], kind)] += 1
        return value

    def chat(self, u, session, text):
        return self._shell(u, "chat_sent", "m-new")

    def move(self, u, session, pos):
        return self._shell(u, "movement_observed", True)

    def transfer(self, u, session, pos):
        return self._shell(u, "floor_transferred", True)


POST = {"id": 7, "postUuid": "p-uuid", "updatedAt": "v1", "revision": 1}
COMMENT = {"id": 9, "commentUuid": "c-uuid", "updatedAt": "v1", "revision": 1, "body": "더미 댓글 [mvload:t:001:comment]", "isDeleted": False}


class RetryTest(unittest.TestCase):
    """503·연결 끊김 뒤 동작마다 다시 보내거나, 확인하거나, '확인 필요'로 남기는가(계획 §5 표, C2)."""

    def runner(self, script, db=None, users=None):
        self.http = FakeHttp(script)
        users = users or [{"idx": 0, "nnn": "001", "name": "mvload_t_001", "password": "pw", "pk": 1, "uuid": "u0", "sess": "s0", "client": self.http,
                           "post": dict(id=7, uuid="p-uuid", version="v1", revision=1)},
                          {"idx": 1, "nnn": "002", "name": "mvload_t_002", "password": "pw", "pk": 2, "uuid": "u1", "sess": "s1", "client": self.http,
                           "post": dict(id=8, uuid="p2-uuid", version="v1", revision=1)}]
        ticks = iter(range(10**6))
        return m.Runner(db or FakeDB(), "http://x", "t", users, lambda: float(next(ticks)), 120.0, None, give_up=1e9, http=self.http,
                        sleep=lambda s: None, monotonic=lambda: 0.0)

    def test_answer_resent_with_same_request_uuid(self):
        r = self.runner([("POST", "/api/learning/sessions/", 201, {"session_id": 3}), ("GET", "/api/learning/sessions/3/questions/1/", 200, {"question": {"question_uuid": "q"}}),
                         ("POST", "/api/learning/sessions/3/questions/1/answers/", 503, None),
                         ("POST", "/api/learning/sessions/3/questions/1/answers/", 201, {"is_correct": True, "attempt_uuid": "a1", "points": {"ledger_uuid": "l1"}})])
        events = r.do_learn(r.users[0], {"subject": "sql", "qtype": "fill_blank", "difficulty": "beginner", "questions": [["right"]]})
        self.assertEqual(events, [("quiz_answered", {"attempt_uuid": "a1"}), ("point_granted", {"attempt_uuid": "a1", "ledger_uuid": "l1"})])
        sent = [b for mt, p, b in self.http.sent if p.endswith("answers/")]
        self.assertEqual(len(sent), 2)
        self.assertEqual(sent[0]["submission_request_uuid"], sent[1]["submission_request_uuid"])

    def test_login_checked_by_status(self):
        r = self.runner([("GET", "/accounts/login/", 200, None), ("POST", "/accounts/login/", 503, None),
                         ("GET", "/accounts/session/status/", 200, {"authenticated": True}), ("GET", "/api/learning/csrf/", 200, {})])
        self.assertEqual(r.do_login(r.users[0], {}), [("session_started", {})])
        self.assertEqual(self.http.count("POST", "/accounts/login/"), 1)  # 로그인돼 있으니 다시 보내지 않음
        r = self.runner([("GET", "/accounts/login/", 200, None), ("POST", "/accounts/login/", None, None),
                         ("GET", "/accounts/session/status/", 401, {"authenticated": False}), ("POST", "/accounts/login/", 302, None), ("GET", "/api/learning/csrf/", 200, {})])
        r.do_login(r.users[0], {})
        self.assertEqual(self.http.count("POST", "/accounts/login/"), 2)
        self.assertEqual(r.users[0]["sess"], "s-new")

    def test_post_create_checked_by_marker_search(self):
        r = self.runner([("POST", "/api/community/posts/", 503, None), ("GET", "/api/community/posts/?q=", 200, {"posts": [POST]})])
        self.assertEqual(r.do_post_create(r.users[0], {}), [("post_created", {"post_uuid": "p-uuid"})])
        self.assertEqual(self.http.count("POST", "/api/community/posts/"), 1)
        self.assertIn("%5Bmvload%3At%3A001%3Apost%5D", self.http.sent[1][1])
        r = self.runner([("POST", "/api/community/posts/", 503, None), ("GET", "/api/community/posts/?q=", 200, {"posts": []}),
                         ("POST", "/api/community/posts/", 201, {"post": POST})])
        r.do_post_create(r.users[0], {})
        self.assertEqual(self.http.count("POST", "/api/community/posts/"), 2)

    def test_post_update_checked_by_revision(self):
        r = self.runner([("PATCH", "/api/community/posts/7/", 503, None), ("GET", "/api/community/posts/?q=", 200, {"posts": [dict(POST, revision=2, updatedAt="v2")]})])
        self.assertEqual(r.do_post_update(r.users[0], {}), [("post_updated", {"post_uuid": "p-uuid"})])
        self.assertEqual(self.http.count("PATCH", "/"), 1)
        self.assertEqual(r.users[0]["post"]["version"], "v2")

    def test_deletes_resend_and_accept_404_only_after_retry(self):
        r = self.runner([("DELETE", "/api/community/posts/7/", 503, None), ("DELETE", "/api/community/posts/7/", 404, None)])
        self.assertEqual(r.do_post_delete(r.users[0], {}), [("post_deleted", {"post_uuid": "p-uuid"})])
        r = self.runner([("DELETE", "/api/community/posts/7/", 404, None)])
        with self.assertRaises(m.Fail):
            r.do_post_delete(r.users[0], {})

    def test_comment_create_update_delete(self):
        r = self.runner([("POST", "/api/community/posts/8/comments/", 503, None), ("GET", "/api/community/posts/8/comments/", 200, {"comments": [COMMENT]}),
                         ("PATCH", "/api/community/comments/9/", None, None),
                         ("GET", "/api/community/posts/8/comments/", 200, {"comments": [dict(COMMENT, revision=2, updatedAt="v2")]}),
                         ("DELETE", "/api/community/comments/9/", 503, None), ("DELETE", "/api/community/comments/9/", 404, None)])
        u, a = r.users[0], {"partner": 1}
        self.assertEqual(r.do_comment_create(u, a), [("comment_created", {"comment_uuid": "c-uuid", "post_uuid": "p2-uuid"})])
        self.assertEqual(r.do_comment_update(u, a), [("comment_updated", {"comment_uuid": "c-uuid", "post_uuid": "p2-uuid"})])
        self.assertEqual(r.do_comment_delete(u, a), [("comment_deleted", {"comment_uuid": "c-uuid", "post_uuid": "p2-uuid"})])
        self.assertEqual((self.http.count("POST", "/api/community/posts/8/comments/"), self.http.count("PATCH", "/"), self.http.count("DELETE", "/")), (1, 1, 2))

    def test_read_and_content_open_are_left_unverified(self):
        for act, a, script in (("read", {"partner": 1}, [("GET", "/api/community/posts/8/", 503, None)]),
                               ("open_game", {"code": "suika"}, [("GET", "/content/suika/open/", None, None)]),
                               ("open_learning", {"subject": "sql", "qtype": "fill_blank"}, [("POST", "/api/learning/content-open/", 503, None)])):
            with self.subTest(act):
                r = self.runner(script)
                with self.assertRaises(m.Unverified):
                    getattr(r, "do_" + act)(r.users[0], a)
                self.assertEqual(len(self.http.sent), 1)  # 다시 보내지 않음

    def test_logout_checked_by_status(self):
        r = self.runner([("POST", "/accounts/logout/", 503, None), ("GET", "/accounts/session/status/", 401, {"authenticated": False})])
        self.assertEqual(r.do_logout(r.users[0], {}), [("session_ended", {"end_reason": "LOGOUT"})])
        self.assertEqual(self.http.count("POST", "/accounts/logout/"), 1)
        r = self.runner([("POST", "/accounts/logout/", 503, None), ("GET", "/accounts/session/status/", 200, {"authenticated": True}), ("POST", "/accounts/logout/", 302, None)])
        r.do_logout(r.users[0], {})
        self.assertEqual(self.http.count("POST", "/accounts/logout/"), 2)

    def test_expire_resent_after_503(self):
        r = self.runner([("GET", "/accounts/session/status/", 503, None), ("GET", "/accounts/session/status/", 401, {"reason": "signed_out"})])
        self.assertEqual(r.do_expire(r.users[0], {}), [("session_ended", {"end_reason": "SESSION_EXPIRED"})])
        r = self.runner([("GET", "/accounts/session/status/", 200, {"authenticated": True})])
        with self.assertRaises(m.Fail):  # 아직 만료되지 않음
            r.do_expire(r.users[0], {})

    def test_shell_checks_outbox_before_calling_again(self):
        db = FakeDB(fail=[(True, True)])  # 끊겼지만 커밋됨
        r = self.runner([], db)
        self.assertEqual(r.do_chat(r.users[0], {}), [("chat_sent", {"message_uuid": "m-committed"})])
        self.assertEqual(db.calls, ["chat_sent"])
        db = FakeDB(fail=[(True, False)])  # 되돌려짐
        r = self.runner([], db)
        self.assertEqual(r.do_move(r.users[0], {"pos": {"floor": "campus", "x": 1.0, "y": 1.0, "dir": "up"}}), [("movement_observed", {})])
        self.assertEqual(db.calls, ["movement_observed", "movement_observed"])

    def test_run_records_ledger_unverified_and_skips_after_failure(self):
        lines = []
        ledger = type("L", (), {"write": lambda self, s: lines.append(s), "flush": lambda self: None})()
        r = self.runner([("GET", "/api/community/posts/8/", 503, None), ("PATCH", "/api/community/posts/7/", 409, {}),
                         ("POST", "/accounts/session/activity/", 200, {"authenticated": True})])
        r.ledger = ledger
        r.run([{"t": 0, "user": 0, "session": 0, "act": "read", "partner": 1, "kinds": ["post_read"]},
               {"t": 1, "user": 0, "session": 0, "act": "post_update", "kinds": ["post_updated"]},
               {"t": 2, "user": 0, "session": 0, "act": "keepalive", "kinds": []},
               {"t": 3, "user": 1, "session": 0, "act": "keepalive", "kinds": []}])
        self.assertEqual([x["act"] for x in r.unverified], ["read"])
        self.assertEqual([(x["act"], x["status"]) for x in r.failures], [("post_update", 409)])
        self.assertEqual([x["act"] for x in r.skipped], ["keepalive"])  # 실패한 사용자의 다음 동작
        self.assertEqual(len(lines), 1)  # 성공한 동작만 장부에


class JudgeTest(unittest.TestCase):
    def ledger_and_outbox(self):
        ledger = [{"user_uuid": "u0", "session_uuid": "s0", "t0": 100.0, "t1": 102.0, "events": [["session_started", {}]]},
                  {"user_uuid": "u0", "session_uuid": "s0", "t0": 200.0, "t1": 205.0, "events": [["quiz_answered", {"attempt_uuid": "a1"}], ["point_granted", {"attempt_uuid": "a1", "ledger_uuid": "l1"}]]}]
        outbox = [{"user_uuid": "u0", "event_type": "session_started", "created_at": 101.0, "raw_data": {"session_uuid": "s0"}},
                  {"user_uuid": "u0", "event_type": "quiz_answered", "created_at": 203.0, "raw_data": {"session_uuid": "s0", "attempt_uuid": "a1"}},
                  {"user_uuid": "u0", "event_type": "point_granted", "created_at": 203.5, "raw_data": {"session_uuid": "s0", "attempt_uuid": "a1", "ledger_uuid": "l1"}}]
        return ledger, outbox

    def test_match_outbox(self):
        ledger, outbox = self.ledger_and_outbox()
        self.assertEqual(m.match_outbox(ledger, outbox), [])
        self.assertTrue(m.match_outbox(ledger, outbox + [dict(outbox[0], created_at=101.5)]))  # 중복
        late = [dict(outbox[0], created_at=150.0)] + outbox[1:]
        self.assertTrue(any("밖" in x for x in m.match_outbox(ledger, late)))
        wrong = outbox[:2] + [dict(outbox[2], raw_data={"session_uuid": "s0", "attempt_uuid": "a1", "ledger_uuid": "l2"})]
        self.assertTrue(any("값이 다름" in x for x in m.match_outbox(ledger, wrong)))
        other_session = [dict(outbox[0], raw_data={"session_uuid": "s9"})] + outbox[1:]
        self.assertTrue(m.match_outbox(ledger, other_session))

    def test_judge_j0(self):
        def row(user, kind, ts, **raw):
            return {"user_uuid": user, "event_type": kind, "created_at": ts, "raw_data": {"session_uuid": f"s-{user}", **raw}, "published": False, "payload_null": False}

        start = datetime(2026, 11, 1, 23, 50, tzinfo=timezone.utc).timestamp()  # 한국 08:50 → UTC 자정을 넘김
        night = datetime(2026, 11, 2, 14, 50, tzinfo=timezone.utc).timestamp()  # 한국 23:50 → 한국 자정을 넘김
        rows = []
        for user, t, reason, domain in (("a", start, "LOGOUT", "REST"), ("b", night, "SESSION_EXPIRED", "LEARNING")):
            for kind in m.KINDS:
                extra = {"end_reason": reason} if kind == "session_ended" else {"domain": domain} if kind == "content_open_requested" else {}
                rows.append(row(user, kind, t + (1800 if kind == "session_ended" else 60), **extra))
        j0 = m.judge_j0(rows, 2, 2, True, 30, [], {})
        self.assertTrue(j0["ok"], j0)
        self.assertFalse(m.judge_j0(rows + [rows[0]], 2, 2, True, 30, [], {})["ok"])  # 한 종류가 두 건
        self.assertFalse(m.judge_j0(rows, 2, 2, True, 30, ["raw.chat"], {})["ok"])
        self.assertFalse(m.judge_j0(rows, 2, 2, True, 30, None, {})["ok"])  # 토픽 확인 못함
        self.assertFalse(m.judge_j0([dict(rows[0], published=True)] + rows[1:], 2, 2, True, 30, [], {})["ok"])


class RawTopicsTest(unittest.TestCase):
    def run_with(self, script):
        with tempfile.TemporaryDirectory() as d:
            cmd = os.path.join(d, "topics")
            with open(cmd, "w") as f:
                f.write("#!/bin/sh\n" + script + "\n")
            os.chmod(cmd, 0o700)
            return m.raw_topics(cmd, "x:1")

    def test_listing_must_include_the_cdc_topic(self):
        self.assertEqual(self.run_with("echo metaverse.outbox.events; echo raw.chat"), ["raw.chat"])
        self.assertEqual(self.run_with("echo metaverse.outbox.events"), [])
        self.assertIsNone(self.run_with("echo ads.outbox.events; echo raw.chat"))  # 목록은 있지만 CDC 토픽이 없음
        self.assertIsNone(self.run_with("true"))  # 빈 출력은 조회 실패로 본다
        self.assertIsNone(self.run_with("exit 1"))
        self.assertIsNone(m.raw_topics("/없는/경로", "x:1"))


class SimClockTest(unittest.TestCase):
    def tearDown(self):
        mv_simclock._clock, mv_simclock._last = None, float("-inf")

    def test_formula_forward_only_and_not_past_real_now(self):
        real = [1_000_000.0]
        with mock.patch.object(mv_simclock.time, "time", lambda: real[0]):
            mv_simclock.start(500_000.0, 1_000_000.0, 120)
            self.assertEqual(mv_simclock.now_ts(), 500_000.0)
            real[0] += 10
            self.assertEqual(mv_simclock.now_ts(), 501_200.0)
            real[0] -= 5  # 벽시계가 뒤로 가도
            self.assertEqual(mv_simclock.now_ts(), 501_200.0)
            real[0] += 10_000  # 시험 시각이 실제 지금을 따라잡으면 실제 지금에 머문다
            self.assertEqual(mv_simclock.now_ts(), real[0])
            self.assertEqual(mv_simclock.now().tzinfo, timezone.utc)

    def test_real_time_before_start(self):
        self.assertAlmostEqual(mv_simclock.now_ts(), __import__("time").time(), delta=1)

    def test_bad_speed(self):
        with self.assertRaises(ValueError):
            mv_simclock.start(0, 0, 0)


if __name__ == "__main__":
    unittest.main()
