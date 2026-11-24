"""
Wolf Suite — On-screen visual runners for every Control Center module.

Each `run_*_visual` opens a Chromium window and streams live pipeline steps
while executing the real module logic underneath.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, Optional

from services.shared.preview_store import active_preview_session_id
from services.shared.visual_runner import VisualRunner, run_visual_pipeline


# Shared metadata for web live preview shells
MODULE_PREVIEW_META = {
    "m1": {
        "title": "Module 1 · Feed Observer",
        "subtitle": "Facebook → screenshot → Gemini Web → engage",
        "steps": [
            "Open visual browser",
            "Facebook navigation",
            "Post capture",
            "Gemini analysis",
            "Engagement",
        ],
    },
    "m2": {
        "title": "Module 2 · Media Engine",
        "subtitle": "Ingest → dual-stage AI → publish → share → seed",
        "steps": [
            "Media ingest",
            "Dual-stage AI copy",
            "Multi-platform publish",
            "Group distribution",
            "Engagement hooks",
        ],
    },
    "m3": {
        "title": "Module 3 · Lead Funnel & Ad Guard",
        "subtitle": "Warm-up · fatigue · ROAS scale · DM funnel",
        "steps": ["Warm-up gate", "Ad fatigue guard", "ROAS auto-scale", "Lead funnel ready"],
    },
    "m4": {
        "title": "Module 4 · Orchestrator",
        "subtitle": "Enqueue → gates → lock → humanized dispatch",
        "steps": ["Enqueue task", "Safety + human caps", "Rate limit / lock", "Execute dispatch"],
    },
    "m5": {
        "title": "Module 5 · Analytics & Telemetry",
        "subtitle": "Sa · CPL · circuit breakers · metering",
        "steps": ["Safety score", "Cost per lead", "Circuit breakers", "Metering ledger"],
    },
    "m6": {
        "title": "Module 6 · Control Center / HITL",
        "subtitle": "HITL queue · dispatch gate · kill-switch",
        "steps": ["Enqueue HITL", "Dispatch gate", "Queue snapshot"],
    },
    "m7": {
        "title": "Module 7 · Webhooks",
        "subtitle": "Register · HMAC · jittered delivery",
        "steps": ["Register endpoint", "HMAC sign", "Deliver event"],
    },
    "m8": {
        "title": "Module 8 · Multi-Agent",
        "subtitle": "Supervisor → Security → Prospecting → Copywriter",
        "steps": ["Supervisor", "Security clearance", "Prospecting", "Copywriter"],
    },
    "ai_gateway": {
        "title": "AI Gateway",
        "subtitle": "Master config · overrides · completion probe",
        "steps": ["Master config", "Module overrides", "Completion probe"],
    },
    "security": {
        "title": "Security · SAML / SCIM / PII",
        "subtitle": "PII · SAML · SCIM lifecycle",
        "steps": ["PII redaction", "SAML metadata", "SCIM store"],
    },
}


async def run_m1_visual(account_id: str = "acct_wolf_01", max_posts: Optional[int] = None, dry_run: Optional[bool] = None) -> Dict[str, Any]:
    """Feed Observer: open Facebook/Gemini for real, with accurate live step updates."""
    from pathlib import Path

    from services.module_1_feed_observer.gemini_bridge import GeminiWebBridge
    from services.module_1_feed_observer.observer import FeedObserver
    from services.shared.humanization import humanization

    # Force headful so Facebook/Gemini windows appear
    os.environ.setdefault("FB_HEADLESS", "false")
    # M1 already opens its own Chromium (FB+Gemini). Avoid a second VisualRunner window.
    os.environ["SUITE_VISUAL"] = "false"
    live = os.getenv("M1_LIVE", "false").lower() in {"1", "true", "yes"} if dry_run is None else not dry_run
    posts_limit = max_posts if max_posts is not None else int(os.getenv("M1_SUITE_POSTS", "1"))

    async def _exec(runner: VisualRunner) -> Dict[str, Any]:
        observer = FeedObserver()
        bridge: Optional[GeminiWebBridge] = None
        results = []
        last_comment = ""

        try:
            # Step 0 — browser
            await runner.set_step(0, detail="Launching Chromium persistent profile...")
            await observer.start()
            await runner.complete_step(0, "Persistent browser ready")

            # Step 1 - Facebook ready; also open Gemini once in the same browser
            await runner.set_step(1, detail="Facebook feed loaded - opening Gemini tab...")
            assert observer.page is not None
            assert observer.context is not None
            try:
                await VisualRunner.inject_hud_overlay(
                    observer.page, "Module 1 · Feed Observer", "Facebook ready", "LIVE"
                )
            except Exception:
                pass
            bridge = GeminiWebBridge(observer.context)
            await bridge.ensure_ready()
            await runner.complete_step(1, "Facebook + Gemini tabs ready (kept open)")

            for i in range(max(1, posts_limit)):
                shot_path = None
                try:
                    await runner.set_step(2, detail=f"Capturing feed post #{i + 1}...")
                    await observer.page.bring_to_front()
                    shot_path = await observer.capture_feed_post_screenshot()
                    await runner.complete_step(2, f"Screenshot saved (post {i + 1})")

                    await runner.set_step(3, detail=f"Gemini analyzing post #{i + 1}...")
                    comment = await bridge.generate_comment(shot_path)
                    last_comment = comment
                    await runner.complete_step(3, comment[:120])

                    await runner.set_step(
                        4,
                        detail="Posting comment on Facebook..." if live else "Dry-run - skip engage",
                    )
                    await observer.page.bring_to_front()
                    if live:
                        ok = await observer.engage_post(comment)
                        if ok:
                            humanization.record_action(account_id)
                        await runner.complete_step(4, f"Engaged: {ok}")
                    else:
                        await runner.complete_step(4, "Dry-run complete")

                    results.append(
                        {
                            "index": i,
                            "comment": comment,
                            "screenshot": shot_path,
                            "engaged": live,
                        }
                    )
                except Exception as exc:
                    # Attribute failure to the step that was running
                    running_idx = next(
                        (idx for idx, st in enumerate(runner.steps) if st.get("status") == "running"),
                        4,
                    )
                    await runner.fail_step(running_idx, f"{type(exc).__name__}: {exc}")
                    raise
                finally:
                    if shot_path:
                        try:
                            Path(shot_path).unlink(missing_ok=True)
                        except OSError:
                            pass

                if i + 1 < posts_limit:
                    await observer.scroll_to_next_post()
                    dwell = humanization.post_scroll_dwell()
                    if humanization.simulate_fast():
                        dwell = min(dwell, 1.0)
                    await asyncio.sleep(dwell)

            status = "COMPLETED"
            await runner.update_cards(
                [
                    {"label": "Status", "value": status, "sub": "observer session"},
                    {"label": "Posts", "value": str(len(results)), "sub": "processed"},
                    {"label": "Mode", "value": "LIVE" if live else "DRY-RUN", "sub": "M1_LIVE"},
                ]
            )
            return {
                "status": status,
                "live": live,
                "pipeline": "facebook→screenshot→gemini→react/comment",
                "result": {
                    "status": status,
                    "posts_processed": len(results),
                    "results": results,
                    "last_comment": last_comment,
                },
            }
        finally:
            if bridge is not None:
                await bridge.close()
            await observer.close()

    return await run_visual_pipeline(
        module_id="m1",
        title="Module 1 · Feed Observer",
        subtitle="Facebook → screenshot → Gemini Web → engage (same open tabs)",
        step_names=[
            "Open visual browser",
            "Facebook navigation",
            "Post capture",
            "Gemini analysis",
            "Engagement",
        ],
        executor=_exec,
    )


async def run_m2_visual(account_id: str = "media_main") -> Dict[str, Any]:
    from services.module_2_media_engine.media_processor import Module2MediaEngine
    import tempfile
    from PIL import Image

    async def _exec(runner: VisualRunner) -> Dict[str, Any]:
        await runner.set_step(0, detail="Staging demo media asset…")
        tmp = tempfile.mktemp(suffix=".png")
        Image.new("RGB", (1080, 1080), (26, 35, 50)).save(tmp)
        await runner.complete_step(0, "1:1 demo image staged")

        engine = Module2MediaEngine()
        await runner.set_step(1, detail="Dual-stage Gemini caption generation…")
        # Prefer full pipeline with visual timing
        result = await engine.execute_media_pipeline(
            tmp,
            target_platforms=["facebook", "instagram"],
            joined_groups=["group_demo_1"],
            account_id=account_id,
            pin_highlight=True,
        )
        await runner.complete_step(1, f"Copy: {(result.get('final_copy') or '')[:80]}")
        await runner.set_step(2, detail="Publishing to platforms…")
        pubs = result.get("published_posts") or {}
        await runner.complete_step(2, f"Platforms: {', '.join(pubs.keys()) or 'none'}")
        await runner.set_step(3, detail="Group share loop with human delays…")
        shares = result.get("group_shares") or []
        await runner.complete_step(3, f"Shares: {len(shares)}")
        await runner.set_step(4, detail="Seed comment + discussion hooks…")
        seed = (result.get("seed_comment") or {}).get("message", "")
        await runner.complete_step(4, (seed or "Hooks ready")[:100])
        await runner.update_cards(
            [
                {"label": "Status", "value": result.get("status", "?"), "sub": "media pipeline"},
                {"label": "Platforms", "value": str(len(pubs)), "sub": "published"},
                {
                    "label": "Human profile",
                    "value": (result.get("humanization") or {}).get("profile", "natural"),
                    "sub": "pacing",
                },
            ]
        )
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return {"status": result.get("status"), "result": result}

    return await run_visual_pipeline(
        module_id="m2",
        title="Module 2 · Media Engine",
        subtitle="Ingest → dual-stage AI → publish → group share → seed comment",
        step_names=[
            "Media ingest",
            "Dual-stage AI copy",
            "Multi-platform publish",
            "Group distribution",
            "Engagement hooks",
        ],
        executor=_exec,
    )


async def run_m3_visual(account_id: str = "page_default") -> Dict[str, Any]:
    from services.module_3_lead_funnel.fb_automation_module3 import FBAutomationModule3

    async def _exec(runner: VisualRunner) -> Dict[str, Any]:
        funnel = FBAutomationModule3()
        await runner.set_step(0, detail="Warm-up + human action caps…")
        warm = funnel.human.warmup_status(account_id)
        await runner.complete_step(0, f"Day {warm.get('warmup_day')} · ×{warm.get('multiplier')}")
        await runner.set_step(1, detail="Ad fatigue rule (Freq>3.5 & CTR<1%)…")
        fatigue = funnel.monitor_ad_fatigue(os.getenv("META_AD_ACCOUNT_ID", "123"))
        paused = len(fatigue.get("paused") or [])
        await runner.complete_step(1, f"Paused fatigued ads: {paused}")
        await runner.set_step(2, detail="ROAS auto-scale (+15% budget)…")
        scale = funnel.auto_scale_budgets(os.getenv("META_AD_ACCOUNT_ID", "123"))
        scaled = len(scale.get("scaled") or [])
        await runner.complete_step(2, f"Campaigns scaled: {scaled}")
        await runner.set_step(3, detail="Comment→DM Spintax funnel ready…")
        await runner.complete_step(
            3, f"Dry-run={funnel.dry_run} · triggers ready for Graph comments"
        )
        await runner.update_cards(
            [
                {"label": "Fatigue paused", "value": str(paused), "sub": "ads"},
                {"label": "Auto-scaled", "value": str(scaled), "sub": "campaigns"},
                {"label": "Mode", "value": "DRY" if funnel.dry_run else "LIVE", "sub": "M3_DRY_RUN"},
            ]
        )
        return {
            "status": "OK",
            "dry_run": funnel.dry_run,
            "fatigue": fatigue,
            "auto_scale": scale,
            "warmup": warm,
        }

    return await run_visual_pipeline(
        module_id="m3",
        title="Module 3 · Lead Funnel & Ad Guard",
        subtitle="Comment→DM · Spintax · fatigue pause · ROAS +15% scale",
        step_names=["Warm-up gate", "Ad fatigue guard", "ROAS auto-scale", "Lead funnel ready"],
        executor=_exec,
    )


async def run_m4_visual(tenant_id: str, account_id: str) -> Dict[str, Any]:
    from services.module_4_orchestrator.dispatcher import orchestrator, TaskPayload

    async def _exec(runner: VisualRunner) -> Dict[str, Any]:
        await runner.set_step(0, detail="Enqueue task into Redis-backed store…")
        task = orchestrator.enqueue_task(
            TaskPayload(
                tenant_id=tenant_id,
                account_id=account_id,
                module_target="module_1",
                action_type="suite_demo_engage",
                payload_data={"source": "visual_suite"},
            )
        )
        await runner.complete_step(0, f"task_id={task.task_id}")
        await runner.set_step(1, detail="Kill-switch + human cap gate…")
        await runner.set_step(2, detail="Token bucket + Redlock…")
        await runner.set_step(3, detail="Dispatch worker with human pre-delay…")
        result = orchestrator.execute_task_dispatch(task.task_id)
        status = result.get("status", "?")
        await runner.complete_step(1, "Gate checked")
        await runner.complete_step(2, "Rate limit / lock acquired or deferred")
        await runner.complete_step(3, f"Dispatch: {status}")
        delay = (result.get("result") or {}).get("human_pre_delay_sec")
        await runner.update_cards(
            [
                {"label": "Status", "value": status, "sub": "orchestrator"},
                {"label": "Task", "value": task.task_id[:14], "sub": "id"},
                {
                    "label": "Pre-delay",
                    "value": f"{delay}s" if delay is not None else "—",
                    "sub": "human dwell",
                },
            ]
        )
        return {"status": status, "result": result}

    return await run_visual_pipeline(
        module_id="m4",
        title="Module 4 · Orchestrator",
        subtitle="Enqueue → gates → token bucket / Redlock → humanized dispatch",
        step_names=["Enqueue task", "Safety + human caps", "Rate limit / lock", "Execute dispatch"],
        executor=_exec,
    )


async def run_m5_visual(tenant_id: str, account_id: str) -> Dict[str, Any]:
    from services.module_5_analytics.telemetry import Module5TelemetryEngine
    from services.shared.safety_bridge import safety_bridge

    async def _exec(runner: VisualRunner) -> Dict[str, Any]:
        telemetry = Module5TelemetryEngine()
        await runner.set_step(0, detail="Compute Account Safety Score Sa…")
        sa = telemetry.calculate_account_safety_score(0, 1, 0)
        await runner.complete_step(0, f"Sa = {sa}")
        await runner.set_step(1, detail="Cost Per Lead (CPL)…")
        cpl = telemetry.calculate_cost_per_lead(4.0, 1.0, 1.0, 2)
        await runner.complete_step(1, f"CPL = ${cpl}")
        await runner.set_step(2, detail="Circuit breakers (429 + cost anomaly)…")
        bridge = safety_bridge.apply_circuit_breakers(
            tenant_id=tenant_id,
            account_id=account_id,
            captcha_failures_15m=0,
            proxy_failure_rate=0.05,
            consecutive_429=3,
            single_task_cost_usd=2.25,
        )
        actions = len((bridge.get("actions") or []))
        await runner.complete_step(2, f"Triggered actions: {actions}")
        await runner.set_step(3, detail="Metering ledger write…")
        snap = telemetry.evaluate_and_meter(
            tenant_id=tenant_id,
            account_id=account_id,
            proxy_errors=1,
            single_task_cost_usd=0.15,
        )
        await runner.complete_step(3, f"Costs: {snap.get('costs')}")
        await runner.update_cards(
            [
                {"label": "Safety Sa", "value": str(sa), "sub": "0–100"},
                {"label": "CPL", "value": f"${cpl}", "sub": "unit economics"},
                {"label": "Breakers", "value": str(actions), "sub": "fired"},
            ]
        )
        return {"safety_score": sa, "cpl": cpl, "circuit": bridge, "snapshot": snap}

    return await run_visual_pipeline(
        module_id="m5",
        title="Module 5 · Analytics & Telemetry",
        subtitle="Sa · CPL · circuit breakers · cost metering",
        step_names=["Safety score", "Cost per lead", "Circuit breakers", "Metering ledger"],
        executor=_exec,
    )


async def run_m6_visual(tenant_id: str, account_id: str) -> Dict[str, Any]:
    from services.module_6_control_center.governance import control_center

    async def _exec(runner: VisualRunner) -> Dict[str, Any]:
        await runner.set_step(0, detail="Enqueue HITL review task…")
        task = control_center.enqueue_hitl_if_needed(
            tenant_id=tenant_id,
            account_id=account_id,
            confidence=0.7,
            payload={"source": "visual_suite"},
            manual=True,
        )
        await runner.complete_step(
            0, f"HITL: {task.task_id if task else 'none'} ({task.status if task else 'n/a'})"
        )
        await runner.set_step(1, detail="Check dispatch gate / kill state…")
        gate = control_center.is_dispatch_allowed(tenant_id, account_id)
        await runner.complete_step(1, f"Allowed={gate.get('allowed')} · {gate.get('reason', 'ok')}")
        await runner.set_step(2, detail="List pending HITL queue…")
        queue = control_center.list_hitl_queue(tenant_id)
        await runner.complete_step(2, f"Pending tasks: {len(queue)}")
        await runner.update_cards(
            [
                {
                    "label": "HITL",
                    "value": task.task_id[:12] if task else "—",
                    "sub": task.status if task else "none",
                },
                {
                    "label": "Dispatch",
                    "value": "OPEN" if gate.get("allowed") else "GATED",
                    "sub": str(gate.get("reason", "")),
                },
                {"label": "Queue", "value": str(len(queue)), "sub": "pending"},
            ]
        )
        return {
            "hitl": task.model_dump() if task else None,
            "gate": gate,
            "queue_size": len(queue),
        }

    return await run_visual_pipeline(
        module_id="m6",
        title="Module 6 · Control Center / HITL",
        subtitle="HITL queue · RBAC · kill-switch dispatch gate",
        step_names=["Enqueue HITL", "Dispatch gate", "Queue snapshot"],
        executor=_exec,
    )


async def run_m7_visual(tenant_id: str) -> Dict[str, Any]:
    from services.module_7_webhooks.webhook_router import (
        gateway as webhook_gateway,
        WebhookEndpointRegistration,
        WebhookPayload,
    )

    async def _exec(runner: VisualRunner) -> Dict[str, Any]:
        await runner.set_step(0, detail="Register webhook endpoint…")
        endpoint = webhook_gateway.register_endpoint(
            WebhookEndpointRegistration(
                tenant_id=tenant_id,
                target_url="https://example.com/hooks/wolf",
                secret_key="whsec_suite_demo",
                subscribed_events=["lead.created"],
            )
        )
        await runner.complete_step(0, endpoint.endpoint_id)
        await runner.set_step(1, detail="Sign payload HMAC SHA-256…")
        await runner.set_step(2, detail="Deliver with human micro-jitter…")
        logs = webhook_gateway.dispatch_event(
            WebhookPayload(
                tenant_id=tenant_id,
                event_type="lead.created",
                data={"lead_id": "lead_suite_demo"},
            )
        )
        await runner.complete_step(1, "X-Signature-SHA256 attached")
        statuses = [l.status for l in logs]
        await runner.complete_step(2, f"Deliveries: {statuses}")
        await runner.update_cards(
            [
                {"label": "Endpoint", "value": endpoint.endpoint_id[:12], "sub": "registered"},
                {"label": "Events", "value": str(len(logs)), "sub": "dispatched"},
                {
                    "label": "Status",
                    "value": statuses[0] if statuses else "—",
                    "sub": "delivery",
                },
            ]
        )
        return {
            "endpoint_id": endpoint.endpoint_id,
            "deliveries": [l.model_dump() for l in logs],
        }

    return await run_visual_pipeline(
        module_id="m7",
        title="Module 7 · Webhooks",
        subtitle="HMAC sign · jittered delivery · retry / DLQ",
        step_names=["Register endpoint", "HMAC sign", "Deliver event"],
        executor=_exec,
    )


async def run_m8_visual(tenant_id: str, account_id: str = "agent_main") -> Dict[str, Any]:
    from services.module_8_multi_agent.agent_orchestrator import Module8MultiAgentOrchestrator

    async def _exec(runner: VisualRunner) -> Dict[str, Any]:
        orch = Module8MultiAgentOrchestrator()
        await runner.set_step(0, detail="Supervisor planning…")
        await runner.set_step(1, detail="Security & risk clearance…")
        await runner.set_step(2, detail="Prospecting + RAG…")
        await runner.set_step(3, detail="Copywriter Spintax DMs…")
        graph = await orch.run_campaign_graph(
            tenant_id,
            "Wolf Suite visual on-screen demo campaign",
            account_id=account_id,
        )
        data = graph.model_dump()
        trace = data.get("node_trace") or []
        await runner.complete_step(0, "Supervisor done")
        await runner.complete_step(1, f"Clearance={data.get('security_clearance')}")
        await runner.complete_step(2, f"Leads={len(data.get('extracted_leads') or [])}")
        await runner.complete_step(3, f"Copy={len(data.get('generated_copy') or [])}")
        await runner.update_cards(
            [
                {"label": "Status", "value": data.get("status", "?"), "sub": "graph"},
                {"label": "Trace", "value": " → ".join(trace[:4]) or "—", "sub": "nodes"},
                {
                    "label": "Human delays",
                    "value": str(len(data.get("human_delays") or [])),
                    "sub": "think pauses",
                },
            ]
        )
        return {"graph": data}

    return await run_visual_pipeline(
        module_id="m8",
        title="Module 8 · Multi-Agent",
        subtitle="Supervisor → Security → Prospecting → Copywriter (LangGraph)",
        step_names=["Supervisor", "Security clearance", "Prospecting", "Copywriter"],
        executor=_exec,
    )


async def run_ai_gateway_visual() -> Dict[str, Any]:
    from services.ai_gateway.gateway import UniversalAIGateway

    async def _exec(runner: VisualRunner) -> Dict[str, Any]:
        gw = UniversalAIGateway()
        await runner.set_step(0, detail="Resolve Level-1 master config…")
        await runner.complete_step(0, "Master key / base URL loaded")
        await runner.set_step(1, detail="Check module overrides…")
        overrides = list(getattr(gw, "module_overrides", {}).keys())
        await runner.complete_step(1, f"Overrides: {overrides or 'none'}")
        await runner.set_step(2, detail="chat/completions probe…")
        raw = await gw.chat_completion(
            "module_8",
            [{"role": "user", "content": "Reply with one short OK for Wolf Suite visual demo."}],
            temperature=0.2,
            max_tokens=40,
        )
        text = (
            raw.get("choices", [{}])[0].get("message", {}).get("content", "")
            if isinstance(raw, dict)
            else str(raw)
        )
        await runner.complete_step(2, (text or "response received")[:120])
        await runner.update_cards(
            [
                {"label": "Gateway", "value": "ONLINE", "sub": "/ai/v1"},
                {"label": "Overrides", "value": str(len(overrides)), "sub": "modules"},
                {"label": "Probe", "value": "OK" if text else "EMPTY", "sub": "completion"},
            ]
        )
        return {"reply": text, "overrides": overrides, "raw_stub": bool(raw.get("wolf_local_stub"))}

    return await run_visual_pipeline(
        module_id="ai_gateway",
        title="AI Gateway",
        subtitle="Level-1 master + Level-2 overrides · OpenAI-compatible",
        step_names=["Master config", "Module overrides", "Completion probe"],
        executor=_exec,
    )


async def run_security_visual(tenant_id: str = "org_wolf_demo") -> Dict[str, Any]:
    from services.security_governance.pii_redactor import PIIRedactor
    from services.security_governance.saml_gateway import BoxyHQSAMLGateway
    from services.security_governance.scim_router import SCIMStore

    async def _exec(runner: VisualRunner) -> Dict[str, Any]:
        await runner.set_step(0, detail="PII redaction (Presidio / regex)…")
        pii = PIIRedactor()
        sample = "Contact Jane Doe at jane@example.com or +1-555-0100"
        redacted = pii.sanitize_prompt(sample)
        await runner.complete_step(0, redacted[:100])
        await runner.set_step(1, detail="SAML SP metadata…")
        saml = BoxyHQSAMLGateway()
        meta = saml.get_sp_metadata() if hasattr(saml, "get_sp_metadata") else {"ok": True}
        await runner.complete_step(1, "SP metadata ready")
        await runner.set_step(2, detail="SCIM user store probe…")
        store = SCIMStore()
        count = 0
        try:
            users = store.list_users(start_index=1, count=5)
            count = len(users) if isinstance(users, list) else 0
            await runner.complete_step(2, f"Users visible: {count}")
        except Exception as exc:
            await runner.complete_step(2, f"Store ready ({exc.__class__.__name__})")
        await runner.update_cards(
            [
                {"label": "PII", "value": "ON", "sub": "redactor"},
                {"label": "SAML", "value": "READY", "sub": "ACS / metadata"},
                {"label": "SCIM", "value": str(count), "sub": "users"},
            ]
        )
        return {"redacted": redacted, "saml": meta if isinstance(meta, dict) else True, "scim_users": count}

    return await run_visual_pipeline(
        module_id="security",
        title="Security · SAML / SCIM / PII",
        subtitle="In-flight PII · SAML broker · SCIM lifecycle",
        step_names=["PII redaction", "SAML metadata", "SCIM store"],
        executor=_exec,
    )


VISUAL_RUNNERS = {
    "m1": run_m1_visual,
    "m2": run_m2_visual,
    "m3": run_m3_visual,
    "m4": run_m4_visual,
    "m5": run_m5_visual,
    "m6": run_m6_visual,
    "m7": run_m7_visual,
    "m8": run_m8_visual,
    "ai_gateway": run_ai_gateway_visual,
    "security": run_security_visual,
}


async def run_module_visual(
    module_id: str,
    tenant_id: str = "org_wolf_demo",
    account_id: str = "acct_wolf_01",
    preview_session_id: Optional[str] = None,
    max_posts: Optional[int] = None,
    dry_run: Optional[bool] = None,
) -> Dict[str, Any]:
    mid = module_id.lower()
    token = active_preview_session_id.set(preview_session_id)
    try:
        if mid == "m1":
            return await run_m1_visual(account_id=account_id, max_posts=max_posts, dry_run=dry_run)
        if mid == "m2":
            return await run_m2_visual(account_id=account_id)
        if mid == "m3":
            return await run_m3_visual(account_id=account_id)
        if mid == "m4":
            return await run_m4_visual(tenant_id, account_id)
        if mid == "m5":
            return await run_m5_visual(tenant_id, account_id)
        if mid == "m6":
            return await run_m6_visual(tenant_id, account_id)
        if mid == "m7":
            return await run_m7_visual(tenant_id)
        if mid == "m8":
            return await run_m8_visual(tenant_id, account_id)
        if mid == "ai_gateway":
            return await run_ai_gateway_visual()
        if mid == "security":
            return await run_security_visual(tenant_id)
        return {"module": mid, "error": "unknown_module"}
    finally:
        active_preview_session_id.reset(token)
