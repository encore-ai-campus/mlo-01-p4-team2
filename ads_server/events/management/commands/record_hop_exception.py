import getpass
from datetime import datetime, timezone

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from events import hopcheck, hops
from foundation.api import User, record_audit

MAX_TEXT = 500  # 감사 detail 의 문자열은 이 길이에서 잘린다(foundation.audit) — 저장되는 값이 입력과 같게 미리 거부한다


class Command(BaseCommand):
    help = (
        "끝내 match 가 안 되는 창(유령·원천 유실·내용 충돌 등)을 사람이 사건을 확인한 뒤 '알려진 예외'로 감사 로그 events.hop_window_exception 에 남긴다(설계 01 D16). "
        "그때부터 verify_hops 는 그 창의 파이프라인 쌍 불일치를 EXC 로 보이고 실패로 세지 않으며, 정리는 기록일부터 30일 뒤에 그 창을 지운다(정리는 P1-6). "
        "이미 확정된 창은 받지 않는다. 기록자는 --by 의 관리자 계정과 OS 로그인 이름이고, 둘 다 인증이 아니다 — 셸 접근 권한이 곧 기록 권한이다."
    )

    def add_arguments(self, parser):
        parser.add_argument("--window", choices=sorted(hops.WINDOWS), required=True, help="창 크기: day 또는 hour(verify_hops 가 판정한 창 크기와 같게)")
        parser.add_argument("--start", action="append", required=True, help="예외로 남길 창의 시작(UTC 경계, YYYY-MM-DDTHH:00:00Z). 여러 번 줄 수 있다")
        parser.add_argument("--reason", required=True, help=f"사유(무엇을 확인했나, {MAX_TEXT}자까지)")
        parser.add_argument("--incident", required=True, help=f"사건 번호({MAX_TEXT}자까지)")
        parser.add_argument("--by", required=True, help="기록하는 사람의 활성 관리자 계정 이름(감사 actor 로 남는다 — 인증이 아니다)")

    def handle(self, *args, **options):
        window = hops.window_label(options["window"])
        size, now = hops.WINDOW_SIZES[window], datetime.now(timezone.utc)
        reason, incident = options["reason"].strip(), options["incident"].strip()
        if not reason or not incident:
            raise CommandError("--reason 과 --incident 는 비울 수 없습니다.")
        if len(reason) > MAX_TEXT or len(incident) > MAX_TEXT:
            raise CommandError(f"--reason 과 --incident 는 {MAX_TEXT}자까지입니다(감사 기록에서 잘리지 않게).")
        actor = User.objects.filter(username=options["by"]).first()
        if actor is None or not actor.is_staff:  # is_staff = 활성 상태의 관리자 역할
            raise CommandError(f"--by 는 활성 관리자 계정이어야 합니다: {options['by']!r}")
        verdicts, starts = hopcheck.window_verdicts(window), []
        for text in options["start"]:
            try:
                start = hops.parse_time(text)
            except ValueError:
                raise CommandError(f"--start 는 YYYY-MM-DDTHH:00:00Z 형식이어야 합니다: {text!r}")
            if hops.format_time(start) != text or hops.window_start(start, size) != start:
                raise CommandError(f"--start 가 {window} 창의 UTC 경계가 아닙니다: {text!r}")
            if not hops.is_closed(start, now, settings.EVENTS_HOP_GRACE_SECONDS, size):
                raise CommandError(f"아직 닫히지 않은 창은 예외로 남기지 않습니다: {text}")
            if verdicts.get(text) is True:  # 마지막 판정이 확정이면 일반 정리(7일)가 맡는다 — 잘못 친 창도 여기서 막힌다
                raise CommandError(f"마지막 판정이 확정인 창은 예외로 남기지 않습니다: {text}")
            starts.append(text)
        try:
            os_user = getpass.getuser()
        except OSError:
            os_user = ""
        log = record_audit(None, hopcheck.EXCEPTION_ACTION, detail=hopcheck.exception_detail(window, sorted(set(starts)), reason, incident, os_user), actor=actor)
        if log.detail.get("truncated"):  # 크기 한도를 넘어 통째로 잘렸다 — 그 행은 pair·window 가 없어 예외로 읽히지 않는다. 기록됐다고 알리지 않는다
            raise CommandError("감사 detail 크기 한도를 넘어 예외가 기록되지 않았다 — 창을 나눠서 다시 기록하세요.")
        self.stdout.write(f"알려진 예외 기록: {len(set(starts))}창(창 {window}, 사건 {incident}, 기록자 {actor.username}·{os_user or '-'})")
