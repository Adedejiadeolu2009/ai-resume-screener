import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import HTTPException, Request


_hits: dict[str, deque[float]] = defaultdict(deque)
_lock = Lock()


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"


def enforce_rate_limit(request: Request, key: str, limit: int, window_seconds: int) -> None:
    now = time.monotonic()
    bucket_key = f"{key}:{client_ip(request)}"
    with _lock:
        bucket = _hits[bucket_key]
        while bucket and now - bucket[0] > window_seconds:
            bucket.popleft()
        if len(bucket) >= limit:
            raise HTTPException(
                status_code=429,
                detail="Too many requests. Please wait a moment and try again.",
            )
        bucket.append(now)
