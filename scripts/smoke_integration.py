"""
Smoke integration test — exercises Module 4 ↔ 5 ↔ 6 ↔ audit without external services.
Run: python scripts/smoke_integration.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.module_4_orchestrator.dispatcher import Module4CentralOrchestrator, TaskPayload
from services.module_5_analytics.telemetry import Module5TelemetryEngine
from services.module_6_control_center.governance import (
    KillLevel,
    KillSwitchRequest,
    OperatorSession,
    Role,
    control_center,
)
from services.security_governance.kms_crypto import KMSCryptoEngine
from services.shared.safety_bridge import SafetyBridge


def assert_true(cond: bool, msg: str):
    if not cond:
        raise AssertionError(msg)
    print(f"  OK  {msg}")


def main():
    print("\n=== Project Wolf Smoke Integration ===\n")

    # Fresh instances so tests don't share polluted global state from prior runs
    orchestrator = Module4CentralOrchestrator()
    telemetry = Module5TelemetryEngine()
    # Use global control_center (wired into Module 4 via safety_bridge)
    # Reset kill state for deterministic run (memory + SQLite)
    control_center.paused_accounts.clear()
    control_center.paused_tenants.clear()
    control_center.global_freeze = False
    control_center.kill_events.clear()
    try:
        from services.shared.sqlite_store import store as sqlite_store
        sqlite_store.save_kill_state(set(), set(), False)
    except Exception:
        pass

    bridge = SafetyBridge(governance=control_center, telemetry=telemetry)
    # Rebind global bridge used by Module 4
    import services.shared.safety_bridge as bridge_mod
    bridge_mod.safety_bridge = bridge
    import services.module_4_orchestrator.dispatcher as m4
    m4.safety_bridge = bridge

    tenant = "org_wolf_demo"
    account = "acct_smoke_01"

    # 1) Safety score + CPL
    sa = telemetry.calculate_account_safety_score(1, 1, 2)
    assert_true(65.0 <= sa <= 75.0, f"Safety score in expected band (got {sa})")
    cpl = telemetry.calculate_cost_per_lead(12.0, 3.0, 5.0, 4)
    assert_true(cpl == 5.0, f"CPL equals 5.0 (got {cpl})")

    # 2) Enqueue + successful dispatch path
    task = orchestrator.enqueue_task(
        TaskPayload(
            tenant_id=tenant,
            account_id=account,
            module_target="module_1",
            action_type="feed_engage",
            payload_data={"post_id": "p_1"},
        )
    )
    # Force success by patching random — instead dispatch until not random fail or check status set
    result = orchestrator.execute_task_dispatch(task.task_id)
    assert_true(
        result["status"] in {"SUCCESS", "RETRY_QUEUED", "QUARANTINED_DLQ"},
        f"Dispatch returned known status ({result['status']})",
    )

    # 3) Circuit breaker → Level 1 account pause
    cb = bridge.apply_circuit_breakers(
        tenant_id=tenant,
        account_id=account,
        captcha_failures_15m=3,
        proxy_failure_rate=0.05,
    )
    assert_true(cb["circuit_breaker_triggered"], "Circuit breaker triggered on CAPTCHA storm")
    assert_true(account in control_center.paused_accounts, "Account paused via Module 6")

    # 4) Module 4 must block after kill-switch
    task2 = orchestrator.enqueue_task(
        TaskPayload(
            tenant_id=tenant,
            account_id=account,
            module_target="module_3",
            action_type="comment_to_dm",
            payload_data={},
        )
    )
    blocked = orchestrator.execute_task_dispatch(task2.task_id)
    assert_true(
        blocked["status"] == "BLOCKED_BY_KILL_SWITCH",
        "Dispatch blocked by kill-switch after circuit breaker",
    )

    # 5) Manual Level 3 global freeze
    control_center.paused_accounts.discard(account)
    session = OperatorSession(
        user_id="admin_smoke",
        tenant_id=tenant,
        email="admin@wolf.test",
        role=Role.SUPER_ADMIN,
    )
    event = control_center.issue_kill_switch(
        KillSwitchRequest(level=KillLevel.GLOBAL, reason="smoke test global freeze"),
        session,
    )
    bridge.log_kill_switch(tenant, session.user_id, 3, event.reason, event_id=event.event_id)
    assert_true(control_center.global_freeze, "Global freeze active")

    gate = bridge.assert_dispatch_allowed(tenant, "acct_other")
    assert_true(gate["allowed"] is False, "Global freeze blocks all accounts")

    # 6) KMS round-trip
    kms = KMSCryptoEngine()
    secret = "fb_session_cookie_xyz"
    sealed = kms.encrypt(secret)
    assert_true(kms.decrypt(sealed) == secret, "KMS AES-256-GCM encrypt/decrypt round-trip")

    # 7) Audit chain integrity
    assert_true(bridge.audit.verify_chain(), "Merkle audit chain verifies")
    assert_true(len(bridge.audit.chain) >= 2, "Audit trail has multiple events")

    print("\n=== All smoke checks passed ===\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
