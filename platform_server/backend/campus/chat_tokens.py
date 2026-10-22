"""Bind public chat to an avatar without reading movement state in the chat process."""
from hashlib import sha256
from hmac import compare_digest
import re

from django.conf import settings
from django.core import signing

from eventing.catalog import FLOORS

SALT = "campus.chat.actor.v1"


def issue_actor_token(user_id, session_key, actor_id, floor):
    return signing.dumps({"user": str(user_id), "session": sha256(session_key.encode()).hexdigest(),
                          "actorId": actor_id, "floor": floor}, salt=SALT)


def verify_actor_token(token, user_id, session_key):
    if not isinstance(token, str) or len(token) > 1024:
        raise signing.BadSignature("Invalid actor token")
    data = signing.loads(token, salt=SALT, max_age=settings.CAMPUS_LOGIN_MAX_AGE_SECONDS)
    if (not isinstance(data, dict) or data.get("user") != str(user_id)
            or not isinstance(data.get("session"), str)
            or not compare_digest(data["session"], sha256(session_key.encode()).hexdigest())
            or not isinstance(data.get("actorId"), str) or not re.fullmatch(r"[0-9a-f]{32}", data["actorId"])
            or not isinstance(data.get("floor"), str) or data["floor"] not in dict(FLOORS)):
        raise signing.BadSignature("Actor token does not match this session")
    return data
