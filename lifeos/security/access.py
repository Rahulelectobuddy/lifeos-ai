import hmac
from collections import defaultdict, deque
from time import monotonic

from fastapi import Header, HTTPException

from lifeos.core.config import get_settings


class RateLimiter:
    """Per-process API limiter; durable run submission adds a database-level limit."""

    def __init__(self, limit: int):
        self.limit = limit
        self.hits = defaultdict(deque)

    def check(self, user_id: int):
        now = monotonic()
        hits = self.hits[user_id]
        while hits and hits[0] < now - 60:
            hits.popleft()
        if len(hits) >= self.limit:
            raise ValueError("Rate limit exceeded")
        hits.append(now)


async def api_identity(
    authorization: str = Header(default=""), x_zulip_user_id: int = Header(default=0)
):
    settings = get_settings()
    expected = settings.api_token.get_secret_value()
    if not expected or not hmac.compare_digest(authorization, "Bearer " + expected):
        raise HTTPException(401, "Unauthorized")
    if x_zulip_user_id not in settings.allowed_user_ids:
        raise HTTPException(403, "User not allowed")
    return x_zulip_user_id
