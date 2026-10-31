from __future__ import annotations

import hashlib
import hmac
import time
from collections import OrderedDict


def compute_hmac_sha256(secret: str, timestamp: str, raw_body: bytes) -> str:
    """Compute canonical `v1=<hex>` HMAC-SHA256 over `<timestamp>.<raw_body>`."""
    if not secret:
        raise ValueError("Webhook signing secret must not be empty")
    signing_input = timestamp.encode("utf-8") + b"." + raw_body
    digest = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).hexdigest()
    return f"v1={digest}"


def verify_lindy_webhook_signature(
    *,
    secret: str,
    signature_header: str,
    timestamp_header: str,
    raw_body: bytes,
    max_skew_seconds: int = 300,
    now_epoch: float | None = None,
) -> tuple[bool, str]:
    """Verify HMAC-SHA256 signature and timestamp freshness for Lindy AI webhook calls."""
    if not secret:
        return False, "missing_server_secret"
    if not signature_header or not timestamp_header:
        return False, "missing_signature_headers"

    try:
        ts_val = int(timestamp_header.strip())
    except ValueError:
        return False, "invalid_timestamp_header"

    current = time.time() if now_epoch is None else float(now_epoch)
    if abs(current - ts_val) > max_skew_seconds:
        return False, "expired_timestamp_window"

    expected = compute_hmac_sha256(secret, str(ts_val), raw_body)
    provided = signature_header.strip()
    if not provided.startswith("v1="):
        provided = f"v1={provided}"

    if not hmac.compare_digest(expected, provided):
        return False, "invalid_hmac_signature"
    return True, "ok"


class BoundedIdempotencyStore:
    """Memory-bounded (<1MB) LRU TTL idempotency cache with optional Render KV URL metadata."""

    def __init__(self, max_entries: int = 2048, ttl_seconds: int = 86400, kv_url: str | None = None) -> None:
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self.kv_url = kv_url or "memory://local-lru"
        self._entries: OrderedDict[str, tuple[float, dict]] = OrderedDict()

    def get(self, key: str, now_epoch: float | None = None) -> dict | None:
        now = time.time() if now_epoch is None else float(now_epoch)
        entry = self._entries.get(key)
        if entry is None:
            return None
        expires_at, cached_payload = entry
        if now >= expires_at:
            self._entries.pop(key, None)
            return None
        self._entries.move_to_end(key)
        return cached_payload

    def put(self, key: str, payload: dict, now_epoch: float | None = None) -> None:
        now = time.time() if now_epoch is None else float(now_epoch)
        self._entries[key] = (now + self.ttl_seconds, payload)
        self._entries.move_to_end(key)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)

    def size(self) -> int:
        return len(self._entries)
