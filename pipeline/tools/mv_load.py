"""메타버스 더미 데이터 도구(계획 MD-1 3판): 시험용 메타버스 앱(리허설 DB)에 사용자 N명이 사건 15종을 한 건씩 내게 한다(기본 100명 × 15 = 1,500건).

사용: <메타버스 가상환경 python> mv_load.py --run <이름> [--users 100] [--days 3] [--seed 1] [--speed 120] [--out <폴더>]
      [--distribution --confirmed-by <사용자 확인 기록>]
- 앱 경로는 환경 변수 MV_BACKEND(메타버스 저장소의 backend 폴더)로 받는다. 앱 저장소는 바꾸지 않는다. DB 설정(DATABASE_ENGINE·POSTGRES_*·PGPASSFILE)도 환경에서 받는다.
  로컬 DB(POSTGRES_HOST 127.0.0.1·localhost)가 아니면 시작하지 않는다.
- 발행기·Redis를 끈다: KAFKA_BOOTSTRAP_SERVERS·CAMPUS_REDIS_URL·CAMPUS_MOVEMENT_REDIS_URL·CAMPUS_SERVICE_REDIS_URL 이 비어 있지 않으면 시작하지 않고,
  없으면 빈 값으로 둔다(앱의 .env 는 override=False 라 빈 값을 덮지 않는다). Django 설정에서 다시 확인한다.
- 시드: 계정 `mvload_<run>_NNN`(비밀번호는 0600 문맥 파일에만), 프로필 설정 끝, 기수 6개에 고르게 + 미지정 10%, 문제은행(예제 20 + 초기 은행 100).
- 일정표(plan): 사용자마다 한 세션 — 로그인 → 글 → 채팅 → 이동 표본 + 층 전환(30초 안) → 콘텐츠 열기(게임·학습 반반) → 학습 답(정답)
  → 짝의 글 읽기·댓글 작성·수정·삭제 → 자기 글 수정·삭제(짝의 댓글 작업 뒤) → 로그아웃(80%) 또는 30분 넘게 쉰 뒤 상태 요청(만료 20%).
  하루 사용자를 로그인 순으로 지그재그 원으로 잇는다(이웃이 두 칸 안 — 세션이 겹친다). 세션은 6시간 이하, 요청 사이가 20분을 넘으면 활동 표시를 넣는다.
  날짜는 어제부터 --days 일 전까지(한국 날짜). 하루 사용자가 24명 이상이면 07:15~23:15에 흩고 첫 사람은 UTC 자정(한국 9시), 마지막 사람은 한국 자정을 넘긴다.
  그보다 적으면(작은 통합 실행) 짝수 날은 한국 9시, 홀수 날은 한국 23시 둘레에 모으고 그 경계를 넘긴다.
- 실행: 시험 시계(mv_simclock)로 띄운 daphne에 HTTP 12종, 셸 3종(채팅·이동·층 전환 — 소켓이 부르는 같은 함수)을 낸다. 동작마다 503·연결 끊김 뒤 처리가 다르다(계획 §5 표):
  다시 보냄(답·활동·상태·조회) / 상태로 확인(로그인·로그아웃) / 표지 검색으로 확인(글·댓글 작성·수정) / 다시 보내고 404면 끝(삭제) / "확인 필요"(글 상세·콘텐츠 열기) /
  outbox 를 본 뒤에만 다시 부름(셸 3종).
- 장부(ledger.ndjson): 성공한 동작만 한 줄씩(시험 시각 t0·t1, 세션 UUID, 사건 종류와 참조 값).
- 판정: J0 시험 유효(사용자 × 15종 각 1, 두 끝 이유·두 영역, UTC 날짜 수 ≥ days, UTC·한국 자정을 넘는 세션, 미발행·payload 있음, raw.* 토픽 0, 발행기·Redis 꺼짐)
  / J1 실패 없음(최종 실패·확인 필요·건너뜀·실제 시각 규칙 어긋남 0, 백엔드 종료 코드 0) / J2 장부 = outbox(사용자·종류별 건수, 각 행의 created_at 이 그 동작의 시험 시각 구간 안,
  세션 UUID·참조 값 일치 — 시간별 건수보다 엄격). J3(outbox = Bronze)은 Bronze가 따라잡은 뒤 따로 본다(보고의 created_at 구간).
- 분포 모드(--distribution)는 오답·30초 넘는 간격의 이동 표본 여러 건·다른 날 세션을 더한다. 켤 때마다 사용자 확인을 받아 --confirmed-by 에 적는다.
- SIGTERM 을 받으면 띄운 daphne를 내리고 rc=143 으로 끝난다.
"""
import argparse
import http.client
import http.cookiejar
import json
import os
import random
import re
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
KST = timezone(timedelta(hours=9))
KINDS = ("session_started", "session_ended", "post_created", "post_updated", "post_deleted", "post_read", "comment_created", "comment_updated",
         "comment_deleted", "content_open_requested", "quiz_answered", "point_granted", "chat_sent", "movement_observed", "floor_transferred")
COHORTS = ("mlo-1", "mlo-2", "mlo-3", "skn-34", "skn-36", "aio-4")
GROUPS = tuple((s, t, d) for s in ("python", "sql") for t in ("multiple_choice", "fill_blank") for d in ("beginner", "intermediate", "advanced"))
EXPECT = {"login": ["session_started"], "post_create": ["post_created"], "chat": ["chat_sent"], "move": ["movement_observed"],
          "transfer": ["floor_transferred"], "open_game": ["content_open_requested"], "open_learning": ["content_open_requested"], "read": ["post_read"],
          "comment_create": ["comment_created"], "comment_update": ["comment_updated"], "comment_delete": ["comment_deleted"],
          "post_update": ["post_updated"], "post_delete": ["post_deleted"], "logout": ["session_ended"], "expire": ["session_ended"], "keepalive": []}
# 미들웨어가 활동 시각을 갱신하는 요청(GET 이 아닌 것 + 활동 표시). 로그아웃·상태는 갱신하지 않는다(session_policy.py:155-160)
TOUCH = {"login", "post_create", "post_update", "post_delete", "comment_create", "comment_update", "comment_delete", "open_learning", "learn", "keepalive"}
HTTP = TOUCH | {"read", "open_game", "logout", "expire"}
# 일정표 여유(PLAN)와 앱의 실제 한계(LIVE — 실행 뒤 장부 시각에 적용). 초 단위
PLAN = {"gap": 20 * 60, "idle": 45 * 60, "transfer": 10, "move_gap": 45, "session": 6 * 3600}
LIVE = {"gap": 30 * 60 - 15, "idle": 30 * 60, "transfer": 30, "move_gap": 30, "session": 8 * 3600}
FULL_MIN = 24  # 하루 사용자가 이만큼이면 하루 전체에 흩는다
FULL = ((7.25, 9.0, 2.0), (9.0, 18.0, 4.0), (18.0, 23.25, 2.0))  # (시작 시, 끝 시, 시간당 무게) 한국 시간
KEEP_GAP, KEEP_EVERY = 20 * 60, 15 * 60
PATTERNS = ((["right"], 0.6), (["wrong", "right"], 0.25), (["wrong", "wrong", "wrong"], 0.15))  # 분포 모드의 문제별 답


# ---------------------------------------------------------------- 일정표(순수)

def learn_kinds(questions):
    return [k for attempts in questions for a in attempts for k in (["quiz_answered", "point_granted"] if a == "right" else ["quiz_answered"])]


def _stratified(r, n, segments):
    """활동 곡선(구간별 무게)의 분위 칸마다 한 명씩 — 이웃 사이가 칸 두세 개를 넘지 않는다. 시(hour) 목록."""
    total = sum((b - a) * w for a, b, w in segments)
    out = []
    for k in range(n):
        u = (k + r.random()) / n * total
        for a, b, w in segments:
            if u <= (b - a) * w:
                out.append(a + u / w)
                break
            u -= (b - a) * w
        else:  # 부동소수 끝자리
            out.append(segments[-1][1])
    return out


def _questions(r, distribution):
    if not distribution:
        return [["right"]]
    first = ["wrong"] * r.randint(0, 2) + ["right"]
    return [first] + [list(p) for p in r.choices([p for p, _ in PATTERNS], [w for _, w in PATTERNS], k=r.randint(0, 3))]


def _campus(r):
    return {"floor": "campus", "x": round(r.uniform(100, 1800), 1), "y": round(r.uniform(100, 900), 1), "dir": r.choice(("up", "down", "left", "right"))}


def _rooftop(r):
    return {"floor": "rooftop", "x": round(r.uniform(100, 2300), 1), "y": round(r.uniform(100, 1700), 1), "dir": "up"}


def _keepalive(acts):
    """한 세션의 동작(시각 순)에 활동 표시를 넣는다: 활동을 갱신한 요청 뒤 20분이 지나는 HTTP 요청 앞에 15분마다."""
    out, last = [], None
    for a in acts:
        if a["act"] in HTTP and a["act"] != "expire" and last is not None:
            while a["t"] - last > KEEP_GAP:
                last += KEEP_EVERY
                out.append(dict(a, act="keepalive", t=last, kinds=[]))
                for key in ("code", "subject", "qtype", "difficulty", "questions", "pos", "partner"):
                    out[-1].pop(key, None)
        out.append(a)
        if a["act"] in TOUCH:
            last = a["t"]
    return out


def _day(r, members, users, day0, k, codes, distribution):
    n = len(members)
    if n >= FULL_MIN:
        segments, pins = FULL, {"first": 9.25, "last": 24.25}
    else:  # 작은 실행: 한 경계 둘레에 모은다(칸 30분)
        center = 9.0 if k % 2 == 0 else 23.0
        segments, pins = ((center - n * 0.25, center + n * 0.25, 1.0),), ({"first": 9.25} if k % 2 == 0 else {"last": 24.25})
    login = dict(zip(members, (day0 + h * 3600 for h in sorted(_stratified(r, n, segments)))))
    ordered = sorted(members, key=login.get)
    zig = ordered[0::2] + ordered[1::2][::-1]
    nxt = {zig[i]: zig[(i + 1) % n] for i in range(n)}
    prv = {v: u for u, v in nxt.items()}
    min_end = {}
    if "first" in pins:
        min_end[ordered[0]] = day0 + pins["first"] * 3600
    if "last" in pins:
        min_end[ordered[-1]] = day0 + pins["last"] * 3600
    acts = defaultdict(list)

    def add(u, act, t, **kw):
        acts[u].append({"t": t, "user": u, "session": 0, "act": act, "kinds": kw.pop("kinds", EXPECT.get(act)), **kw})
        return t

    def m(a, b):
        return r.uniform(a, b) * 60

    post_t, a_end, c_end = {}, {}, {}
    for u in members:
        t = add(u, "login", login[u])
        post_t[u] = t = add(u, "post_create", t + m(1, 4))
        t = add(u, "chat", t + m(0.5, 3))
        t = add(u, "move", t + m(0.5, 3), pos=_campus(r))
        for _ in range(r.randint(0, 3) if distribution else 0):
            t = add(u, "move", t + r.uniform(45, 90), pos=_campus(r))
        t = add(u, "transfer", t + r.uniform(3, 8), pos=_rooftop(r))
        subject, qtype, difficulty = r.choice(GROUPS)
        if users[u]["content"] == "game":
            t = add(u, "open_game", t + m(0.5, 3), code=r.choice(codes))
        else:
            t = add(u, "open_learning", t + m(0.5, 3), subject=subject, qtype=qtype)
        q = _questions(r, distribution)
        a_end[u] = add(u, "learn", t + m(1, 4), subject=subject, qtype=qtype, difficulty=difficulty, questions=q, kinds=learn_kinds(q))
    for u in members:
        j = nxt[u]
        t = add(u, "read", max(a_end[u], post_t[j]) + m(2, 8), partner=j)
        for act in ("comment_create", "comment_update", "comment_delete"):
            t = add(u, act, t + m(1, 4), partner=j)
        c_end[u] = t
    for u in members:
        t = add(u, "post_update", c_end[u] + m(1, 4))
        t = add(u, "post_delete", max(t, c_end[prv[u]]) + m(1, 5))
        if users[u]["end"] == "logout":
            add(u, "logout", max(t + m(1, 8), min_end.get(u, 0)))
        else:
            add(u, "expire", max(t + m(45, 60), min_end.get(u, 0)))
    return {u: _keepalive(a) for u, a in acts.items()}


def _extra_sessions(r, users, first_day, days):
    """분포 모드: 사용자마다 다른 날 세션 0~2개(로그인 → 이동 1~3 → 학습 열기 → 학습 → 로그아웃)."""
    out = []
    for user in users:
        others = [k for k in range(days) if k != user["day"]]
        for s, k in enumerate(r.sample(others, min(len(others), r.randint(0, 2))), 1):
            t = datetime.combine(first_day + timedelta(days=k), datetime.min.time(), KST).timestamp() + r.uniform(10, 20) * 3600
            acts = []

            def add(act, t, **kw):
                acts.append({"t": t, "user": user["idx"], "session": s, "act": act, "kinds": kw.pop("kinds", EXPECT.get(act)), **kw})
                return t

            t = add("login", t)
            t = add("move", t + r.uniform(1, 3) * 60, pos=_campus(r))
            for _ in range(r.randint(0, 2)):
                t = add("move", t + r.uniform(45, 90), pos=_campus(r))
            subject, qtype, difficulty = r.choice(GROUPS)
            t = add("open_learning", t + r.uniform(1, 3) * 60, subject=subject, qtype=qtype)
            q = [list(p) for p in r.choices([p for p, _ in PATTERNS], [w for _, w in PATTERNS], k=r.randint(1, 3))]
            t = add("learn", t + r.uniform(1, 3) * 60, subject=subject, qtype=qtype, difficulty=difficulty, questions=q, kinds=learn_kinds(q))
            add("logout", t + r.uniform(1, 5) * 60)
            out += _keepalive(acts)
    return out


def plan(n_users, days, seed, first_day, codes, logout_ratio=0.8, game_ratio=0.5, distribution=False):
    """시드가 같으면 같은 일정표. first_day: 첫 한국 날짜. 반환 {users, actions(시각 순), start, end}."""
    if days < 1 or n_users < 2 * days:
        raise ValueError("하루에 두 명 이상이어야 한다(짝)")
    r = random.Random(f"mvload:{seed}")
    order = list(range(n_users))
    r.shuffle(order)
    expire = set(r.sample(range(n_users), n_users - round(n_users * logout_ratio)))
    game = set(r.sample(range(n_users), round(n_users * game_ratio)))
    assigned = n_users - max(1, n_users // 10)
    users = [{"idx": i, "nnn": f"{i + 1:03d}", "cohort": COHORTS[i % len(COHORTS)] if i < assigned else "unassigned",
              "end": "expire" if i in expire else "logout", "content": "game" if i in game else "learning"} for i in range(n_users)]
    actions, pos = [], 0
    for k in range(days):
        size = n_users // days + (k < n_users % days)
        members, pos = order[pos:pos + size], pos + size
        for u in members:
            users[u]["day"] = k
        day0 = datetime.combine(first_day + timedelta(days=k), datetime.min.time(), KST).timestamp()
        for attempt in range(50):  # 세션 6시간 상한을 넘으면 같은 시드에서 정해진 다음 난수로 다시
            per_user = _day(random.Random(f"mvload:{seed}:{k}:{attempt}"), members, users, day0, k, codes, distribution)
            if all(a[-1]["t"] - a[0]["t"] <= PLAN["session"] for a in per_user.values()):
                break
        else:
            raise RuntimeError(f"{k}일째 일정을 6시간 안에 만들지 못했다")
        actions += [a for acts in per_user.values() for a in acts]
    if distribution:
        actions += _extra_sessions(random.Random(f"mvload:{seed}:extra"), users, first_day, days)
    for i, a in enumerate(actions):
        a["k"] = i
    actions.sort(key=lambda a: (a["t"], a["k"]))
    return {"users": users, "actions": actions, "start": actions[0]["t"], "end": actions[-1]["t"]}


# ---------------------------------------------------------------- 규칙 검사(순수 — 일정표와 실행 장부 둘 다)

def _t0(r):
    return r.get("t0", r.get("t"))


def _t1(r):
    return r.get("t1", r.get("t"))


def problems(rows, strict, lim):
    """일정표 행(t) 또는 장부 행(t0·t1)이 앱 규칙과 의존 순서를 지키는지. 어긋난 곳의 설명 목록."""
    out, by_user, once = [], defaultdict(list), {}
    for r in rows:
        by_user[r["user"]].append(r)
    for user, rs in sorted(by_user.items()):
        rs.sort(key=_t0)
        kinds = Counter(k for r in rs for k in r["kinds"])
        bad = {k: kinds[k] for k in set(KINDS) | set(kinds) if k not in KINDS or (kinds[k] != 1 if strict else kinds[k] < 1)}
        if bad:
            out.append(f"사용자 {user}: 종류 수 {dict(sorted(bad.items()))}")
        sessions = defaultdict(list)
        for r in rs:
            sessions[r["session"]].append(r)
        prev_end = float("-inf")
        for s, srs in sorted(sessions.items(), key=lambda kv: _t0(kv[1][0])):
            if srs[0]["act"] != "login" or srs[-1]["act"] not in ("logout", "expire"):
                out.append(f"사용자 {user} 세션 {s}: 로그인으로 시작해 로그아웃·만료로 끝나지 않음")
            if _t0(srs[0]) <= prev_end:
                out.append(f"사용자 {user} 세션 {s}: 앞 세션과 겹침")
            prev_end = _t1(srs[-1])
            if _t1(srs[-1]) - _t0(srs[0]) > lim["session"]:
                out.append(f"사용자 {user} 세션 {s}: 길이 {(_t1(srs[-1]) - _t0(srs[0])) / 3600:.2f}시간")
            last_touch = last_move = None
            for r in srs:
                act = r["act"]
                if act == "expire":
                    if last_touch is None or _t0(r) - _t1(last_touch) < lim["idle"]:
                        out.append(f"사용자 {user}: 만료 전 쉰 시간이 {lim['idle']}초 미만")
                elif act in HTTP and last_touch is not None and _t1(r) - _t0(last_touch) > lim["gap"]:
                    out.append(f"사용자 {user} {act}: 활동 뒤 {(_t1(r) - _t0(last_touch)) / 60:.1f}분(세션 만료 위험)")
                if act == "move":
                    if last_move is not None and _t0(r) - _t1(last_move) < lim["move_gap"]:
                        out.append(f"사용자 {user}: 이동 표본 간격 {_t0(r) - _t1(last_move):.0f}초")
                    last_move = r
                if act == "transfer" and (last_move is None or _t1(r) - _t0(last_move) >= lim["transfer"]):
                    out.append(f"사용자 {user}: 층 전환이 이동 표본 뒤 {lim['transfer']}초 안이 아님")
                if act in TOUCH:
                    last_touch = r
        for r in rs:
            once.setdefault((user, r["act"]), r)
    for user in sorted(by_user):
        chain = [once.get((user, a)) for a in ("post_create", "post_update", "post_delete")]
        if None not in chain and not (_t1(chain[0]) <= _t0(chain[1]) and _t1(chain[1]) <= _t0(chain[2])):
            out.append(f"사용자 {user}: 글 작성·수정·삭제 순서")
        read = once.get((user, "read"))
        if read is None:
            continue
        j = read.get("partner")
        steps = [once.get((user, a)) for a in ("read", "comment_create", "comment_update", "comment_delete")]
        made, gone = once.get((j, "post_create")), once.get((j, "post_delete"))
        if j == user or None in steps or made is None or gone is None:
            out.append(f"사용자 {user}: 짝 {j}의 글·댓글 동작이 없음")
            continue
        if not (_t1(made) <= _t0(steps[0]) and all(_t1(a) <= _t0(b) for a, b in zip(steps, steps[1:])) and _t1(steps[-1]) <= _t0(gone)):
            out.append(f"사용자 {user}: 짝 {j}의 글 작성 → 읽기·댓글 → 글 삭제 순서")
    partners = Counter(r["partner"] for r in rows if r["act"] == "read")
    if any(c != 1 for c in partners.values()):
        out.append(f"글을 두 번 이상 읽힌 사용자: {sorted(u for u, c in partners.items() if c != 1)}")
    return out


# ---------------------------------------------------------------- 판정(순수)

def match_outbox(ledger, outbox, eps=1.0):
    """J2: (사용자, 종류)별 건수가 같고, k번째 outbox 행의 created_at 이 k번째 장부 동작의 [t0, t1] 안이며, 세션 UUID·참조 값이 같은지."""
    want, got, out = defaultdict(list), defaultdict(list), []
    for row in ledger:
        for kind, ref in row["events"]:
            want[(row["user_uuid"], kind)].append((row["t0"], row["t1"], row["session_uuid"], ref))
    for o in outbox:
        got[(o["user_uuid"], o["event_type"])].append(o)
    for key in sorted(set(want) | set(got)):
        w, g = sorted(want.get(key, []), key=lambda x: x[0]), sorted(got.get(key, []), key=lambda o: o["created_at"])
        if len(w) != len(g):
            out.append(f"{key}: 장부 {len(w)}건, outbox {len(g)}건")
            continue
        for (t0, t1, session, ref), o in zip(w, g):
            raw = o["raw_data"]
            if not t0 - eps <= o["created_at"] <= t1 + eps:
                out.append(f"{key}: created_at {o['created_at']:.1f} 이 동작 구간 [{t0:.1f}, {t1:.1f}] 밖")
            wrong = {k: (v, raw.get(k)) for k, v in {"session_uuid": session, **ref}.items() if raw.get(k) != v}
            if wrong:
                out.append(f"{key}: 값이 다름 {wrong}")
    return out


def judge_j0(outbox, n_users, days, strict, ledger_total, raw_topics, settings_bad):
    per = Counter((o["user_uuid"], o["event_type"]) for o in outbox)
    users = {o["user_uuid"] for o in outbox}
    each = all((per[(u, k)] == 1) if strict else (per[(u, k)] >= 1) for u in users for k in KINDS)
    total = n_users * len(KINDS) if strict else ledger_total
    reasons = sorted({o["raw_data"].get("end_reason") for o in outbox if o["event_type"] == "session_ended"})
    domains = sorted({o["raw_data"].get("domain") for o in outbox if o["event_type"] == "content_open_requested"})
    utc_dates = sorted({datetime.fromtimestamp(o["created_at"], timezone.utc).date().isoformat() for o in outbox})
    span = defaultdict(dict)
    for o in outbox:
        if o["event_type"] in ("session_started", "session_ended"):
            span[o["raw_data"].get("session_uuid")][o["event_type"]] = o["created_at"]
    ends = [(s["session_started"], s["session_ended"]) for s in span.values() if len(s) == 2]
    cross_utc = sum(datetime.fromtimestamp(a, timezone.utc).date() != datetime.fromtimestamp(b, timezone.utc).date() for a, b in ends)
    cross_kst = sum(datetime.fromtimestamp(a, KST).date() != datetime.fromtimestamp(b, KST).date() for a, b in ends)
    published = sum(o["published"] for o in outbox)
    payload_null = sum(o["payload_null"] for o in outbox)
    ok = (len(users) == n_users and each and len(outbox) == total and {"LOGOUT", "SESSION_EXPIRED"} <= set(reasons)
          and {"REST", "LEARNING"} <= set(domains) and len(utc_dates) >= days and cross_utc >= 1 and cross_kst >= 1
          and published == 0 and payload_null == 0 and raw_topics == [] and not settings_bad)
    return {"ok": ok, "rows": len(outbox), "expected_rows": total, "users": len(users), "each_kind_ok": each, "end_reasons": reasons,
            "domains": domains, "utc_dates": utc_dates, "sessions_crossing_utc_midnight": cross_utc, "sessions_crossing_kst_midnight": cross_kst,
            "published_not_null": published, "payload_json_null": payload_null, "raw_topics": raw_topics, "settings_not_empty": settings_bad}


# ---------------------------------------------------------------- HTTP

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Http:
    """브라우저 한 개(쿠키 상자). 302 는 따라가지 않고 상태로 돌려준다. 연결 실패는 OSError."""

    def __init__(self, base, timeout=15):
        self.base, self.timeout, self.jar = base, timeout, http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar), _NoRedirect)

    def csrf(self):
        return next((c.value for c in self.jar if c.name == "campus_csrftoken"), "")

    def request(self, method, path, json_body=None, form=None):
        headers, data = {}, None
        if json_body is not None:
            data, headers["Content-Type"] = json.dumps(json_body).encode(), "application/json"
        elif form is not None:
            data, headers["Content-Type"] = urllib.parse.urlencode(form).encode(), "application/x-www-form-urlencoded"
        if method != "GET":
            headers["X-CSRFToken"] = self.csrf()
            data = b"" if data is None else data
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with self.opener.open(req, timeout=self.timeout) as r:
                status, raw, h = r.status, r.read(), r.headers
        except urllib.error.HTTPError as e:
            status, raw, h = e.code, e.read(), e.headers
        try:
            body = json.loads(raw) if raw[:1] in (b"{", b"[") else None
        except ValueError:
            body = None
        return status, body, h.get("Retry-After")


class Fail(Exception):
    def __init__(self, why, status=None):
        super().__init__(why)
        self.status = status


class Unverified(Exception):
    pass


class Transient(Exception):
    """DB 일시 오류(셸 동작·DB 읽기). 커밋됐는지 모른다."""


# ---------------------------------------------------------------- 실행기

class Runner:
    def __init__(self, db, base, run, users, clock, speed, ledger, give_up=90.0, http=Http, sleep=time.sleep, monotonic=time.monotonic):
        self.db, self.base, self.run_name, self.users, self.clock, self.speed = db, base, run, users, clock, speed
        self.ledger, self.give_up, self.http, self.sleep, self.monotonic = ledger, give_up, http, sleep, monotonic
        self.counts, self.status = Counter(), Counter()
        self.failures, self.unverified, self.skipped, self.rows = [], [], [], []
        self.broken, self.lag = set(), 0.0

    # --- 요청 한 번과 재시도 규칙
    def call(self, c, method, path, json_body=None, form=None):
        try:
            status, body, ra = c.request(method, path, json_body, form)
        except (OSError, http.client.HTTPException):
            status, body, ra = None, None, None
        self.status[status] += 1
        try:
            wait = min(float(ra), 10.0) if ra else 1.0
        except ValueError:
            wait = 1.0
        return status, body, wait

    def retrying(self, c, method, path, ok, json_body=None, form=None):
        """사건이 없거나 멱등인 요청: 503·연결 끊김이면 같은 요청을 다시 보낸다."""
        return self.guarded(lambda: self.call(c, method, path, json_body, form), ok, label=f"{method} {path}")

    def guarded(self, do, ok, check=None, unverified=False, ok_after_retry=(), label=""):
        deadline, retried = self.monotonic() + self.give_up, False
        while True:
            status, body, wait = do()
            if status in ok or (retried and status in ok_after_retry):
                return status, body
            if status not in (None, 503):
                raise Fail(f"{label} → {status} {str(body)[:200]}", status)
            if unverified:
                raise Unverified(f"{label} → {status}")
            if check is not None:
                found = check()
                if found is not None:
                    return "checked", found
            if self.monotonic() > deadline:
                raise Fail(f"{label} → {status}, {self.give_up}초 동안 회복 안 됨", status)
            self.sleep(wait)
            retried = True

    def db_retry(self, fn):
        deadline = self.monotonic() + self.give_up
        while True:
            try:
                return fn()
            except Transient:
                if self.monotonic() > deadline:
                    raise Fail("DB 읽기가 회복되지 않음")
                self.sleep(1.0)

    def shell(self, u, kind, fn, ref_key=None):
        """셸 동작: 일시 오류면 outbox 에서 이 사용자·종류가 장부보다 많은지 먼저 본다(커밋됐을 수 있다). 아니면 다시 부른다."""
        deadline = self.monotonic() + self.give_up
        while True:
            try:
                result = fn()
                if result is False or result is None:
                    raise Fail(f"{kind}: 앱 함수가 저장하지 않음({result})")
                return result
            except Transient:
                if self.db_retry(lambda: self.db.count(u, kind)) > self.counts[(u["idx"], kind)]:
                    return self.db_retry(lambda: self.db.last_ref(u, kind, ref_key)) if ref_key else True
                if self.monotonic() > deadline:
                    raise Fail(f"{kind}: {self.give_up}초 동안 회복 안 됨")
                self.sleep(1.0)

    def status_of(self, u):
        status, body = self.retrying(u["client"], "GET", "/accounts/session/status/", {200, 401})
        return status == 200 and bool(body and body.get("authenticated"))

    def search(self, u, marker):
        _, body = self.retrying(u["client"], "GET", "/api/community/posts/?q=" + urllib.parse.quote(marker), {200})
        items = body["posts"]
        if len(items) > 1:
            raise Fail(f"표지 {marker} 글이 {len(items)}개")
        return items[0] if items else None

    def comment_of(self, u, post_id, marker):
        _, body = self.retrying(u["client"], "GET", f"/api/community/posts/{post_id}/comments/", {200})
        found = [c for c in body["comments"] if marker in c["body"] and not c["isDeleted"]]
        if len(found) > 1:
            raise Fail(f"표지 {marker} 댓글이 {len(found)}개")
        return found[0] if found else None

    def marker(self, u, what):
        return f"[mvload:{self.run_name}:{u['nnn']}:{what}]"

    # --- 동작(돌려주는 값: [(종류, 참조 값)])
    def do_login(self, u, a):
        c = u["client"] = self.http(self.base)
        self.retrying(c, "GET", "/accounts/login/", {200})
        self.guarded(lambda: self.call(c, "POST", "/accounts/login/", form={"username": u["name"], "password": u["password"], "csrfmiddlewaretoken": c.csrf()}),
                     {302}, check=lambda: {} if self.status_of(u) else None, label="로그인")
        self.retrying(c, "GET", "/api/learning/csrf/", {200})  # 로그인 뒤 바뀐 CSRF 쿠키
        u["sess"] = self.db_retry(lambda: self.db.session_uuid(u))
        return [("session_started", {})]

    def do_keepalive(self, u, a):
        _, body = self.retrying(u["client"], "POST", "/accounts/session/activity/", {200})
        if not body.get("authenticated"):
            raise Fail("활동 표시: 로그인 상태가 아님")
        return []

    def do_post_create(self, u, a):
        marker = self.marker(u, "post")
        body = {"category": "free", "title": f"더미 글 {u['nnn']}", "body": f"더미 본문 {marker}"}

        def check():
            p = self.search(u, marker)
            return {"post": p} if p else None

        _, res = self.guarded(lambda: self.call(u["client"], "POST", "/api/community/posts/", body), {201}, check=check, label="글 작성")
        p = res["post"]
        u["post"] = {"id": p["id"], "uuid": p["postUuid"], "version": p["updatedAt"], "revision": p["revision"]}
        return [("post_created", {"post_uuid": p["postUuid"]})]

    def do_post_update(self, u, a):
        post, marker = u["post"], self.marker(u, "post")
        body = {"category": "free", "title": f"더미 글 {u['nnn']} 수정", "body": f"더미 본문 {marker} 수정", "version": post["version"]}

        def check():
            p = self.search(u, marker)
            return {"post": p} if p and p["id"] == post["id"] and p["revision"] > post["revision"] else None

        _, res = self.guarded(lambda: self.call(u["client"], "PATCH", f"/api/community/posts/{post['id']}/", body), {200}, check=check, label="글 수정")
        post.update(version=res["post"]["updatedAt"], revision=res["post"]["revision"])
        return [("post_updated", {"post_uuid": post["uuid"]})]

    def do_post_delete(self, u, a):
        post = u["post"]
        self.guarded(lambda: self.call(u["client"], "DELETE", f"/api/community/posts/{post['id']}/"), {200}, ok_after_retry={404}, label="글 삭제")
        return [("post_deleted", {"post_uuid": post["uuid"]})]

    def partner_post(self, a):
        post = self.users[a["partner"]].get("post")
        if post is None:
            raise Fail(f"짝 {a['partner']}의 글이 없음")
        return post

    def do_read(self, u, a):
        post = self.partner_post(a)
        self.guarded(lambda: self.call(u["client"], "GET", f"/api/community/posts/{post['id']}/"), {200}, unverified=True, label="글 상세")
        return [("post_read", {"post_uuid": post["uuid"]})]

    def do_comment_create(self, u, a):
        post, marker = self.partner_post(a), self.marker(u, "comment")

        def check():
            c = self.comment_of(u, post["id"], marker)
            return {"comment": c} if c else None

        _, res = self.guarded(lambda: self.call(u["client"], "POST", f"/api/community/posts/{post['id']}/comments/", {"body": f"더미 댓글 {marker}"}),
                              {201}, check=check, label="댓글 작성")
        c = res["comment"]
        u["comment"] = {"id": c["id"], "uuid": c["commentUuid"], "version": c["updatedAt"], "revision": c["revision"], "post": post}
        return [("comment_created", {"comment_uuid": c["commentUuid"], "post_uuid": post["uuid"]})]

    def do_comment_update(self, u, a):
        com, marker = u["comment"], self.marker(u, "comment")

        def check():
            c = self.comment_of(u, com["post"]["id"], marker)
            return {"comment": c} if c and c["id"] == com["id"] and c["revision"] > com["revision"] else None

        _, res = self.guarded(lambda: self.call(u["client"], "PATCH", f"/api/community/comments/{com['id']}/", {"body": f"더미 댓글 {marker} 수정", "version": com["version"]}),
                              {200}, check=check, label="댓글 수정")
        com.update(version=res["comment"]["updatedAt"], revision=res["comment"]["revision"])
        return [("comment_updated", {"comment_uuid": com["uuid"], "post_uuid": com["post"]["uuid"]})]

    def do_comment_delete(self, u, a):
        com = u["comment"]
        self.guarded(lambda: self.call(u["client"], "DELETE", f"/api/community/comments/{com['id']}/"), {200}, ok_after_retry={404}, label="댓글 삭제")
        return [("comment_deleted", {"comment_uuid": com["uuid"], "post_uuid": com["post"]["uuid"]})]

    def do_open_game(self, u, a):
        self.guarded(lambda: self.call(u["client"], "GET", f"/content/{a['code']}/open/"), {302}, unverified=True, label="게임 열기")
        return [("content_open_requested", {"content_uuid": self.db.game_uuid(a["code"])})]

    def do_open_learning(self, u, a):
        _, res = self.guarded(lambda: self.call(u["client"], "POST", "/api/learning/content-open/", {"subject": a["subject"], "question_type": a["qtype"]}),
                              {201}, unverified=True, label="학습 열기")
        return [("content_open_requested", {"content_uuid": res["content_uuid"]})]

    def do_learn(self, u, a):
        c, events = u["client"], []
        _, s = self.retrying(c, "POST", "/api/learning/sessions/", {201}, {"subject": a["subject"], "question_type": a["qtype"], "difficulty": a["difficulty"]})
        for order, attempts in enumerate(a["questions"], 1):
            path = f"/api/learning/sessions/{s['session_id']}/questions/{order}/"
            _, q = self.retrying(c, "GET", path, {200})
            for want in attempts:
                text, choice = self.db_retry(lambda: self.db.answer(q["question"]["question_uuid"], want == "right"))
                body = {"answer": text, "submission_request_uuid": str(uuid.uuid4())} | ({"choice_uuid": choice} if choice else {})
                _, res = self.retrying(c, "POST", path + "answers/", {201}, body)  # 같은 submission_request_uuid 로 다시 보내면 앞 답을 돌려준다
                if res["is_correct"] != (want == "right"):
                    raise Fail(f"학습 답 판정이 의도와 다름({want})")
                events.append(("quiz_answered", {"attempt_uuid": res["attempt_uuid"]}))
                if want == "right":
                    events.append(("point_granted", {"attempt_uuid": res["attempt_uuid"], "ledger_uuid": res["points"]["ledger_uuid"]}))
        return events

    def do_chat(self, u, a):
        message = self.shell(u, "chat_sent", lambda: self.db.chat(u, u["sess"], f"더미 채팅 {u['nnn']}"), "message_uuid")
        return [("chat_sent", {"message_uuid": message})]

    def do_move(self, u, a):
        self.shell(u, "movement_observed", lambda: self.db.move(u, u["sess"], a["pos"]))
        return [("movement_observed", {})]

    def do_transfer(self, u, a):
        self.shell(u, "floor_transferred", lambda: self.db.transfer(u, u["sess"], a["pos"]))
        return [("floor_transferred", {})]

    def do_logout(self, u, a):
        c = u["client"]
        self.guarded(lambda: self.call(c, "POST", "/accounts/logout/", form={"csrfmiddlewaretoken": c.csrf()}), {302},
                     check=lambda: None if self.status_of(u) else {}, label="로그아웃")
        return [("session_ended", {"end_reason": "LOGOUT"})]

    def do_expire(self, u, a):
        # 쉰 뒤 첫 요청에서 미들웨어가 만료 처리한다(삭제와 session_ended 가 한 트랜잭션 — 503 뒤 다시 보내면 signed_out)
        _, body = self.retrying(u["client"], "GET", "/accounts/session/status/", {401})
        if (body or {}).get("reason") not in ("idle", "signed_out"):
            raise Fail(f"만료가 아님: {body}")
        return [("session_ended", {"end_reason": "SESSION_EXPIRED"})]

    # --- 일정표 실행
    def wait(self, t):
        while (now := self.clock()) < t:
            self.sleep(min((t - now) / self.speed, 1.0))
        self.lag = max(self.lag, now - t)

    def run(self, actions):
        for seq, a in enumerate(actions):
            self.wait(a["t"])
            u = self.users[a["user"]]
            if a["user"] in self.broken:
                self.skipped.append({"seq": seq, "user": a["user"], "act": a["act"]})
                continue
            t0 = self.clock()
            session = u.get("sess")
            try:
                events = getattr(self, "do_" + a["act"])(u, a)
                if [k for k, _ in events] != a["kinds"]:
                    raise Fail(f"사건이 일정표와 다름: {[k for k, _ in events]} ≠ {a['kinds']}")
            except Unverified as e:
                self.unverified.append({"seq": seq, "user": a["user"], "act": a["act"], "why": str(e)})
                continue
            except Fail as e:
                self.broken.add(a["user"])
                self.failures.append({"seq": seq, "user": a["user"], "act": a["act"], "status": e.status, "why": str(e)})
                continue
            row = {"seq": seq, "user": a["user"], "user_uuid": u["uuid"], "session": a["session"], "act": a["act"], "partner": a.get("partner"),
                   "planned": a["t"], "t0": t0, "t1": self.clock(), "real": time.time(), "session_uuid": u["sess"] if a["act"] == "login" else session,
                   "kinds": a["kinds"], "events": [[k, ref] for k, ref in events]}
            self.counts.update((a["user"], k) for k, _ in events)
            self.rows.append(row)
            self.ledger.write(json.dumps(row, ensure_ascii=False) + "\n")
            self.ledger.flush()


# ---------------------------------------------------------------- 앱(Django) 쪽 — 실제 실행에서만

class DjangoDB:
    """도구 프로세스 안의 앱 함수·ORM. 쓰고 나면 연결을 닫는다(쉬는 동안 연결을 쥐지 않음). DB 오류는 Transient."""

    def __init__(self, backend):
        from django.contrib.auth import get_user_model
        from django.db import DatabaseError, connection
        self.backend, self.User, self.DatabaseError, self.connection = backend, get_user_model(), DatabaseError, connection
        self.games = {}

    def _do(self, fn):
        try:
            return fn()
        except self.DatabaseError as e:
            raise Transient(repr(e)) from e
        finally:
            self.connection.close()

    def game_codes(self):
        from eventing.models import ContentItem
        rows = ContentItem.objects.filter(status="ACTIVE", content_kind="EXTERNAL_LINK", external_url__isnull=False, zone__isnull=False)
        self.games = {code: str(pk) for code, pk in rows.values_list("code", "content_uuid")}
        self.connection.close()
        return sorted(self.games)

    def game_uuid(self, code):
        return self.games[code]

    def seed(self, run, users):
        from accounts.avatars import DEFAULT_AVATAR
        from accounts.cohorts import COHORT_CHOICES
        from accounts.models import Profile
        from django.core.management import call_command
        from learning.models import Question
        if set(COHORTS) != set(dict(COHORT_CHOICES)):
            raise SystemExit(f"앱의 기수가 도구와 다르다: {sorted(dict(COHORT_CHOICES))}")
        prefix = f"mvload_{run}_"
        if self.User.objects.filter(username__startswith=prefix).exists():
            raise SystemExit(f"{prefix}… 계정이 이미 있다 — 다른 --run 이름을 쓴다")
        call_command("seed_learning_questions", verbosity=0)
        call_command("seed_learning_questions", file=Path(self.backend) / "learning" / "data" / "initial_subject_bank.json", verbosity=0)
        for s, t, d in GROUPS:
            if Question.objects.filter(is_active=True, subject=s, question_type=t, difficulty=d).count() < 10:
                raise SystemExit(f"문제은행 {s}/{t}/{d} 활성 문제가 10개 미만")
        out = []
        for user in users:
            password = secrets.token_urlsafe(18)
            account = self.User.objects.create_user(username=prefix + user["nnn"], password=password)
            Profile.objects.update_or_create(user=account, defaults={
                "nickname": f"더미{user['nnn']}", "avatar": DEFAULT_AVATAR, "cohort": "" if user["cohort"] == "unassigned" else user["cohort"],
                "setup_complete": True, "guide_completed": True})
            out.append(dict(user, name=account.username, password=password, pk=account.pk, uuid=str(Profile.objects.get(user=account).user_uuid)))
        self.connection.close()
        return out

    def session_uuid(self, u):
        from eventing.models import PlatformSession
        return self._do(lambda: str(PlatformSession.objects.filter(user_id=u["pk"], ended_at__isnull=True).latest("started_at").session_uuid))

    def answer(self, question_uuid, right):
        from learning.grading import answers_match
        from learning.models import Question

        def pick():
            q = Question.objects.prefetch_related("choices").get(question_uuid=question_uuid)
            if q.question_type != "multiple_choice":
                return (q.correct_answer if right else "mvload_wrong"), None
            choices = list(q.choices.all())
            c = next(c for c in choices if answers_match(q.subject, q.question_type, c.choice_text, q.correct_answer) == right)
            return c.choice_text, str(c.choice_uuid)
        return self._do(pick)

    def count(self, u, kind):
        from eventing.models import EventOutbox
        return self._do(lambda: EventOutbox.objects.filter(message_key=u["uuid"], event_type=kind).count())

    def last_ref(self, u, kind, key):
        from eventing.models import EventOutbox
        return self._do(lambda: EventOutbox.objects.filter(message_key=u["uuid"], event_type=kind).latest("created_at").raw_data[key])

    def _user(self, u):
        return self.User.objects.get(pk=u["pk"])

    def chat(self, u, session, text):
        from eventing.events import record_chat
        return self._do(lambda: record_chat(self._user(u), session, text, "campus"))

    def move(self, u, session, pos):
        from campus.positions import save_position
        return self._do(lambda: save_position(self._user(u), pos, session_uuid=session))

    def transfer(self, u, session, pos):
        from eventing.events import record_floor_transfer
        return self._do(lambda: record_floor_transfer(self._user(u), session, "campus", pos["floor"], pos))

    def outbox(self, user_uuids):
        from eventing.models import EventOutbox
        rows = EventOutbox.objects.filter(message_key__in=user_uuids).values_list("message_key", "event_type", "created_at", "raw_data", "published_at", "payload_json")
        out = [{"user_uuid": str(k), "event_type": t, "created_at": c.timestamp(), "raw_data": r, "published": p is not None, "payload_null": j is None}
               for k, t, c, r, p, j in rows]
        self.connection.close()
        return out


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Backend:
    """시험 시계로 띄운 daphne(메타버스 백엔드). 로그는 실행 폴더의 backend.log."""

    def __init__(self, backend, out, env):
        self.port, self.log = free_port(), open(out / "backend.log", "ab")
        self.proc = subprocess.Popen([sys.executable, "-m", "daphne", "-b", "127.0.0.1", "-p", str(self.port), "mv_simclock:application"],
                                     cwd=backend, env=env, stdout=self.log, stderr=subprocess.STDOUT)
        self.base = f"http://127.0.0.1:{self.port}"
        deadline = time.monotonic() + 60
        try:
            while True:
                try:
                    if Http(self.base, timeout=2).request("GET", "/accounts/session/status/")[0] == 401:
                        break
                except OSError:
                    pass
                if self.proc.poll() is not None or time.monotonic() > deadline:
                    raise SystemExit(f"백엔드가 뜨지 않았다({out / 'backend.log'})")
                time.sleep(0.3)
        except BaseException:
            self.stop()
            raise

    def stop(self):
        if self.proc.poll() is None:
            self.proc.terminate()  # daphne(Twisted)는 SIGTERM 에 정상 종료(0). SIGINT 는 백그라운드로 띄운 셸에서 무시된 채 물려받을 수 있다
            try:
                self.proc.wait(30)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self.log.close()
        return self.proc.returncode


def raw_topics(cmd, bootstrap):
    try:
        listed = subprocess.run([cmd, "--bootstrap-server", bootstrap, "--list"], capture_output=True, text=True, timeout=120, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return sorted(t for t in listed.split() if t.startswith("raw."))


ENV_OFF = ("KAFKA_BOOTSTRAP_SERVERS", "CAMPUS_REDIS_URL", "CAMPUS_MOVEMENT_REDIS_URL", "CAMPUS_SERVICE_REDIS_URL")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", required=True, help="실행 이름(영소문자·숫자 12자까지) — 계정 이름과 결과 폴더에 쓴다. 다시 돌릴 때는 새 이름")
    ap.add_argument("--users", type=int, default=100)
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--speed", type=float, default=120.0, help="시험 시계 배속(기본 120 — 3일이 약 33분)")
    ap.add_argument("--logout-ratio", type=float, default=0.8)
    ap.add_argument("--game-ratio", type=float, default=0.5)
    ap.add_argument("--give-up", type=float, default=90.0, help="요청 하나의 재시도를 포기하는 실제 시간(초)")
    ap.add_argument("--distribution", action="store_true", help="분포 모드(오답·여러 이동 표본·다른 날 세션). --confirmed-by 가 있어야 돈다")
    ap.add_argument("--confirmed-by", default="", help="분포 모드 사용자 확인 기록")
    ap.add_argument("--out", default=str(Path(tempfile.gettempdir()) / "mvload"), help="결과 폴더의 부모(기본 $TMPDIR/mvload)")
    ap.add_argument("--kafka-topics", default="/opt/kafka/current/bin/kafka-topics.sh")
    ap.add_argument("--kafka-bootstrap", default="127.0.0.1:9092")
    a = ap.parse_args()
    if not re.fullmatch(r"[a-z0-9]{1,12}", a.run):
        ap.error("--run 은 영소문자·숫자 12자까지")
    if a.distribution and not a.confirmed_by.strip():
        ap.error("분포 모드는 켤 때마다 사용자 확인을 받는다 — --confirmed-by 에 확인 기록을 적는다")
    backend = Path(os.environ.get("MV_BACKEND", ""))
    if not (backend / "manage.py").is_file():
        raise SystemExit("MV_BACKEND 에 메타버스 backend 폴더를 준다")
    if os.environ.get("DATABASE_ENGINE") != "postgresql" or os.environ.get("POSTGRES_HOST") not in ("127.0.0.1", "localhost"):
        raise SystemExit("로컬 PostgreSQL(DATABASE_ENGINE=postgresql, POSTGRES_HOST=127.0.0.1)에서만 돈다")
    for key in ENV_OFF:
        if os.environ.setdefault(key, "").strip():
            raise SystemExit(f"{key} 가 비어 있지 않다(발행기·Redis 는 꺼야 한다)")
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    out = Path(a.out) / a.run
    out.mkdir(parents=True, exist_ok=False)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))

    sys.path[:0] = [str(TOOLS), str(backend)]
    import mv_simclock
    mv_simclock.install()  # Django 보다 먼저. start() 전에는 실제 시각(시드)
    import django
    django.setup()
    from django.conf import settings
    settings_bad = {k: getattr(settings, k) for k in ENV_OFF if getattr(settings, k)}
    if settings_bad:
        raise SystemExit(f"앱 설정에 발행기·Redis 가 켜져 있다: {sorted(settings_bad)}")
    db = DjangoDB(backend)
    first_day = datetime.now(KST).date() - timedelta(days=a.days)
    p = plan(a.users, a.days, a.seed, first_day, db.game_codes(), a.logout_ratio, a.game_ratio, a.distribution)
    found = problems(p["actions"], not a.distribution, PLAN)
    if found:
        raise SystemExit(f"일정표가 규칙을 어긴다(도구 결함): {found[:5]}")
    if p["end"] > time.time() - 600:
        raise SystemExit("일정표 끝이 지금보다 늦다(어제 밤 세션이 오늘 새벽까지 간다) — 한국 시간 01:30 뒤에 돌린다")
    users = db.seed(a.run, p["users"])
    s0, speed = p["start"] - 60, a.speed
    r0 = time.time() + 15  # 백엔드가 뜰 동안. 그 전의 시험 시각은 S0 보다 앞이라 과거다
    fd = os.open(out / "ctx.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)  # 비밀번호가 들어 있다
    with os.fdopen(fd, "w") as f:
        json.dump({"run": a.run, "seed": a.seed, "speed": speed, "s0": s0, "r0": r0, "users": users}, f, ensure_ascii=False)
    env = dict(os.environ, MV_SIM_S0=str(s0), MV_SIM_R0=str(r0), MV_SIM_SPEED=str(speed),
               PYTHONPATH=os.pathsep.join([str(TOOLS), str(backend)]))
    started = time.time()
    server = Backend(backend, out, env)
    mv_simclock.start(s0, r0, speed)
    print(f"start run={a.run} users={a.users} days={a.days} speed={speed} port={server.port} "
          f"sim {datetime.fromtimestamp(p['start'], KST).isoformat()} → {datetime.fromtimestamp(p['end'], KST).isoformat()} "
          f"(약 {(p['end'] - s0) / speed / 60:.0f}분)", flush=True)
    with open(out / "ledger.ndjson", "w") as ledger:
        runner = Runner(db, server.base, a.run, users, mv_simclock.now_ts, speed, ledger, a.give_up)
        try:
            runner.run(p["actions"])
        finally:
            exit_code = server.stop()
    outbox = db.outbox([u["uuid"] for u in users])
    topics = raw_topics(a.kafka_topics, a.kafka_bootstrap)
    live = problems(runner.rows, not a.distribution, LIVE) if not (runner.failures or runner.unverified or runner.skipped) else ["실패가 있어 건너뜀"]
    total = sum(len(r["events"]) for r in runner.rows)
    j0 = judge_j0(outbox, a.users, a.days, not a.distribution, total, topics, settings_bad)
    http_5xx = sum(1 for f in runner.failures if (f["status"] or 0) >= 500)
    j1 = {"ok": not (runner.failures or runner.unverified or runner.skipped or live) and exit_code == 0, "failures": runner.failures,
          "unverified": runner.unverified, "skipped": len(runner.skipped), "final_5xx": http_5xx, "timing_problems": live, "backend_exit_code": exit_code}
    j2 = match_outbox(runner.rows, outbox)
    stamps = [o["created_at"] for o in outbox]
    report = {"run": a.run, "seed": a.seed, "users": a.users, "days": a.days, "speed": speed, "distribution": a.distribution, "confirmed_by": a.confirmed_by or None,
              "sim_start": datetime.fromtimestamp(p["start"], KST).isoformat(), "sim_end": datetime.fromtimestamp(p["end"], KST).isoformat(),
              "real_seconds": round(time.time() - started, 1), "lag_max_sim_s": round(runner.lag, 1), "ledger_rows": len(runner.rows), "ledger_events": total,
              "http_status": {str(k): v for k, v in sorted(runner.status.items(), key=lambda kv: str(kv[0]))},
              "outbox_created_at_utc": [datetime.fromtimestamp(min(stamps), timezone.utc).isoformat(), datetime.fromtimestamp(max(stamps), timezone.utc).isoformat()] if stamps else None,
              "outbox_by_kind": dict(sorted(Counter(o["event_type"] for o in outbox).items())),
              "J0": j0, "J1": j1, "J2": {"ok": not j2, "problems": j2[:50], "problem_count": len(j2)},
              "J3": "따로 — Bronze 가 따라잡은 뒤 위 created_at 구간·이번 사용자로 대조"}
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print("MVLOAD_REPORT " + json.dumps({k: report[k] for k in ("ledger_events", "lag_max_sim_s", "outbox_by_kind")} | {j: report[j]["ok"] for j in ("J0", "J1", "J2")}, ensure_ascii=False), flush=True)
    print(f"report {out / 'report.json'}", flush=True)
    sys.exit(0 if all(report[j]["ok"] for j in ("J0", "J1", "J2")) else 1)


if __name__ == "__main__":
    main()
