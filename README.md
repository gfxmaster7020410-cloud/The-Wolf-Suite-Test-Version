# Wolf Suite — Enterprise Autonomous Social Media & Multi-Agent Platform

**Wolf Suite** is an enterprise-grade autonomous social media automation, lead generation, and multi-agent orchestration platform built according to the 10 core architectural specifications.

## System Architecture Overview

```
+-----------------------------------------------------------------------------------+
|                        ENTERPRISE GOVERNANCE & SECURITY                           |
|      BoxyHQ SAML 2.0 | SCIM 2.0 | Presidio PII Redaction | Merkle Audit Trail       |
+-----------------------------------------------------------------------------------+
|                            UNIVERSAL AI GATEWAY                                   |
|      OpenAI REST Schema | Global Master Key | Per-Module Provider Overrides       |
+-----------------------------------------------------------------------------------+
|                          CORE AUTOMATION STACK                                    |
|  +------------------------+  +------------------------+  +---------------------+  |
|  | Module 1: Feed Observer|  | Module 2: Media Engine |  | Module 3: Lead/DM   |  |
|  | (Chromium/Playwright)  |  | (Dual-Stage AI Media)  |  | (Meta Graph / Ads)  |  |
|  +------------------------+  +------------------------+  +---------------------+  |
|                                         ^                                         |
|  +--------------------------------------+--------------------------------------+  |
|  | Module 4: Central Orchestration (FastAPI / Celery / Redis Rate Limiter)     |  |
|  +-----------------------------------------------------------------------------+  |
|  | Module 8: Multi-Agent Orchestration (LangGraph / Qdrant RAG / Self-Healing)  |  |
|  +-----------------------------------------------------------------------------+  |
+-----------------------------------------------------------------------------------+
|                          PLATFORM & OBSERVABILITY                                 |
|  +-----------------------+  +-----------------------+  +-----------------------+  |
|  | Module 5: Observability|  | Module 6: Next.js 14  |  | Module 7: API Gateway |  |
|  | (ClickHouse/Postgres) |  | (HITL Control Center) |  | (Cloudflare/Webhooks) |  |
|  +-----------------------+  +-----------------------+  +-----------------------+  |
+-----------------------------------------------------------------------------------+
```

## System Modules

* **Module 1**: Autonomous Feed Observer & Engagement Agent (Playwright, Vision AI, Human-Mimicking Bezier Cursors & Typing Cadence)
* **Module 2**: AI Media Processing, Multi-Platform Distribution & Engagement Engine (Dual-Stage Gemini AI, FB/IG/Threads Publishing)
* **Module 3**: Lead Capture, Conversational Messenger Funnels & Anti-Detection Sentinel (Comment-to-DM, Spintax, Ad Fatigue Guard)
* **Module 4**: Central Orchestration, Task Scheduling & Distributed Worker Architecture (FastAPI, Celery, Redis Token Bucket, Redlock)
* **Module 5**: Unified Analytics, System Observability & AI Telemetry (ClickHouse OLAP, Postgres OLTP, Safety Score $S_a$, Cost Metering)
* **Module 6**: Multi-Tenant Control Center, Security Governance & HITL Workflows (Next.js 14, RLS, Envelope Encryption, Kill-Switches)
* **Module 7**: External Ecosystem Integration, Webhook Gateways & Developer Platform (HMAC SHA-256, Exponential Retries, DLQ)
* **Module 8**: Autonomous Multi-Agent Orchestration & Self-Healing Engine (LangGraph State Machine, Vector RAG Memory, Pivot Loops)
* **Enterprise Security**: SAML 2.0 SSO, SCIM 2.0 Provisioning, Presidio PII Redactor, Merkle Audit Trails.
* **Universal AI Gateway**: OpenAI REST Schema Compatible (`/v1/chat/completions`) with Global & Per-Module Overrides.

## Quick Start

```bash
# 1) Infrastructure
docker compose up -d

# 2) Python deps
pip install -r requirements.txt

# 3) Apply DB schema
psql $DATABASE_URL -f db/schema.sql

# 4) Smoke integration (Modules 4↔5↔6 + KMS + Audit)
python scripts/smoke_integration.py

# 5) Unified API
uvicorn main:app --reload --port 8080

# 6) Control Center UI
cd apps/control_center && npm install && npm run dev
```

## Integration Wiring (Current)

* **Module 5 → Module 6:** CAPTCHA circuit breakers auto-issue Level 1 account kill-switches
* **Module 4 → Module 6:** Every dispatch checks kill-switch gate before rate-limit / Redlock
* **All safety events → Merkle Audit:** Hash-chained SIEM-ready audit trail
* **Unified API:** `main.py` mounts orchestrator, governance, webhooks, SAML, SCIM

## Repository Structure

```
f:\Project Wolf
├── apps/
│   └── control_center/          # Module 6: Next.js 14 Control Center (HITL / Kill-Switch UI)
├── db/
│   └── schema.sql               # PostgreSQL multi-tenant schema
├── scripts/
│   └── smoke_integration.py     # Cross-module smoke tests
├── main.py                      # Unified FastAPI entrypoint
├── services/
│   ├── ai_gateway/              # Universal AI Abstraction Gateway
│   ├── module_1_feed_observer/  # Module 1: Browser Automation & Feed Observer
│   ├── module_2_media_engine/   # Module 2: AI Media Synthesis & Multi-Platform Publisher
│   ├── module_3_lead_funnel/    # Module 3: Comment-to-DM, Spintax & Ad Guard
│   ├── module_4_orchestrator/   # Module 4: FastAPI Dispatcher & Celery Worker Pool
│   ├── module_5_analytics/      # Module 5: Telemetry, Safety Score & Cost Metering
│   ├── module_7_webhooks/       # Module 7: Webhook Gateway & Developer Portal
│   ├── module_8_multi_agent/    # Module 8: LangGraph Multi-Agent Orchestrator
│   └── security_governance/     # Enterprise SAML, SCIM, PII Redactor & KMS
├── docker-compose.yml           # Infrastructure Stack (Postgres, ClickHouse, Redis, Qdrant)
├── requirements.txt             # Global Python Dependencies
└── .env.example                 # Master Configuration Template
```
