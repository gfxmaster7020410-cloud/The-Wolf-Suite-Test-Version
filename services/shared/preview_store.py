"""
In-memory live preview store for Wolf Suite web UI.
Modules push step updates here; Control Center polls / streams them.
"""

from __future__ import annotations

import threading
import time
import uuid
from contextvars import ContextVar
from typing import Any, Dict, List, Optional

# Active web preview session for the current async task
active_preview_session_id: ContextVar[Optional[str]] = ContextVar(
    "wolf_preview_sid", default=None
)


class LivePreviewStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: Dict[str, Dict[str, Any]] = {}

    def start(
        self,
        module_id: str,
        title: str,
        subtitle: str,
        step_names: List[str],
        cards: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        sid = f"prev_{uuid.uuid4().hex[:10]}"
        with self._lock:
            self._sessions[sid] = {
                "session_id": sid,
                "module_id": module_id,
                "title": title,
                "subtitle": subtitle,
                "status": "running",
                "cards": cards or [],
                "steps": [
                    {"name": n, "detail": "Waiting…", "status": "pending"} for n in step_names
                ],
                "result": None,
                "updated_at": time.time(),
                "created_at": time.time(),
            }
            # Keep only last 12 sessions
            if len(self._sessions) > 12:
                oldest = sorted(self._sessions.values(), key=lambda s: s["created_at"])[:3]
                for o in oldest:
                    self._sessions.pop(o["session_id"], None)
        return sid

    def set_step(
        self,
        session_id: str,
        index: int,
        *,
        status: str = "running",
        detail: Optional[str] = None,
    ) -> None:
        with self._lock:
            sess = self._sessions.get(session_id)
            if not sess or index < 0 or index >= len(sess["steps"]):
                return
            if detail is not None:
                sess["steps"][index]["detail"] = detail
            sess["steps"][index]["status"] = status
            if status == "running":
                for i in range(index):
                    if sess["steps"][i]["status"] in {"pending", "running"}:
                        sess["steps"][i]["status"] = "completed"
            sess["updated_at"] = time.time()

    def update_cards(self, session_id: str, cards: List[Dict[str, Any]]) -> None:
        with self._lock:
            sess = self._sessions.get(session_id)
            if not sess:
                return
            sess["cards"] = cards
            sess["updated_at"] = time.time()

    def complete(self, session_id: str, result: Optional[Dict[str, Any]] = None) -> None:
        with self._lock:
            sess = self._sessions.get(session_id)
            if not sess:
                return
            for st in sess["steps"]:
                if st["status"] in {"pending", "running"}:
                    st["status"] = "completed"
            sess["status"] = "completed"
            sess["result"] = result
            sess["subtitle"] = "Execution complete — review results"
            sess["updated_at"] = time.time()

    def fail(self, session_id: str, error: str) -> None:
        with self._lock:
            sess = self._sessions.get(session_id)
            if not sess:
                return
            for st in sess["steps"]:
                if st["status"] == "running":
                    st["status"] = "failed"
                    st["detail"] = error[:200]
                    break
            sess["status"] = "failed"
            sess["subtitle"] = error[:160]
            sess["updated_at"] = time.time()

    def get(self, session_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            sess = self._sessions.get(session_id)
            return dict(sess) if sess else None

    def latest_for_module(self, module_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            matches = [s for s in self._sessions.values() if s["module_id"] == module_id]
            if not matches:
                return None
            return dict(sorted(matches, key=lambda s: s["updated_at"])[-1])

    def latest(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            if not self._sessions:
                return None
            return dict(sorted(self._sessions.values(), key=lambda s: s["updated_at"])[-1])


preview_store = LivePreviewStore()
