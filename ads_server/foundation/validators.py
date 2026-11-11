"""입력 규칙(계정·비밀번호·UUID·요청 키·금액·서비스 문자열). 규칙은 **여기에만** 둔다(폼은 입력 변환만 한다, 설계 §6.6)."""
import re
import unicodedata
import uuid

from django.core.exceptions import ValidationError

from .errors import InvalidRequest, NotFound

USERNAME_RE = re.compile(r"[a-z0-9_]{4,20}")
UUID_TEXT = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")  # 계약 `Uuid`(하이픈 36자만, C1·C8 같은 패턴). `fullmatch` 로 쓴다
MAX_AMOUNT = 2**53 - 1  # 포인트 금액의 최대값. JSON(JavaScript) 정수로 정확히 표현되는 한계(계약 `Money`와 같다)


def is_int(value):
    """정수인가. **bool 은 아니다**(파이썬에서는 `True`·`False` 도 `int` 라서 `isinstance` 만으로는 1·0 으로 통과한다)."""
    return isinstance(value, int) and not isinstance(value, bool)


def validate_username(value):
    if not USERNAME_RE.fullmatch(value or ""):
        raise ValidationError("아이디는 영문 소문자·숫자·밑줄(_) 4~20자여야 합니다.", code="invalid_username")
    return value


def _has_control_or_format_chars(value):
    # 제어 문자와 보이지 않는 서식 문자(제로폭 공백 등)를 막는다.
    return any(unicodedata.category(ch).startswith("C") for ch in value)


def _clean_text(value, label, min_len, max_len):
    value = unicodedata.normalize("NFC", (value or "").strip())
    if _has_control_or_format_chars(value):
        raise ValidationError(f"{label}에 사용할 수 없는 문자가 있습니다.", code="invalid_chars")
    if not (min_len <= len(value) <= max_len):
        raise ValidationError(f"{label}은(는) {min_len}~{max_len}자여야 합니다.", code="invalid_length")
    return value


def clean_nickname(value):
    return _clean_text(value, "닉네임", 2, 20)


def clean_class_label(value):
    return _clean_text(value, "반", 1, 30)


def parse_uuid(value, what):
    """UUID 로 바꾼다. 형식이 틀린 값은 '없는 것'과 같게 404 로 취급한다(존재 여부를 알리지 않는다)."""
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        raise NotFound(f"{what}을(를) 찾을 수 없습니다.")


def parse_uuids(values):
    """UUID 로 읽히는 값만 UUID 로 돌려준다. 형식이 틀린 값은 없는 ID 와 같아 버린다(여러 ID 를 한 번에 조회할 때)."""
    valid = []
    for value in values:
        try:
            valid.append(parse_uuid(value, "ID"))
        except NotFound:
            pass
    return valid


def clean_text(value, label, pointer, max_len=100):
    """서비스 입력용 문자열 검증(1~max_len자, 보이지 않는 서식·제어 문자 금지). 틀리면 400 `InvalidRequest`."""
    value = unicodedata.normalize("NFC", value.strip()) if isinstance(value, str) else ""
    if not value or len(value) > max_len or _has_control_or_format_chars(value):
        raise InvalidRequest(errors=[{"pointer": pointer, "message": f"{label}은(는) 1~{max_len}자의 보이는 문자여야 합니다."}])
    return value


_REQUEST_KEY = re.compile(r"[A-Za-z0-9_-]{1,64}")  # 화면이 만드는 `secrets.token_urlsafe` 의 글자, 저장하는 열의 길이


def is_request_key(value):
    """두 번 하면 안 되는 동작의 요청 키(화면 폼 하나마다 새 값) 형식: 영문·숫자·`-`·`_` 1~64자.
    화면은 폼 검증 전에 POST 원문 키로 조회하므로, 여기서 NUL 같은 글자가 걸러지지 않으면 DB 오류(500)가 된다."""
    return isinstance(value, str) and _REQUEST_KEY.fullmatch(value) is not None


def clean_request_key(value):
    """요청 키 검증. 없으면(`None`) 그대로, 형식이 틀리면 400 `InvalidRequest`(`/request_key`)."""
    if value is not None and not is_request_key(value):
        raise InvalidRequest(errors=[{"pointer": "/request_key", "message": "영문·숫자·-·_ 1~64자여야 합니다."}])
    return value


class MaximumLengthValidator:
    """비밀번호 최대 길이(C6 `maxLength`). Django 에는 최소 길이 검증기만 있다 — 화면 폼의 `max_length` 만으로는 서비스·서버 명령이 더 긴 비밀번호를 받았다."""

    def __init__(self, max_length=128):
        self.max_length = max_length

    def validate(self, password, user=None):
        if len(password) > self.max_length:
            raise ValidationError(f"비밀번호는 {self.max_length}자 이하여야 합니다.", code="password_too_long")

    def get_help_text(self):
        return f"비밀번호는 {self.max_length}자 이하여야 합니다."
