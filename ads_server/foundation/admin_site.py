"""관리자 사이트. 관리자 본인의 비밀번호 변경도 감사 로그에 남긴다(위협 T23).

`UserAdmin` 은 다른 사용자의 비밀번호 초기화만 기록한다. 본인 변경은 AdminSite 의 별도 뷰라 여기서 잡는다.
두 비밀번호 화면 모두 `current_login_only` 로 감싸고 비밀번호 열만 쓴다(`PasswordOnlySave`).
본인 변경의 현재 비밀번호 확인은 로그인 잠금(axes)과 같은 계수이고 틀린 시도는 감사에 남는다(T43 — 광고주 `accounts.change_password` 와 같은 규칙).
"""
from functools import partial, wraps

from django.contrib import admin
from django.contrib.admin.forms import AdminPasswordChangeForm as OwnPasswordForm
from django.contrib.auth import HASH_SESSION_KEY, authenticate
from django.contrib.auth.views import PasswordChangeView
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils.crypto import constant_time_compare
from django.views.decorators.debug import sensitive_variables

from .audit import record_audit
from .models import User


class ReadOnlyAdmin(admin.ModelAdmin):
    """읽기 전용 관리자 화면. 서비스 규칙(검증·버전·상태 전이·감사)을 우회하지 않도록 모델 변경은 서비스 함수로만 한다."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class PasswordOnlySave:
    """비밀번호 폼의 저장을 **비밀번호 열로만**. Django 기본의 통째 저장은 폼의 사용자 객체를 읽은 뒤 다른 곳이 바꾼 열(활성 여부·세션 폐기 버전·프로필)을
    되돌린다(외부 검토 3-1d). 본인 변경 폼의 사용자는 인증할 때(잠그기 전) 읽은 객체라 꼭 필요하고, 사용자 관리의 초기화는 대상을 잠근 뒤에 읽지만
    **심층 방어**로 둔다 — 잠금이 대상을 놓치면(ID 해석 차이 등) 이 보호가 없을 때 비활성화가 되돌려졌다."""

    def save(self, commit=True):
        user = super().save(commit=False)
        if commit:
            user.save(update_fields=["password"])
        return user


def current_login_only(view, *others, audit=None):
    """비밀번호를 바꾸는 POST 는 **요청을 인증한 근거가 아직 유효할 때만** 처리한다: 요청한 사람의 행을 잠그고 세션의 인증 해시를 그 최신 행과 대조한 뒤
    같은 트랜잭션 안에서 처리한다 — 확인과 저장 사이에 다른 변경이 끼지 못한다. 서버 명령 `changepassword` 로 급히 끊은 뒤, 진행 중이던 요청이 서버가 정한
    비밀번호를 덮고 로그인을 이어 가던 것을 막는다(외부 검토 3-1d). 잠금을 쥔 동안 들어온 서버 명령은 기다렸다가 그 뒤에 적용된다(서버 명령이 이긴다).

    `others`: 이 요청이 바꿀 다른 사용자의 **해석이 끝난 PK**(사용자 관리의 초기화 대상 — 화면이 대상을 찾는 것과 같은 해석을 호출한 쪽이 한다).
    요청자와 함께 **ID 순서로 한 번에** 잠근다 — 두 관리자가 서로를 동시에 초기화해도 잠금 순서가 같아 교착 없이 줄을 서고, 뒤의 요청은 앞의 초기화로
    바뀐 자기 인증 근거 때문에 403 이다(요청자부터 잠그면 순서가 엇갈려 교착·500).

    `audit(request)`: 성공(리다이렉트)했을 때 남길 감사 기록. **변경과 같은 트랜잭션**에서 쓴다 — 기록이 실패하면 비밀번호도 되돌려진다
    (커밋 뒤에 쓰면 응답은 500 인데 비밀번호는 바뀌고 기록은 없었다, 외부 검토 00d3067)."""
    @wraps(view)
    def guarded(request, *args, **kwargs):
        if request.method != "POST":
            return view(request, *args, **kwargs)
        with transaction.atomic():
            locked = User.objects.select_for_update().filter(pk__in=[request.user.pk, *others]).order_by("pk")
            me = next((u for u in locked if u.pk == request.user.pk), None)
            if me is None or not me.is_active or not constant_time_compare(request.session.get(HASH_SESSION_KEY, ""), me.get_session_auth_hash()):
                raise PermissionDenied("로그인 정보가 바뀌었습니다. 다시 로그인하세요.")
            response = view(request, *args, **kwargs)
            if audit is not None and response.status_code == 302:
                audit(request)
            return response
    return guarded


class OwnPasswordChangeForm(PasswordOnlySave, OwnPasswordForm):
    """현재 비밀번호를 `authenticate(request, …)` 로 확인한다 — 로그인과 이 확인의 실패를 (아이디, IP)마다 함께 세고 5회면 5분 잠금(429, axes 미들웨어).
    잠금 중에는 맞는 비밀번호도 거절된다. Django 기본(`check_password`)은 계수 밖이라 세션을 탈취한 사람이 비밀번호를 끝없이 맞혀 볼 수 있었다(버그 탐색 B2).
    틀린 시도는 `account.password_change_rejected` 로 남는다 — 뷰의 트랜잭션(`current_login_only`)은 폼 오류(200)로 끝나면 커밋된다.
    그래서 실패 계수·거절 감사는 폼 오류 응답을 만든 뒤 예외(500)가 나면 함께 되돌려진다(성공 감사를 변경과 같은 트랜잭션에 두는 것과 같은 선택)."""

    def __init__(self, request, *args, **kwargs):
        self.request = request
        super().__init__(*args, **kwargs)

    @sensitive_variables("old_password")
    def clean_old_password(self):
        old_password = self.cleaned_data["old_password"]
        confirmed = authenticate(self.request, username=self.user.get_username(), password=old_password)
        if confirmed is None or confirmed.pk != self.user.pk:
            record_audit(self.request, "account.password_change_rejected", target=self.user, actor=self.user)
            raise ValidationError(self.error_messages["password_incorrect"], code="password_incorrect")
        return old_password


class AuditedAdminSite(admin.AdminSite):
    def password_change(self, request, extra_context=None):
        """Django `AdminSite.password_change` 를 그대로 옮기고 폼만 요청을 받게 했다(axes 계수에 요청이 필요한데 Django 뷰는 폼에 user 만 넘긴다).
        원본과 `form_class` 말고 같은지는 시험이 대조한다 — Django 를 올려 원본이 바뀌면 실패한다."""
        audit = lambda r: record_audit(r, "account.password_changed", target=r.user, actor=r.user)  # noqa: E731
        defaults = {
            "form_class": partial(OwnPasswordChangeForm, request),
            "success_url": reverse("admin:password_change_done", current_app=self.name),
            "extra_context": {**self.each_context(request), **(extra_context or {})},
        }
        if self.password_change_template is not None:
            defaults["template_name"] = self.password_change_template
        request.current_app = self.name
        return current_login_only(PasswordChangeView.as_view(**defaults), audit=audit)(request)
