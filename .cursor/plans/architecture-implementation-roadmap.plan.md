---
name: architecture-implementation-roadmap
overview: Comprehensive implementation roadmap for building all core services of Project Wolf according to SOFTWARE_SPECIFICATION.md.
todos:
  - id: impl-module-1
    content: Implement Module 1 Autonomous Feed Observer with Playwright & Bezier mechanics
    status: completed
  - id: impl-module-2
    content: Implement Module 2 AI Media Synthesis & Multi-Platform Publisher
    status: completed
  - id: impl-module-3
    content: Implement Module 3 Lead Funnel, Spintax, Ad Fatigue Guard & ROAS Auto-Scale
    status: completed
  - id: impl-module-4
    content: Implement Module 4 Central Orchestrator Dispatcher & Celery Worker Stack
    status: completed
  - id: impl-module-5
    content: Implement Module 5 Telemetry, Safety Score, CPL & Full Circuit Breakers
    status: completed
  - id: impl-module-7
    content: Implement Module 7 External Webhook Gateway with HMAC SHA-256 Signatures
    status: completed
  - id: impl-module-8
    content: Implement Module 8 Multi-Agent Orchestration Engine with LangGraph & RAG
    status: completed
  - id: impl-security-saml-scim
    content: Implement Enterprise Security SAML 2.0 Gateway & SCIM 2.0 Router
    status: completed
isProject: true
---

# Project Wolf — Service Implementation Roadmap

Based on [`f:\Project Wolf\SOFTWARE_SPECIFICATION.md`](f:\Project Wolf\SOFTWARE_SPECIFICATION.md). **All phases below are implemented** in the `services/` tree (local Redis/Qdrant fallbacks where infra is absent).

```mermaid
graph TD
    Phase1["Phase 1: Module 1 Feed Observer"] --> Phase2["Phase 2: Module 2 Media Engine"]
    Phase2 --> Phase3["Phase 3: Module 3 Lead Funnel"]
    Phase3 --> Phase4["Phase 4: Module 4 Orchestrator"]
    Phase4 --> Phase5["Phase 5: Module 5 Analytics"]
    Phase5 --> Phase6["Phase 6: Module 7 Webhooks"]
    Phase6 --> Phase7["Phase 7: Module 8 Multi-Agent"]
    Phase7 --> Phase8["Phase 8: SAML & SCIM Security"]
```

---

## Status Summary

| Phase | Module | Target File | Status |
| :--- | :--- | :--- | :--- |
| 1 | Feed Observer | `module_1_feed_observer/observer.py` + `gemini_bridge.py` | Done |
| 2 | Media Engine | `module_2_media_engine/media_processor.py` | Done |
| 3 | Lead Funnel | `module_3_lead_funnel/fb_automation_module3.py` | Done |
| 4 | Orchestrator | `module_4_orchestrator/dispatcher.py` + `celery_app.py` | Done |
| 5 | Analytics | `module_5_analytics/telemetry.py` | Done |
| 6 | Webhooks | `module_7_webhooks/webhook_router.py` | Done |
| 7 | Multi-Agent | `module_8_multi_agent/agent_orchestrator.py` | Done |
| 8 | SAML/SCIM | `security_governance/saml_gateway.py` + `scim_router.py` | Done |

### Phase notes
* **M1:** Facebook URL → screenshot → Gemini Web best comment → react/comment; Bezier + human delays.
* **M2:** Aspect validation, dual-stage captions, Graph publish (dry-run default), group shares, hooks.
* **M3:** Comment→DM + Spintax, fatigue pause, **+15% budget auto-scale** on 48h ROAS, 14-day warm-up caps.
* **M4:** Redis token bucket + Redlock (in-memory fallback), Celery, jittered backoff + DLQ.
* **M5:** Sa, CPL, metering ledger, all four circuit breakers wired through `safety_bridge`.
* **M7:** HMAC `X-Signature-SHA256`, 5-window retry + DLQ, OAuth scopes.
* **M8:** LangGraph DAG (sequential fallback), agents + Qdrant/in-memory RAG, self-healing.
* **Security:** SAML Jackson proxy or local JWT ACS; SCIM Users CRUD with soft-delete.
