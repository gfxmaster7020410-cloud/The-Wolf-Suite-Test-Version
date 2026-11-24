"""
Module 1 — Seen-posts store (skip already-commented duplicates).

Thread-safe, JSON-backed, bounded. The worker records every post URL it
acted on so a later run never engages the same post twice.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Optional

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "data" / "module1_seen.json"


class SeenStore:
    """
    Thread-safe, JSON-backed, bounded memory of engaged posts.

    PHASE-09 (decision D14): entries older than `expiry_days` stop blocking
    and are purged, so old posts become re-engageable. expiry_days=0 keeps
    pure legacy semantics (never expire; only max_entries count-trims).
    """

    def __init__(
        self,
        path: Optional[Path] = None,
        max_entries: int = 5000,
        expiry_days: int = 0,
    ):
        self._path = Path(path) if path else DEFAULT_PATH
        self._max = max(0, int(max_entries))
        self._expiry_sec = max(0, int(expiry_days)) * 86400
        self._lock = threading.Lock()
        self._urls: dict[str, float] = {}
        self._load()

    # ------------------------------------------------------------------ io
    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._urls = {k: float(v) for k, v in data.items() if isinstance(k, str)}
            elif isinstance(data, list):
                self._urls = {str(u): time.time() for u in data}
        except (json.JSONDecodeError, ValueError, OSError) as exc:
            print(f"[M1 Seen] Failed to load {self._path}: {exc}")

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self._urls, indent=1, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            print(f"[M1 Seen] Failed to save {self._path}: {exc}")

    # ------------------------------------------------------------------ api
    def _expired(self, ts: float) -> bool:
        # Strict '>' so an entry is honored through its full window; future
        # timestamps (clock skew / hand edits) always read as fresh.
        return self._expiry_sec > 0 and (time.time() - ts) > self._expiry_sec

    def contains(self, url: str) -> bool:
        with self._lock:
            ts = self._urls.get(url or "")
            if ts is None:
                return False
            if self._expired(ts):
                # Belt-and-braces for processes that never restarted: an
                # expired entry is treated as absent and dropped lazily.
                del self._urls[url or ""]
                self._save()
                return False
            return True

    def purge(self) -> int:
        """Delete every expired entry; returns how many were removed."""
        with self._lock:
            dead = [u for u, t in self._urls.items() if self._expired(t)]
            for u in dead:
                del self._urls[u]
            if dead:
                self._save()
            return len(dead)

    def add(self, url: str) -> None:
        if not url:
            return
        with self._lock:
            self._urls[url] = time.time()
            if self._max and len(self._urls) > self._max:
                # Keep the most recently seen entries (runs after any expiry
                # handling so both invariants hold).
                cutoff = sorted(self._urls.values())[: len(self._urls) - self._max][-1]
                self._urls = {k: v for k, v in self._urls.items() if v >= cutoff}
            self._save()

    def count(self) -> int:
        with self._lock:
            return len(self._urls)

    def clear(self) -> int:
        with self._lock:
            n = len(self._urls)
            self._urls = {}
            self._save()
            return n

    def reset_for(self, path: Optional[Path] = None, max_entries: Optional[int] = None) -> None:
        """Rebind to a new store member (called by the worker on each run)."""
        if path is not None:
            self._path = Path(path)
        if max_entries is not None:
            self._max = max(0, int(max_entries))
        with self._lock:
            self._urls = {}
        self._load()


seen_store = SeenStore()