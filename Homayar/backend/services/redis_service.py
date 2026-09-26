"""Redis service — blacklist, caching, job status."""
import json
import time
from typing import Optional

from backend.services.logging_service import logger
from backend.config import get_settings


def _get_redis():
    import redis
    settings = get_settings()
    return redis.from_url(settings.redis_url)


# ─── Token Blacklist ────────────────────────────────────────

def blacklist_token(token: str, ttl_seconds: int = 86400 * 7):
    """Add token to Redis blacklist with TTL."""
    try:
        r = _get_redis()
        r.setex(f"homayar:blacklist:{token}", ttl_seconds, "1")
        logger.info(f"TOKEN_BLACKLISTED prefix={token[:12]}...")
    except Exception as e:
        logger.error(f"TOKEN_BLACKLIST_FAIL error='{e}'")


def is_token_blacklisted(token: str) -> bool:
    """Check if token is in Redis blacklist."""
    try:
        r = _get_redis()
        return r.exists(f"homayar:blacklist:{token}") > 0
    except Exception:
        return False


# ─── Job Status ──────────────────────────────────────────────

def set_job_status(call_id: str, status: str, progress: int, detail: str = "", step: str = ""):
    """Store job status in Redis."""
    try:
        r = _get_redis()
        r.setex(
            f"homayar:job:{call_id}",
            86400,
            json.dumps({
                "call_id": call_id,
                "status": status,
                "progress": progress,
                "detail": detail,
                "step": step,
                "timestamp": time.time(),
            }),
        )
    except Exception as e:
        logger.warning(f"JOB_STATUS_FAIL call_id={call_id} error='{e}'")


def get_job_status(call_id: str) -> Optional[dict]:
    """Get job status from Redis."""
    try:
        r = _get_redis()
        data = r.get(f"homayar:job:{call_id}")
        if data:
            return json.loads(data)
    except Exception:
        pass
    return {"call_id": call_id, "status": "unknown", "progress": 0}
