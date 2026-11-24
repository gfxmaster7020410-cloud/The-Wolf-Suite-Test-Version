"""
Module 6: Multi-Tenant Control Center — RBAC, HITL queues, emergency kill-switches.
Backend governance API that powers the Next.js Control Center UI.
"""

import time
import uuid
from enum import Enum
from typing import Any, Dict, List, Optional, Set

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field


class Role(str, Enum):
    SUPER_ADMIN = "SuperAdmin"
    CAMPAIGN_MANAGER = "CampaignManager"
    HITL_OPERATOR = "HITLOperator"
    READ_ONLY_VIEWER = "ReadOnlyViewer"


class Permission(str, Enum):
    CONFIG = "config"
    CAMPAIGNS = "campaigns"
    HITL = "hitl"
    VAULT = "vault"
    KILL_SWITCH = "kill_switch"


# RBAC matrix from SOFTWARE_SPECIFICATION.md Section 8.1
RBAC_MATRIX: Dict[Role, Dict[Permission, str]] = {
    Role.SUPER_ADMIN: {
        Permission.CONFIG: "full",
        Permission.CAMPAIGNS: "full",
        Permission.HITL: "full",
        Permission.VAULT: "full",
        Permission.KILL_SWITCH: "global",
    },
    Role.CAMPAIGN_MANAGER: {
        Permission.CONFIG: "none",
        Permission.CAMPAIGNS: "full",
        Permission.HITL: "full",
        Permission.VAULT: "workspace",
        Permission.KILL_SWITCH: "tenant",
    },
    Role.HITL_OPERATOR: {
        Permission.CONFIG: "none",
        Permission.CAMPAIGNS: "view",
        Permission.HITL: "full",
        Permission.VAULT: "none",
        Permission.KILL_SWITCH: "tenant",
    },
    Role.READ_ONLY_VIEWER: {
        Permission.CONFIG: "none",
        Permission.CAMPAIGNS: "view",
        Permission.HITL: "none",
        Permission.VAULT: "none",
        Permission.KILL_SWITCH: "none",
    },
}


class KillLevel(int, Enum):
    ACCOUNT = 1   # Single profile pause
    TENANT = 2    # Workspace-wide campaign pause
    GLOBAL = 3    # System freeze via Pub/Sub kill-signal


class HITLReason(str, Enum):
    LOW_CONFIDENCE = "low_ai_confidence"
    HIGH_INTENT = "high_intent_deal"
    FIRST_TOUCH = "first_touch_outreach"
    MANUAL = "manual_escalation"


class OperatorSession(BaseModel):
    user_id: str
    tenant_id: str
    email: str
    role: Role


class HITLTask(BaseModel):
    task_id: str = Field(default_factory=lambda: f"hitl_{uuid.uuid4().hex[:8]}")
    tenant_id: str
    account_id: str
    reason: HITLReason
    confidence: Optional[float] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    status: str = "PENDING"  # PENDING | APPROVED | REJECTED
    created_at: float = Field(default_factory=time.time)
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[float] = None


class KillSwitchRequest(BaseModel):
    level: KillLevel
    tenant_id: Optional[str] = None
    account_id: Optional[str] = None
    reason: str


class KillSwitchEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: f"kill_{uuid.uuid4().hex[:8]}")
    level: KillLevel
    tenant_id: Optional[str] = None
    account_id: Optional[str] = None
    reason: str
    issued_by: str
    issued_at: float = Field(default_factory=time.time)
    active: bool = True


class Module6ControlCenter:
    """
    Administrative command center:
    - Multi-tenant RBAC enforcement
    - Human-in-the-Loop review queues (confidence < 85%, high-intent, first-touch)
    - Level 1/2/3 emergency kill-switches
    - SQLite durability for local/dev (Docker-free)
    """

    CONFIDENCE_THRESHOLD = 0.85

    def __init__(self, persist: bool = True):
        self.persist = persist
        self.hitl_queue: Dict[str, HITLTask] = {}
        self.kill_events: Dict[str, KillSwitchEvent] = {}
        self.paused_accounts: Set[str] = set()
        self.paused_tenants: Set[str] = set()
        self.global_freeze: bool = False
        if persist:
            self._hydrate_from_store()

    def _hydrate_from_store(self) -> None:
        from services.shared.sqlite_store import store

        for task_id, raw in store.load_all_hitl().items():
            self.hitl_queue[task_id] = HITLTask(**raw)

        for raw in store.list_kill_events():
            self.kill_events[raw["event_id"]] = KillSwitchEvent(**raw)

        state = store.load_kill_state()
        self.paused_accounts = set(state["paused_accounts"])
        self.paused_tenants = set(state["paused_tenants"])
        self.global_freeze = bool(state["global_freeze"])

    def _persist_hitl(self, task: HITLTask) -> None:
        if not self.persist:
            return
        from services.shared.sqlite_store import store
        store.upsert_hitl(task.model_dump())

    def _persist_kill(self, event: KillSwitchEvent) -> None:
        if not self.persist:
            return
        from services.shared.sqlite_store import store
        store.save_kill_event(event.model_dump())
        store.save_kill_state(self.paused_accounts, self.paused_tenants, self.global_freeze)

    def require_permission(self, session: OperatorSession, permission: Permission, min_level: str = "view") -> None:
        access = RBAC_MATRIX.get(session.role, {}).get(permission, "none")
        levels = {"none": 0, "view": 1, "workspace": 2, "tenant": 2, "full": 3, "global": 4}
        if levels.get(access, 0) < levels.get(min_level, 1):
            raise HTTPException(
                status_code=403,
                detail=f"Role {session.role} lacks {permission} permission (need >= {min_level})"
            )

    def enqueue_hitl_if_needed(
        self,
        tenant_id: str,
        account_id: str,
        confidence: Optional[float],
        payload: Dict[str, Any],
        high_intent: bool = False,
        first_touch: bool = False,
        manual: bool = False,
    ) -> Optional[HITLTask]:
        """Auto-route tasks to HITL when escalation triggers fire."""
        reason: Optional[HITLReason] = None
        if manual:
            reason = HITLReason.MANUAL
        elif high_intent:
            reason = HITLReason.HIGH_INTENT
        elif first_touch:
            reason = HITLReason.FIRST_TOUCH
        elif confidence is not None and confidence < self.CONFIDENCE_THRESHOLD:
            reason = HITLReason.LOW_CONFIDENCE

        if reason is None:
            return None

        task = HITLTask(
            tenant_id=tenant_id,
            account_id=account_id,
            reason=reason,
            confidence=confidence,
            payload=payload,
        )
        self.hitl_queue[task.task_id] = task
        self._persist_hitl(task)
        return task

    def list_hitl_queue(self, tenant_id: str, status: str = "PENDING") -> List[HITLTask]:
        return [
            t for t in self.hitl_queue.values()
            if t.tenant_id == tenant_id and t.status == status
        ]

    def review_hitl(self, task_id: str, session: OperatorSession, approve: bool) -> HITLTask:
        self.require_permission(session, Permission.HITL, "full")
        task = self.hitl_queue.get(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="HITL task not found")
        if task.tenant_id != session.tenant_id and session.role != Role.SUPER_ADMIN:
            raise HTTPException(status_code=403, detail="Cross-tenant HITL access denied")

        task.status = "APPROVED" if approve else "REJECTED"
        task.reviewed_by = session.user_id
        task.reviewed_at = time.time()
        self._persist_hitl(task)
        return task

    def issue_kill_switch(self, req: KillSwitchRequest, session: OperatorSession) -> KillSwitchEvent:
        if req.level == KillLevel.GLOBAL:
            self.require_permission(session, Permission.KILL_SWITCH, "global")
            self.global_freeze = True
        elif req.level == KillLevel.TENANT:
            self.require_permission(session, Permission.KILL_SWITCH, "tenant")
            if not req.tenant_id:
                raise HTTPException(status_code=400, detail="tenant_id required for Level 2")
            self.paused_tenants.add(req.tenant_id)
        else:
            self.require_permission(session, Permission.KILL_SWITCH, "tenant")
            if not req.account_id:
                raise HTTPException(status_code=400, detail="account_id required for Level 1")
            self.paused_accounts.add(req.account_id)

        event = KillSwitchEvent(
            level=req.level,
            tenant_id=req.tenant_id or session.tenant_id,
            account_id=req.account_id,
            reason=req.reason,
            issued_by=session.user_id,
        )
        self.kill_events[event.event_id] = event
        self._persist_kill(event)
        return event

    def is_dispatch_allowed(self, tenant_id: str, account_id: str) -> Dict[str, Any]:
        """Called by Module 4 before worker dispatch."""
        if self.global_freeze:
            return {"allowed": False, "reason": "GLOBAL_FREEZE_LEVEL_3"}
        if tenant_id in self.paused_tenants:
            return {"allowed": False, "reason": "TENANT_PAUSE_LEVEL_2"}
        if account_id in self.paused_accounts:
            return {"allowed": False, "reason": "ACCOUNT_PAUSE_LEVEL_1"}
        return {"allowed": True, "reason": None}


control_center = Module6ControlCenter()
app = FastAPI(title="Module 6 Control Center Governance API")


def get_session(
    x_user_id: str = Header(..., alias="X-User-Id"),
    x_tenant_id: str = Header(..., alias="X-Tenant-Id"),
    x_user_email: str = Header(..., alias="X-User-Email"),
    x_user_role: str = Header(..., alias="X-User-Role"),
) -> OperatorSession:
    try:
        role = Role(x_user_role)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid role: {x_user_role}")
    return OperatorSession(
        user_id=x_user_id,
        tenant_id=x_tenant_id,
        email=x_user_email,
        role=role,
    )


@app.get("/api/v1/rbac/matrix")
def get_rbac_matrix(session: OperatorSession = Depends(get_session)):
    return {role.value: perms for role, perms in RBAC_MATRIX.items()}


@app.get("/api/v1/hitl/queue")
def get_hitl_queue(session: OperatorSession = Depends(get_session)):
    control_center.require_permission(session, Permission.HITL, "view")
    return control_center.list_hitl_queue(session.tenant_id)


@app.post("/api/v1/hitl/enqueue")
def enqueue_hitl(
    tenant_id: str,
    account_id: str,
    confidence: Optional[float] = None,
    high_intent: bool = False,
    first_touch: bool = False,
    payload: Optional[Dict[str, Any]] = None,
    session: OperatorSession = Depends(get_session),
):
    control_center.require_permission(session, Permission.HITL, "full")
    task = control_center.enqueue_hitl_if_needed(
        tenant_id=tenant_id,
        account_id=account_id,
        confidence=confidence,
        payload=payload or {},
        high_intent=high_intent,
        first_touch=first_touch,
        manual=True,
    )
    return task or {"status": "NO_ESCALATION"}


@app.post("/api/v1/hitl/{task_id}/review")
def review_hitl(task_id: str, approve: bool, session: OperatorSession = Depends(get_session)):
    return control_center.review_hitl(task_id, session, approve)


@app.post("/api/v1/kill-switch")
def issue_kill_switch(req: KillSwitchRequest, session: OperatorSession = Depends(get_session)):
    event = control_center.issue_kill_switch(req, session)
    # Lazy import avoids circular dependency at module load
    from services.shared.safety_bridge import safety_bridge
    safety_bridge.log_kill_switch(
        tenant_id=event.tenant_id or session.tenant_id,
        actor=session.user_id,
        level=int(event.level),
        reason=event.reason,
        account_id=event.account_id,
        event_id=event.event_id,
    )
    return event


@app.get("/api/v1/dispatch/allowed")
def check_dispatch_allowed(tenant_id: str, account_id: str):
    return control_center.is_dispatch_allowed(tenant_id, account_id)


@app.get("/api/v1/kill-switch/events")
def list_kill_events(session: OperatorSession = Depends(get_session)):
    control_center.require_permission(session, Permission.KILL_SWITCH, "tenant")
    return list(control_center.kill_events.values())
