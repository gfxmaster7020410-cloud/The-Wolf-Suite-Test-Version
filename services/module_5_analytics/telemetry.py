"""
Module 5: Unified Analytics, System Observability & AI Telemetry.

Spec §7:
  - Account Safety Score Sa = 100 - (w1·Cfails + w2·Perrors + w3·Rthrottles)
  - CPL = (LLM + Proxy + Infra costs) / verified leads
  - Circuit breakers: CAPTCHA, proxy pool, 429 cooling, token-cost anomaly
  - Cost metering ledger (SQLite locally; Postgres schema in db/schema.sql)
"""

from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional


DEFAULT_METERING_DB = Path(__file__).resolve().parents[2] / "data" / "wolf.db"


class CostMeteringLedger:
    """Append-only usage cost ledger (SQLite-compatible with schema.sql)."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else DEFAULT_METERING_DB
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS cost_metering_ledger (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        tenant_id TEXT NOT NULL,
                        cost_center_id TEXT NOT NULL,
                        module_name TEXT NOT NULL,
                        resource_type TEXT NOT NULL,
                        units_consumed REAL NOT NULL,
                        calculated_cost_usd REAL NOT NULL,
                        created_at REAL NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS telemetry_incidents (
                        incident_id TEXT PRIMARY KEY,
                        tenant_id TEXT NOT NULL,
                        account_id TEXT,
                        trigger_name TEXT NOT NULL,
                        action TEXT NOT NULL,
                        detail TEXT NOT NULL,
                        created_at REAL NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS cooling_windows (
                        account_id TEXT PRIMARY KEY,
                        multiplier REAL NOT NULL DEFAULT 1.0,
                        consecutive_429 INTEGER NOT NULL DEFAULT 0,
                        updated_at REAL NOT NULL
                    )
                    """
                )
                conn.commit()
            finally:
                conn.close()

    def record(
        self,
        tenant_id: str,
        cost_center_id: str,
        module_name: str,
        resource_type: str,
        units_consumed: float,
        calculated_cost_usd: float,
    ) -> Dict[str, Any]:
        row = {
            "tenant_id": tenant_id,
            "cost_center_id": cost_center_id,
            "module_name": module_name,
            "resource_type": resource_type,
            "units_consumed": float(units_consumed),
            "calculated_cost_usd": float(calculated_cost_usd),
            "created_at": time.time(),
        }
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    """
                    INSERT INTO cost_metering_ledger (
                        tenant_id, cost_center_id, module_name, resource_type,
                        units_consumed, calculated_cost_usd, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["tenant_id"],
                        row["cost_center_id"],
                        row["module_name"],
                        row["resource_type"],
                        row["units_consumed"],
                        row["calculated_cost_usd"],
                        row["created_at"],
                    ),
                )
                conn.commit()
                row["id"] = cur.lastrowid
            finally:
                conn.close()
        return row

    def sum_costs(self, tenant_id: str, since_ts: Optional[float] = None) -> Dict[str, float]:
        with self._lock:
            conn = self._connect()
            try:
                if since_ts:
                    rows = conn.execute(
                        """
                        SELECT resource_type, SUM(calculated_cost_usd) AS total
                        FROM cost_metering_ledger
                        WHERE tenant_id = ? AND created_at >= ?
                        GROUP BY resource_type
                        """,
                        (tenant_id, since_ts),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        SELECT resource_type, SUM(calculated_cost_usd) AS total
                        FROM cost_metering_ledger
                        WHERE tenant_id = ?
                        GROUP BY resource_type
                        """,
                        (tenant_id,),
                    ).fetchall()
                return {r["resource_type"]: float(r["total"] or 0) for r in rows}
            finally:
                conn.close()

    def log_incident(self, tenant_id: str, account_id: Optional[str], trigger: str, action: str, detail: str) -> str:
        incident_id = f"inc_{uuid.uuid4().hex[:10]}"
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO telemetry_incidents (
                        incident_id, tenant_id, account_id, trigger_name, action, detail, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (incident_id, tenant_id, account_id, trigger, action, detail, time.time()),
                )
                conn.commit()
            finally:
                conn.close()
        return incident_id

    def get_cooling(self, account_id: str) -> Dict[str, Any]:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM cooling_windows WHERE account_id = ?",
                    (account_id,),
                ).fetchone()
                if not row:
                    return {"account_id": account_id, "multiplier": 1.0, "consecutive_429": 0}
                return dict(row)
            finally:
                conn.close()

    def set_cooling(self, account_id: str, multiplier: float, consecutive_429: int) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO cooling_windows (account_id, multiplier, consecutive_429, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(account_id) DO UPDATE SET
                        multiplier = excluded.multiplier,
                        consecutive_429 = excluded.consecutive_429,
                        updated_at = excluded.updated_at
                    """,
                    (account_id, float(multiplier), int(consecutive_429), time.time()),
                )
                conn.commit()
            finally:
                conn.close()


class Module5TelemetryEngine:
    """
    Safety scores, CPL, circuit breakers, and cost metering.
    """

    TOKEN_COST_ANOMALY_USD = 2.0

    def __init__(
        self,
        w1: float = 15.0,
        w2: float = 10.0,
        w3: float = 5.0,
        ledger: Optional[CostMeteringLedger] = None,
    ):
        self.w1 = w1  # Captcha failures
        self.w2 = w2  # Proxy errors
        self.w3 = w3  # Rate throttles
        self.ledger = ledger or CostMeteringLedger()
        self._events: List[Dict[str, Any]] = []

    def calculate_account_safety_score(
        self,
        captcha_failures: int,
        proxy_errors: int,
        rate_limit_throttles: int,
    ) -> float:
        """Sa = 100 - (w1·Cfails + w2·Perrors + w3·Rthrottles), clamped [0, 100]."""
        penalty = (
            self.w1 * captcha_failures
            + self.w2 * proxy_errors
            + self.w3 * rate_limit_throttles
        )
        return max(0.0, min(100.0, 100.0 - penalty))

    def calculate_cost_per_lead(
        self,
        total_llm_costs: float,
        total_proxy_costs: float,
        total_infrastructure_costs: float,
        verified_leads_count: int,
    ) -> float:
        if verified_leads_count <= 0:
            return 0.0
        total = total_llm_costs + total_proxy_costs + total_infrastructure_costs
        return round(total / verified_leads_count, 4)

    def record_cost(
        self,
        tenant_id: str,
        cost_center_id: str,
        module_name: str,
        resource_type: str,
        units_consumed: float,
        calculated_cost_usd: float,
    ) -> Dict[str, Any]:
        return self.ledger.record(
            tenant_id=tenant_id,
            cost_center_id=cost_center_id,
            module_name=module_name,
            resource_type=resource_type,
            units_consumed=units_consumed,
            calculated_cost_usd=calculated_cost_usd,
        )

    def cpl_from_ledger(self, tenant_id: str, verified_leads_count: int) -> float:
        sums = self.ledger.sum_costs(tenant_id)
        return self.calculate_cost_per_lead(
            total_llm_costs=sums.get("llm", 0.0),
            total_proxy_costs=sums.get("proxy", 0.0),
            total_infrastructure_costs=sums.get("infrastructure", 0.0),
            verified_leads_count=verified_leads_count,
        )

    def record_http_status(self, account_id: str, status_code: int) -> Dict[str, Any]:
        """Track consecutive 429s and extend cooling window by 200% at threshold."""
        state = self.ledger.get_cooling(account_id)
        consecutive = int(state.get("consecutive_429") or 0)
        multiplier = float(state.get("multiplier") or 1.0)

        if int(status_code) == 429:
            consecutive += 1
        else:
            consecutive = 0

        triggered = False
        if consecutive >= 3:
            multiplier *= 3.0  # +200% ⇒ 3× original cooling window
            triggered = True
            consecutive = 0  # reset streak after applying extension

        self.ledger.set_cooling(account_id, multiplier=multiplier, consecutive_429=consecutive)
        return {
            "account_id": account_id,
            "status_code": status_code,
            "consecutive_429": consecutive,
            "cooling_multiplier": multiplier,
            "rate_limit_breaker": triggered,
        }

    def check_circuit_breakers(
        self,
        captcha_failures_15m: int = 0,
        proxy_failure_rate: float = 0.0,
        consecutive_429: int = 0,
        single_task_cost_usd: float = 0.0,
        account_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Incident matrix (Spec §7.2):
          - >2 Captchas / 15m → PAUSE_PROFILE_DISPATCH
          - >20% proxy failures → ROTATE_PROXY_SUBNET_AND_THROTTLE
          - ≥3 consecutive HTTP 429s → EXTEND_COOLING_WINDOW_200PCT
          - Single task cost > $2.00 → TERMINATE_THREAD_AND_FLAG
        """
        actions: List[Dict[str, Any]] = []

        if captcha_failures_15m > 2:
            actions.append(
                {
                    "action": "PAUSE_PROFILE_DISPATCH",
                    "trigger": "Consecutive Challenge",
                    "reason": ">2 CAPTCHA failures in 15m",
                    "alert_channel": "Telegram Urgent Alert",
                }
            )

        if proxy_failure_rate > 0.20:
            actions.append(
                {
                    "action": "ROTATE_PROXY_SUBNET_AND_THROTTLE",
                    "trigger": "Proxy Pool Failures",
                    "reason": ">20% proxy failure rate",
                    "alert_channel": "Slack Ops Channel",
                }
            )

        # Prefer live cooling tracker when account_id provided
        if account_id:
            cool = self.ledger.get_cooling(account_id)
            consecutive_429 = max(consecutive_429, int(cool.get("consecutive_429") or 0))

        if consecutive_429 >= 3:
            actions.append(
                {
                    "action": "EXTEND_COOLING_WINDOW_200PCT",
                    "trigger": "Rate-Limit Threshold",
                    "reason": ">=3 consecutive HTTP 429s",
                    "alert_channel": "Dashboard Incident Log",
                    "cooling_multiplier": (
                        float(self.ledger.get_cooling(account_id).get("multiplier") or 1.0) * 3.0
                        if account_id
                        else 3.0
                    ),
                }
            )

        if single_task_cost_usd > self.TOKEN_COST_ANOMALY_USD:
            actions.append(
                {
                    "action": "TERMINATE_THREAD_AND_FLAG",
                    "trigger": "Token Cost Anomaly",
                    "reason": f"Single task cost ${single_task_cost_usd:.2f} > $2.00 USD",
                    "alert_channel": "Email / Slack Alert",
                    "flag_payload": True,
                }
            )

        return {
            "circuit_breaker_triggered": len(actions) > 0,
            "actions": actions,
        }

    def evaluate_and_meter(
        self,
        tenant_id: str,
        account_id: str,
        captcha_failures: int = 0,
        proxy_errors: int = 0,
        rate_limit_throttles: int = 0,
        captcha_failures_15m: int = 0,
        proxy_failure_rate: float = 0.0,
        consecutive_429: int = 0,
        single_task_cost_usd: float = 0.0,
        cost_center_id: str = "default",
    ) -> Dict[str, Any]:
        """Composite telemetry snapshot used by suite / safety bridge."""
        safety = self.calculate_account_safety_score(
            captcha_failures, proxy_errors, rate_limit_throttles
        )
        breakers = self.check_circuit_breakers(
            captcha_failures_15m=captcha_failures_15m or captcha_failures,
            proxy_failure_rate=proxy_failure_rate,
            consecutive_429=consecutive_429,
            single_task_cost_usd=single_task_cost_usd,
            account_id=account_id,
        )

        if single_task_cost_usd > 0:
            self.record_cost(
                tenant_id=tenant_id,
                cost_center_id=cost_center_id,
                module_name="runtime",
                resource_type="llm",
                units_consumed=1.0,
                calculated_cost_usd=single_task_cost_usd,
            )

        for action in breakers.get("actions", []):
            self.ledger.log_incident(
                tenant_id=tenant_id,
                account_id=account_id,
                trigger=action.get("trigger", "unknown"),
                action=action["action"],
                detail=action.get("reason", ""),
            )
            if action["action"] == "EXTEND_COOLING_WINDOW_200PCT" and account_id:
                cool = self.ledger.get_cooling(account_id)
                self.ledger.set_cooling(
                    account_id,
                    multiplier=float(cool.get("multiplier") or 1.0) * 3.0,
                    consecutive_429=0,
                )

        snapshot = {
            "safety_score": safety,
            "circuit_breakers": breakers,
            "cooling": self.ledger.get_cooling(account_id) if account_id else None,
            "costs": self.ledger.sum_costs(tenant_id),
        }
        self._events.append({"ts": time.time(), **snapshot})
        return snapshot
