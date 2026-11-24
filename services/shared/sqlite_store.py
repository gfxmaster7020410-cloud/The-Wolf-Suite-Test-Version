"""
Local SQLite persistence for Project Wolf (Docker-free development).
Persists HITL tasks, kill-switch events/state, and Merkle audit records.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "wolf.db"


class WolfSQLiteStore:
    """Thread-safe SQLite store for governance and audit durability."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS hitl_tasks (
                        task_id TEXT PRIMARY KEY,
                        tenant_id TEXT NOT NULL,
                        account_id TEXT NOT NULL,
                        reason TEXT NOT NULL,
                        confidence REAL,
                        payload TEXT NOT NULL DEFAULT '{}',
                        status TEXT NOT NULL DEFAULT 'PENDING',
                        reviewed_by TEXT,
                        reviewed_at REAL,
                        created_at REAL NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS kill_switch_events (
                        event_id TEXT PRIMARY KEY,
                        level INTEGER NOT NULL,
                        tenant_id TEXT,
                        account_id TEXT,
                        reason TEXT NOT NULL,
                        issued_by TEXT NOT NULL,
                        issued_at REAL NOT NULL,
                        active INTEGER NOT NULL DEFAULT 1
                    );

                    CREATE TABLE IF NOT EXISTS kill_state (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS audit_events (
                        event_id TEXT PRIMARY KEY,
                        timestamp TEXT NOT NULL,
                        tenant_id TEXT NOT NULL,
                        actor TEXT NOT NULL,
                        action TEXT NOT NULL,
                        module TEXT NOT NULL,
                        payload_hash TEXT NOT NULL,
                        prev_block_hash TEXT NOT NULL,
                        block_hash TEXT NOT NULL,
                        created_at REAL NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_hitl_tenant_status
                        ON hitl_tasks(tenant_id, status);
                    CREATE INDEX IF NOT EXISTS idx_audit_created
                        ON audit_events(created_at);
                    """
                )
                conn.commit()
            finally:
                conn.close()

    # ------------------------------------------------------------------ HITL
    def upsert_hitl(self, task: Dict[str, Any]) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO hitl_tasks (
                        task_id, tenant_id, account_id, reason, confidence,
                        payload, status, reviewed_by, reviewed_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(task_id) DO UPDATE SET
                        status=excluded.status,
                        reviewed_by=excluded.reviewed_by,
                        reviewed_at=excluded.reviewed_at,
                        confidence=excluded.confidence,
                        payload=excluded.payload
                    """,
                    (
                        task["task_id"],
                        task["tenant_id"],
                        task["account_id"],
                        task["reason"] if isinstance(task["reason"], str) else str(task["reason"]),
                        task.get("confidence"),
                        json.dumps(task.get("payload") or {}),
                        task.get("status", "PENDING"),
                        task.get("reviewed_by"),
                        task.get("reviewed_at"),
                        task.get("created_at", time.time()),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def list_hitl(self, tenant_id: str, status: str = "PENDING") -> List[Dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    """
                    SELECT * FROM hitl_tasks
                    WHERE tenant_id = ? AND status = ?
                    ORDER BY created_at DESC
                    """,
                    (tenant_id, status),
                ).fetchall()
                return [self._row_hitl(r) for r in rows]
            finally:
                conn.close()

    def get_hitl(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM hitl_tasks WHERE task_id = ?", (task_id,)
                ).fetchone()
                return self._row_hitl(row) if row else None
            finally:
                conn.close()

    def load_all_hitl(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute("SELECT * FROM hitl_tasks").fetchall()
                return {r["task_id"]: self._row_hitl(r) for r in rows}
            finally:
                conn.close()

    @staticmethod
    def _row_hitl(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "task_id": row["task_id"],
            "tenant_id": row["tenant_id"],
            "account_id": row["account_id"],
            "reason": row["reason"],
            "confidence": row["confidence"],
            "payload": json.loads(row["payload"] or "{}"),
            "status": row["status"],
            "reviewed_by": row["reviewed_by"],
            "reviewed_at": row["reviewed_at"],
            "created_at": row["created_at"],
        }

    # ----------------------------------------------------------- Kill-switch
    def save_kill_event(self, event: Dict[str, Any]) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO kill_switch_events (
                        event_id, level, tenant_id, account_id, reason,
                        issued_by, issued_at, active
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(event_id) DO UPDATE SET active=excluded.active
                    """,
                    (
                        event["event_id"],
                        int(event["level"]),
                        event.get("tenant_id"),
                        event.get("account_id"),
                        event["reason"],
                        event["issued_by"],
                        event.get("issued_at", time.time()),
                        1 if event.get("active", True) else 0,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def list_kill_events(self) -> List[Dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT * FROM kill_switch_events ORDER BY issued_at ASC"
                ).fetchall()
                return [
                    {
                        "event_id": r["event_id"],
                        "level": r["level"],
                        "tenant_id": r["tenant_id"],
                        "account_id": r["account_id"],
                        "reason": r["reason"],
                        "issued_by": r["issued_by"],
                        "issued_at": r["issued_at"],
                        "active": bool(r["active"]),
                    }
                    for r in rows
                ]
            finally:
                conn.close()

    def save_kill_state(
        self,
        paused_accounts: set,
        paused_tenants: set,
        global_freeze: bool,
    ) -> None:
        payload = {
            "paused_accounts": sorted(paused_accounts),
            "paused_tenants": sorted(paused_tenants),
            "global_freeze": global_freeze,
        }
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO kill_state (key, value) VALUES ('active', ?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value
                    """,
                    (json.dumps(payload),),
                )
                conn.commit()
            finally:
                conn.close()

    def load_kill_state(self) -> Dict[str, Any]:
        with self._lock:
            return self._load_kill_state_unlocked()

    def _load_kill_state_unlocked(self) -> Dict[str, Any]:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT value FROM kill_state WHERE key = 'active'"
            ).fetchone()
            if not row:
                return {
                    "paused_accounts": set(),
                    "paused_tenants": set(),
                    "global_freeze": False,
                }
            data = json.loads(row["value"])
            return {
                "paused_accounts": set(data.get("paused_accounts") or []),
                "paused_tenants": set(data.get("paused_tenants") or []),
                "global_freeze": bool(data.get("global_freeze")),
            }
        finally:
            conn.close()

    # --------------------------------------------------------------- Audit
    def append_audit(self, record: Dict[str, Any]) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO audit_events (
                        event_id, timestamp, tenant_id, actor, action, module,
                        payload_hash, prev_block_hash, block_hash, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record["event_id"],
                        record["timestamp"],
                        record["tenant_id"],
                        record["actor"],
                        record["action"],
                        record["module"],
                        record["payload_hash"],
                        record["prev_block_hash"],
                        record["block_hash"],
                        time.time(),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def list_audit(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    """
                    SELECT event_id, timestamp, tenant_id, actor, action, module,
                           payload_hash, prev_block_hash, block_hash
                    FROM audit_events
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    def audit_count(self) -> int:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute("SELECT COUNT(*) AS c FROM audit_events").fetchone()
                return int(row["c"])
            finally:
                conn.close()

    def dashboard_stats(self, tenant_id: str) -> Dict[str, Any]:
        with self._lock:
            conn = self._connect()
            try:
                pending = conn.execute(
                    "SELECT COUNT(*) AS c FROM hitl_tasks WHERE tenant_id=? AND status='PENDING'",
                    (tenant_id,),
                ).fetchone()["c"]
                kills = conn.execute(
                    "SELECT COUNT(*) AS c FROM kill_switch_events WHERE active=1"
                ).fetchone()["c"]
                audits = conn.execute("SELECT COUNT(*) AS c FROM audit_events").fetchone()["c"]
                state = self._load_kill_state_unlocked()
                return {
                    "pending_hitl": pending,
                    "active_kill_events": kills,
                    "audit_events": audits,
                    "paused_accounts": len(state["paused_accounts"]),
                    "paused_tenants": len(state["paused_tenants"]),
                    "global_freeze": state["global_freeze"],
                    "db_path": str(self.db_path),
                }
            finally:
                conn.close()


# Process-wide singleton
store = WolfSQLiteStore()
