"""
Project Wolf — Unified API Gateway
Mounts Module 4 Orchestrator, Module 6 Control Center, Module 7 Webhooks,
and Safety Bridge circuit-breaker endpoints under one FastAPI process.
"""

import os
from pathlib import Path
from typing import Optional

# Load .env without requiring python-dotenv
_env_path = Path(__file__).resolve().parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        _k, _v = _k.strip(), _v.strip().strip('"').strip("'")
        os.environ.setdefault(_k, _v)

from fastapi import Body, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from services.module_4_orchestrator.dispatcher import app as orchestrator_app
from services.module_6_control_center.governance import app as governance_app
from services.module_7_webhooks.webhook_router import app as webhooks_app
from services.security_governance.saml_gateway import app as saml_app
from services.security_governance.scim_router import scim_app
from services.ai_gateway.gateway import app as ai_gateway_app
from services.shared.safety_bridge import safety_bridge


app = FastAPI(
    title="Wolf Suite Unified Platform API",
    version="2.0.0",
    description="Wolf Suite — enterprise autonomous social media & multi-agent orchestration",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class CircuitBreakerRequest(BaseModel):
    tenant_id: str
    account_id: str
    captcha_failures_15m: int = 0
    proxy_failure_rate: float = Field(0.0, ge=0.0, le=1.0)


def _humanization_status(account_id: str = "acct_wolf_01") -> dict:
    from services.shared.humanization import humanization
    snap = humanization.snapshot()
    return {
        **snap,
        "cap": humanization.can_act(account_id),
        "behaviors": [
            "M1: Bezier cursor + Gaussian typing + Gemini Web engage loop",
            "M2: Inter-platform gaps, group-share 45–120s, Spintax seed comments",
            "M3: Action delays, Spintax public replies, 14-day warm-up caps",
            "M4: Pre-dispatch human dwell + hourly/daily action ceilings",
            "M7: Webhook micro-jitter + retry jitter",
            "M8: Agent think delays + Spintax DM variants",
            "Shared: reading dwell, typo cadence, HUMAN_SIMULATE_FAST demo mode",
        ],
    }


@app.get("/api/v1/humanization/profile")
def get_humanization_profile():
    return _humanization_status()


class HumanizationUpdate(BaseModel):
    profile_name: Optional[str] = None
    enabled: Optional[bool] = None
    max_actions_per_hour: Optional[int] = None
    max_actions_per_day: Optional[int] = None
    action_delay_min: Optional[float] = None
    action_delay_max: Optional[float] = None
    key_delay_mean: Optional[float] = None
    group_share_delay_min: Optional[float] = None
    group_share_delay_max: Optional[float] = None
    typo_probability: Optional[float] = None
    account_start_date: Optional[str] = None


@app.post("/api/v1/humanization/profile")
def update_humanization_profile(body: HumanizationUpdate):
    """Tune Wolf Suite human-like behavior from the main screen."""
    from services.shared.humanization import humanization, HumanizationProfile

    data = body.model_dump(exclude_none=True)
    if "profile_name" in data:
        # Re-apply preset when profile name changes
        import os
        os.environ["HUMAN_PROFILE"] = data["profile_name"]
        base = HumanizationProfile.from_env()
        humanization.profile = base
        data.pop("profile_name", None)
    if data:
        humanization.update_profile(**data)
    return _humanization_status()


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "wolf-suite",
        "product": "Wolf Suite",
        "modules": ["m1", "m2", "m3", "m4", "m5", "m6", "m7", "m8", "security", "ai_gateway"],
    }


@app.get("/api/v1/suite/overview")
def suite_overview(tenant_id: str = "org_wolf_demo"):
    """
    Wolf Suite main-screen aggregate: all modules, live gates, and connection graph.
    """
    from services.shared.sqlite_store import store
    from services.module_6_control_center.governance import control_center
    from services.module_4_orchestrator.dispatcher import orchestrator

    stats = store.dashboard_stats(tenant_id)
    freeze = control_center.global_freeze or stats.get("global_freeze", False)
    dispatch_ok = not freeze and len(control_center.paused_accounts) == 0

    modules = [
        {
            "id": "m1",
            "name": "Feed Observer",
            "role": "Engage & react with humanized browser mechanics",
            "status": "ready" if dispatch_ok else "gated",
            "depends_on": ["m4", "ai_gateway", "security"],
            "href": "/module-1",
        },
        {
            "id": "m2",
            "name": "Media Engine",
            "role": "Dual-stage AI captions + multi-platform publish",
            "status": "ready" if dispatch_ok else "gated",
            "depends_on": ["m4", "ai_gateway"],
            "href": "#module-m2",
        },
        {
            "id": "m3",
            "name": "Lead Funnel",
            "role": "Comment-to-DM, Spintax, ad fatigue guard",
            "status": "ready" if dispatch_ok else "gated",
            "depends_on": ["m4", "m7"],
            "href": "#module-m3",
        },
        {
            "id": "m4",
            "name": "Orchestrator",
            "role": "Task dispatch, rate limits, Redlock, DLQ",
            "status": "online",
            "metric": f"{len(orchestrator.task_store)} tasks · {len(orchestrator.dead_letter_queue)} DLQ",
            "depends_on": ["m6"],
            "href": "#module-m4",
        },
        {
            "id": "m5",
            "name": "Analytics",
            "role": "Safety score, CPL, circuit breakers",
            "status": "online",
            "depends_on": ["m6"],
            "href": "#module-m5",
        },
        {
            "id": "m6",
            "name": "Control Center",
            "role": "RBAC, HITL, kill-switch governance",
            "status": "freeze" if freeze else "online",
            "metric": f"{stats.get('pending_hitl', 0)} HITL · {stats.get('paused_accounts', 0)} paused",
            "depends_on": ["security"],
            "href": "/hitl",
        },
        {
            "id": "m7",
            "name": "Webhooks",
            "role": "HMAC delivery, retries, OAuth scopes",
            "status": "online",
            "depends_on": ["m4"],
            "href": "#module-m7",
        },
        {
            "id": "m8",
            "name": "Multi-Agent",
            "role": "LangGraph supervisor, self-healing loops",
            "status": "ready" if dispatch_ok else "gated",
            "depends_on": ["m4", "ai_gateway", "m6"],
            "href": "#module-m8",
        },
        {
            "id": "ai_gateway",
            "name": "AI Gateway",
            "role": "Master key + per-module LLM overrides",
            "status": "online",
            "depends_on": [],
            "href": "#module-ai",
        },
        {
            "id": "security",
            "name": "Security",
            "role": "SAML, SCIM, PII, KMS, Merkle audit",
            "status": "online",
            "metric": f"{stats.get('audit_events', 0)} audit events",
            "depends_on": [],
            "href": "/kill-switch",
        },
    ]

    return {
        "product": "Wolf Suite",
        "tenant_id": tenant_id,
        "global_freeze": freeze,
        "dispatch_allowed": dispatch_ok,
        "stats": stats,
        "humanization": _humanization_status(tenant_id),
        "modules": modules,
        "connections": [
            {"from": "m8", "to": "m4"},
            {"from": "m4", "to": "m1"},
            {"from": "m4", "to": "m2"},
            {"from": "m4", "to": "m3"},
            {"from": "m5", "to": "m6"},
            {"from": "m6", "to": "m4"},
            {"from": "m1", "to": "ai_gateway"},
            {"from": "m2", "to": "ai_gateway"},
            {"from": "m8", "to": "ai_gateway"},
            {"from": "m3", "to": "m7"},
            {"from": "security", "to": "m6"},
        ],
    }


class SuiteRunRequest(BaseModel):
    module_id: str
    tenant_id: str = "org_wolf_demo"
    account_id: str = "acct_wolf_01"
    max_posts: Optional[int] = None
    dry_run: Optional[bool] = None


@app.get("/api/v1/module1/config")
def get_m1_config():
    """Full Module 1 settings (persisted data/module1_settings.json + env defaults).

    Legacy keys are preserved so the Control Center UI keeps working.
    """
    from services.module_1_feed_observer.runner import module1_runner

    settings = module1_runner.config()
    return {
        **settings,
        # Legacy Control Center keys
        "user_data_dir": settings["user_data_dir"],
        "facebook_url": settings["feed_url"],
        "gemini_mode": "web",
        "gemini_url": settings["gemini_url"],
        "default_reaction": settings["fixed_reaction"],
        "live_engage": True,
        "comment_paste": settings["typing_style"] == "paste",
        "default_posts": settings["max_posts_per_run"],
        "wait_time": settings["gap_between_posts_min"],
        "loop": "unlimited",
    }


@app.put("/api/v1/module1/config")
async def update_m1_config(patch: dict = Body(default={})):
    """Update any Module 1 setting live — applies to a running worker too.

    Unknown keys are ignored (listed as warnings), values are validated.
    """
    from services.module_1_feed_observer.runner import module1_runner

    return module1_runner.update_config(patch)


@app.post("/api/v1/module1/test-ai")
async def module1_test_ai():
    """PHASE-03 — verify the configured Gemini REST key (no browser, no run).

    Sends a tiny built-in PNG with a trivial structured prompt and reports
    {ok, latency_ms, key_configured} so the dashboard can validate the
    API-key field instantly.
    """
    from services.module_1_feed_observer.runner import module1_runner

    return await module1_runner.test_ai_engine()


@app.get("/api/v1/module1/config/meta")
def module1_config_meta():
    """PHASE-16 — enum option lists + per-field help text.

    The dashboard renders every select/radio/tooltip from this payload so
    validator enums stay the single source of truth (no hardcoded drift)."""
    from services.module_1_feed_observer.config import settings_meta

    return settings_meta()


@app.post("/api/v1/module1/seen/clear")
async def module1_seen_clear():
    """PHASE-16 — wipe the persisted seen-posts memory (next run starts fresh).

    The worker rebuilds its SeenStore from the same JSON file each start,
    so clearing here takes effect across runs."""
    from services.module_1_feed_observer.seen_store import seen_store

    cleared = seen_store.clear()
    return {"ok": True, "cleared": cleared}


class Module1StartRequest(BaseModel):
    wait_time: float = 3.0


@app.post("/api/v1/module1/start")
async def module1_start(req: Optional[Module1StartRequest] = None):
    """Start the Facebook + Gemini Web engagement worker (manual loop)."""
    from services.module_1_feed_observer.runner import module1_runner

    return await module1_runner.start()


@app.post("/api/v1/module1/stop")
async def module1_stop():
    """Stop the running Module 1 worker."""
    from services.module_1_feed_observer.runner import module1_runner

    return await module1_runner.stop()


@app.post("/api/v1/module1/pause")
async def module1_pause(body: dict = Body(default={})):
    """Pause the worker between actions (browser stays open)."""
    from services.module_1_feed_observer.runner import module1_runner

    return await module1_runner.pause(
        reason=(body or {}).get("reason", "user") or "user"
    )


@app.post("/api/v1/module1/resume")
async def module1_resume():
    from services.module_1_feed_observer.runner import module1_runner

    return await module1_runner.resume()


@app.post("/api/v1/module1/skip")
async def module1_skip():
    """Skip the current post without engaging."""
    from services.module_1_feed_observer.runner import module1_runner

    return await module1_runner.skip_post()


@app.get("/api/v1/module1/status")
def module1_status():
    """Live analytics: phase, comments, likes, caps, seen posts, settings."""
    from services.module_1_feed_observer.runner import module1_runner

    return module1_runner.status()


@app.post("/api/v1/suite/run")
async def suite_run_module(req: SuiteRunRequest, wait: bool = False):
    """
    Start a module run with live web preview.
    Returns immediately with preview_session_id (poll /api/v1/suite/preview/{id}).
    Pass wait=true to block until finished (scripts / smoke).

    Playwright jobs run on a Windows Proactor-safe worker thread so Chromium
    can spawn under uvicorn (avoids NotImplementedError on subprocess_exec).
    """
    import asyncio

    from services.shared.async_runtime import await_playwright_job
    from services.shared.preview_store import preview_store
    from services.shared.suite_visual import MODULE_PREVIEW_META, run_module_visual

    mid = req.module_id.lower()
    meta = MODULE_PREVIEW_META.get(
        mid,
        {
            "title": mid,
            "subtitle": "Live module execution",
            "steps": ["Starting", "Running", "Finishing"],
        },
    )
    sid = preview_store.start(
        module_id=mid,
        title=meta["title"],
        subtitle=meta["subtitle"],
        step_names=meta["steps"],
    )

    def _factory():
        return run_module_visual(
            mid,
            tenant_id=req.tenant_id,
            account_id=req.account_id,
            preview_session_id=sid,
            max_posts=req.max_posts,
            dry_run=req.dry_run,
        )

    async def _job() -> None:
        try:
            await await_playwright_job(_factory)
        except Exception as exc:
            # Ensure preview is marked failed even if visual runner crashes early
            try:
                preview_store.fail(sid, f"{type(exc).__name__}: {exc}")
            except Exception:
                pass

    if wait:
        result = await await_playwright_job(_factory)
        return {"module": mid, "on_screen": True, "preview_session_id": sid, **result}

    asyncio.create_task(_job())
    return {
        "module": mid,
        "status": "started",
        "on_screen": True,
        "web_preview": True,
        "preview_session_id": sid,
        "preview_url": f"/api/v1/suite/preview/{sid}",
    }


@app.get("/api/v1/suite/preview/latest")
def suite_preview_latest():
    from services.shared.preview_store import preview_store

    sess = preview_store.latest()
    if not sess:
        return {"status": "idle", "steps": [], "cards": []}
    return sess


@app.get("/api/v1/suite/preview/{session_id}")
def suite_preview_session(session_id: str):
    from services.shared.preview_store import preview_store

    sess = preview_store.get(session_id)
    if not sess:
        return {"status": "not_found", "session_id": session_id}
    return sess


@app.post("/api/v1/safety/circuit-breakers")
def enforce_circuit_breakers(req: CircuitBreakerRequest):
    """Module 5 → Module 6: auto-pause accounts on CAPTCHA/proxy incidents."""
    return safety_bridge.apply_circuit_breakers(
        tenant_id=req.tenant_id,
        account_id=req.account_id,
        captcha_failures_15m=req.captcha_failures_15m,
        proxy_failure_rate=req.proxy_failure_rate,
    )


@app.get("/api/v1/safety/audit")
def list_audit_trail(limit: Optional[int] = 50):
    chain = safety_bridge.audit.export_siem_batch()
    return {
        "verified": safety_bridge.audit.verify_chain(),
        "count": len(chain),
        "events": chain[-limit:] if limit else chain,
    }


@app.get("/api/v1/safety/dashboard")
def safety_dashboard(tenant_id: str = "org_wolf_demo"):
    """Live Control Center metrics from SQLite + in-memory kill state."""
    from services.shared.sqlite_store import store
    from services.module_6_control_center.governance import control_center

    stats = store.dashboard_stats(tenant_id)
    return {
        **stats,
        "memory_pending_hitl": len(control_center.list_hitl_queue(tenant_id)),
        "memory_paused_accounts": len(control_center.paused_accounts),
        "memory_global_freeze": control_center.global_freeze,
        "audit_chain_verified": safety_bridge.audit.verify_chain(),
        "in_memory_audit_count": len(safety_bridge.audit.chain),
    }


# Mount module routers (preserve their /api/v1 paths)
app.mount("/orchestrator", orchestrator_app)
app.mount("/governance", governance_app)
app.mount("/webhooks", webhooks_app)
app.mount("/auth", saml_app)
app.mount("/scim", scim_app)
app.mount("/ai", ai_gateway_app)


# Windows console / Playwright bootstrap (also applied under uvicorn --reload)
import sys as _sys

if _sys.platform == "win32":
    import asyncio as _asyncio

    _asyncio.set_event_loop_policy(_asyncio.WindowsProactorEventLoopPolicy())
    try:
        _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        _sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=True)
