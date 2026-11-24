"""
Cross-module safety bridge:
Module 5 circuit breakers → Module 6 kill-switch
Module 4 dispatch → Module 6 allow-check
All sensitive actions → Merkle audit trail
"""

from typing import Any, Dict, Optional

from services.module_5_analytics.telemetry import Module5TelemetryEngine
from services.module_6_control_center.governance import (
    KillLevel,
    KillSwitchRequest,
    Module6ControlCenter,
    OperatorSession,
    Role,
    control_center,
)
from services.security_governance.siem_exporter import MerkleAuditLogger


class SafetyBridge:
    """Single integration point for kill-switch, circuit breakers, and audit."""

    def __init__(
        self,
        governance: Optional[Module6ControlCenter] = None,
        telemetry: Optional[Module5TelemetryEngine] = None,
        audit: Optional[MerkleAuditLogger] = None,
    ):
        self.governance = governance or control_center
        self.telemetry = telemetry or Module5TelemetryEngine()
        self.audit = audit or MerkleAuditLogger()

    def assert_dispatch_allowed(self, tenant_id: str, account_id: str) -> Dict[str, Any]:
        gate = self.governance.is_dispatch_allowed(tenant_id, account_id)
        if not gate["allowed"]:
            self.audit.append(
                tenant_id=tenant_id,
                actor="system:module_4",
                action="DISPATCH_BLOCKED",
                module="MODULE_4_ORCHESTRATOR",
                payload={"account_id": account_id, "reason": gate["reason"]},
            )
        return gate

    def apply_circuit_breakers(
        self,
        tenant_id: str,
        account_id: str,
        captcha_failures_15m: int = 0,
        proxy_failure_rate: float = 0.0,
        consecutive_429: int = 0,
        single_task_cost_usd: float = 0.0,
    ) -> Dict[str, Any]:
        result = self.telemetry.check_circuit_breakers(
            captcha_failures_15m=captcha_failures_15m,
            proxy_failure_rate=proxy_failure_rate,
            consecutive_429=consecutive_429,
            single_task_cost_usd=single_task_cost_usd,
            account_id=account_id,
        )

        applied = []
        for action in result.get("actions", []):
            name = action["action"]
            if name == "PAUSE_PROFILE_DISPATCH":
                session = OperatorSession(
                    user_id="system:module_5",
                    tenant_id=tenant_id,
                    email="telemetry@wolf.internal",
                    role=Role.SUPER_ADMIN,
                )
                event = self.governance.issue_kill_switch(
                    KillSwitchRequest(
                        level=KillLevel.ACCOUNT,
                        tenant_id=tenant_id,
                        account_id=account_id,
                        reason=action["reason"],
                    ),
                    session,
                )
                applied.append(event.model_dump())
                self.audit.append(
                    tenant_id=tenant_id,
                    actor="system:module_5",
                    action="CIRCUIT_BREAKER_ACCOUNT_PAUSE",
                    module="MODULE_5_ANALYTICS",
                    payload={
                        "account_id": account_id,
                        "reason": action["reason"],
                        "alert_channel": action["alert_channel"],
                        "kill_event_id": event.event_id,
                    },
                )
            elif name == "ROTATE_PROXY_SUBNET_AND_THROTTLE":
                self.audit.append(
                    tenant_id=tenant_id,
                    actor="system:module_5",
                    action="PROXY_POOL_THROTTLE",
                    module="MODULE_5_ANALYTICS",
                    payload={
                        "account_id": account_id,
                        "reason": action["reason"],
                        "proxy_failure_rate": proxy_failure_rate,
                    },
                )
                applied.append(action)
            elif name == "EXTEND_COOLING_WINDOW_200PCT":
                cool = self.telemetry.ledger.get_cooling(account_id)
                new_mult = float(cool.get("multiplier") or 1.0) * 3.0
                self.telemetry.ledger.set_cooling(
                    account_id, multiplier=new_mult, consecutive_429=0
                )
                # Stretch human action delays suite-wide
                from services.shared.humanization import humanization

                humanization.set_cooling_multiplier(new_mult)
                self.audit.append(
                    tenant_id=tenant_id,
                    actor="system:module_5",
                    action="COOLING_WINDOW_EXTENDED",
                    module="MODULE_5_ANALYTICS",
                    payload={
                        "account_id": account_id,
                        "reason": action["reason"],
                        "cooling_multiplier": new_mult,
                    },
                )
                applied.append({**action, "cooling_multiplier": new_mult})
            elif name == "TERMINATE_THREAD_AND_FLAG":
                self.audit.append(
                    tenant_id=tenant_id,
                    actor="system:module_5",
                    action="TOKEN_COST_ANOMALY",
                    module="MODULE_5_ANALYTICS",
                    payload={
                        "account_id": account_id,
                        "reason": action["reason"],
                        "alert_channel": action["alert_channel"],
                        "cost_usd": single_task_cost_usd,
                    },
                )
                applied.append(action)

        return {
            **result,
            "applied_controls": applied,
            "dispatch_gate": self.governance.is_dispatch_allowed(tenant_id, account_id),
        }

    def log_kill_switch(
        self,
        tenant_id: str,
        actor: str,
        level: int,
        reason: str,
        account_id: Optional[str] = None,
        event_id: Optional[str] = None,
    ):
        self.audit.append(
            tenant_id=tenant_id,
            actor=actor,
            action="KILL_SWITCH_ISSUED",
            module="MODULE_6_CONTROL_CENTER",
            payload={
                "level": level,
                "reason": reason,
                "account_id": account_id,
                "event_id": event_id,
            },
        )

    def log_task_result(
        self,
        tenant_id: str,
        account_id: str,
        task_id: str,
        status: str,
        module_target: str,
    ):
        self.audit.append(
            tenant_id=tenant_id,
            actor="system:module_4",
            action=f"TASK_{status}",
            module="MODULE_4_ORCHESTRATOR",
            payload={
                "task_id": task_id,
                "account_id": account_id,
                "module_target": module_target,
                "status": status,
            },
        )


safety_bridge = SafetyBridge()
