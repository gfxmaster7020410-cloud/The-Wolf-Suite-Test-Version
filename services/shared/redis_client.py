"""
Shared Redis client with in-memory fallback for Docker-free local development.
Used by Module 4 (token bucket / Redlock) and Module 7 (retry scheduling).
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Dict, Optional


class _InMemoryRedis:
    """Minimal subset of Redis commands used by Wolf Suite services."""

    def __init__(self) -> None:
        self._kv: Dict[str, Any] = {}
        self._expiry: Dict[str, float] = {}
        self._lock = threading.Lock()

    def _purge(self, key: str) -> None:
        exp = self._expiry.get(key)
        if exp is not None and exp <= time.time():
            self._kv.pop(key, None)
            self._expiry.pop(key, None)

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            self._purge(key)
            val = self._kv.get(key)
            return None if val is None else str(val)

    def set(self, key: str, value: Any, ex: Optional[int] = None, nx: bool = False) -> bool:
        with self._lock:
            self._purge(key)
            if nx and key in self._kv:
                return False
            self._kv[key] = value
            if ex is not None:
                self._expiry[key] = time.time() + float(ex)
            else:
                self._expiry.pop(key, None)
            return True

    def delete(self, *keys: str) -> int:
        with self._lock:
            deleted = 0
            for key in keys:
                if key in self._kv:
                    deleted += 1
                self._kv.pop(key, None)
                self._expiry.pop(key, None)
            return deleted

    def eval(self, script: str, numkeys: int, *keys_and_args: Any) -> Any:
        # Support Redlock release: delete if value matches
        if numkeys >= 1 and ("redis.call" in script.lower() or "get" in script.lower()):
            key = str(keys_and_args[0])
            expected = str(keys_and_args[1]) if len(keys_and_args) > 1 else None
            with self._lock:
                self._purge(key)
                if expected is None or str(self._kv.get(key)) == expected:
                    existed = key in self._kv
                    self._kv.pop(key, None)
                    self._expiry.pop(key, None)
                    return 1 if existed else 0
                return 0
        return 0

    def ping(self) -> bool:
        return True


_client: Any = None
_backend: str = "uninitialized"
_lock = threading.Lock()


def get_redis() -> Any:
    """Return a Redis client, or in-memory shim when Redis is unavailable."""
    global _client, _backend
    with _lock:
        if _client is not None:
            return _client

        url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        force_memory = os.getenv("REDIS_FORCE_MEMORY", "false").lower() in {"1", "true", "yes"}
        if force_memory:
            _client = _InMemoryRedis()
            _backend = "memory"
            return _client

        try:
            import redis as redis_lib

            client = redis_lib.Redis.from_url(url, decode_responses=True, socket_connect_timeout=1.5)
            client.ping()
            _client = client
            _backend = "redis"
            return _client
        except Exception:
            _client = _InMemoryRedis()
            _backend = "memory"
            return _client


def redis_backend() -> str:
    get_redis()
    return _backend


def reset_redis_client() -> None:
    """Test helper to force re-resolution of the Redis backend."""
    global _client, _backend
    with _lock:
        _client = None
        _backend = "uninitialized"
