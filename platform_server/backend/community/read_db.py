"""Explicit replica reads with a short read-your-writes window and primary fallback."""
import logging

from django.conf import settings
from django.db import connections, InterfaceError, OperationalError

ALIAS = "community_read"
COOKIE = "community_recent_write"
SALT = "community.read-your-writes.v1"
PIN_SECONDS = 10
READ_VIEWS = {"metadata", "posts", "comments"}
logger = logging.getLogger(__name__)


def read_database(request):
    if (ALIAS not in settings.DATABASES or request.method != "GET"
            or getattr(request, "_community_primary", False)
            or connections["default"].in_atomic_block):
        return "default"
    recent_user = request.get_signed_cookie(COOKIE, default=None, salt=SALT, max_age=PIN_SECONDS)
    return "default" if recent_user == str(request.user.pk) else ALIAS


def call_view(view, request, *args, **kwargs):
    # Only these GET handlers are free of business writes. Detail views record read events.
    if request.method == "GET" and view.__name__ in READ_VIEWS:
        try:
            return view(request, *args, **kwargs)
        except (InterfaceError, OperationalError):
            if read_database(request) != ALIAS:
                raise
            connections[ALIAS].close()
            request._community_primary = True
            logger.warning("Community replica unavailable; retrying read on primary")
            return view(request, *args, **kwargs)
    response = view(request, *args, **kwargs)
    if request.method in {"POST", "PATCH", "DELETE"} and 200 <= response.status_code < 300:
        response.set_signed_cookie(COOKIE, str(request.user.pk), salt=SALT, max_age=PIN_SECONDS,
                                   httponly=True, secure=request.is_secure(), samesite="Lax",
                                   path="/api/community/")
    return response
