from django.core.management.base import BaseCommand, CommandError

from foundation.api import User, errors
from masters import api


class Command(BaseCommand):
    help = "내부 모듈(C8)용 키를 발급·폐기한다. 발급한 관리자가 키의 주인(C8 쓰기의 감사 행위자)이다. 평문 키는 발급할 때 한 번만 출력된다(저장하지 않는다)."

    def add_arguments(self, parser):
        parser.add_argument("action", choices=["issue", "revoke"])
        parser.add_argument("key_id", nargs="?", help="revoke: 폐기할 키 ID(관리 사이트의 내부 키 목록에서 찾는다)")
        parser.add_argument("--admin", required=True, help="행위자(관리자 아이디). issue 에서는 키의 주인. 감사 로그에 남는다")
        parser.add_argument("--label", default="", help="issue: 키 이름(필수, 예: llm-classifier)")

    def handle(self, *args, **opts):
        try:
            actor = User.objects.get(username=opts["admin"])
        except User.DoesNotExist:
            raise CommandError("관리자를 찾을 수 없다")
        try:
            if opts["action"] == "revoke":
                if not opts["key_id"]:
                    raise CommandError("revoke 에는 키 ID 가 필요하다")
                api.revoke_internal_key(actor, opts["key_id"])
                self.stdout.write("revoked")
                return
            issued = api.issue_internal_key(actor, opts["label"])
        except errors.ApiError as e:
            raise CommandError(e.detail or e.title)
        self.stdout.write(f"key_id: {issued.key_id}\nkey: {issued.key}\n(이 키는 다시 볼 수 없다. 내부 모듈의 비밀 저장소에 넣는다)")
