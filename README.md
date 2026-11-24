# Wolf Suite

**An open-source, self-hosted Multi-Agent Orchestration & Enterprise AI Governance Control Plane.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688.svg)](https://fastapi.tiangolo.com/)
[![Next.js 14](https://img.shields.io/badge/Next.js-14-black.svg)](https://nextjs.org/)
[![Tests](https://img.shields.io/badge/tests-261%20passing-brightgreen.svg)](#observability--testing)

Wolf Suite is a modular control plane for running autonomous AI agents and automated
workflows against real business systems — with the guardrails that regulated teams need
**before** they can ship them: human approval gates, account- and tenant-level
kill-switches, PII redaction, tamper-evident audit trails, envelope encryption, and a
single governed gateway for every model provider.

It is built as eight services mounted behind one FastAPI control plane, with a Next.js 14
operator console for HITL review and emergency stop, and an OpenAI-compatible AI gateway
that lets each module use a different provider — Claude, Gemini, OpenAI, Groq, DeepSeek, or
local Ollama/vLLM — without changing application code.

**Design goal:** make multi-agent automation observable, budgeted, reversible, and
auditable by default, instead of bolting safety on afterwards.

> Full architecture, data model, and per-module specifications live in
> **[SOFTWARE_SPECIFICATION.md](SOFTWARE_SPECIFICATION.md)**. This README is the
> reviewer's entry point.

---

## Architecture

```
+---------------------------------------------------------------------------------------+
|   OPERATOR CONSOLE  -  Next.js 14 / React 18                                          |
|   HITL review queue | Level 1-3 kill-switch | workflow config | live preview          |
+---------------------------------------------------------------------------------------+
                                            |
                                            v
+---------------------------------------------------------------------------------------+
|   UNIFIED CONTROL PLANE  -  FastAPI :8080                                             |
|   /v1/chat/completions | /api/v1/hitl | /api/v1/kill-switch                           |
|   /scim/v2/Users | /sso/saml | /health | /api/v1/suite/overview                       |
+---------------------------------------------------------------------------------------+
                                            |
                                            v
+---------------------------------------------------------------------------------------+
|   SERVICE MODULES                                                                     |
|                                                                                       |
|   M4 Orchestrator      Celery workers | Redis token bucket | Redlock locks | DLQ      |
|   M6 Governance        HITL queue | Level 1-3 kill-switch | RBAC | dispatch gate      |
|   M7 Integration       HMAC-signed webhooks | retries with jitter | OAuth scopes      |
|   Security Governance  SAML 2.0 | SCIM 2.0 | KMS envelope crypto | PII | audit chain  |
+---------------------------------------------------------------------------------------+
                                            |
                                            v
+---------------------------------------------------------------------------------------+
|   AUTOMATION WORKLOADS                                                                |
|                                                                                       |
|   M1 Workflow Automation | M2 Media Engine | M3 Lead Operations | M8 Multi-Agent      |
|   LangGraph supervisor + self-healing reflection + Qdrant RAG memory                  |
+---------------------------------------------------------------------------------------+
                                            |
                                            v
+---------------------------------------------------------------------------------------+
|   UNIVERSAL AI GATEWAY  -  OpenAI-compatible /v1/chat/completions                     |
|   Claude | Gemini | OpenAI | Groq | DeepSeek | Ollama / vLLM (per-module overrides)   |
+---------------------------------------------------------------------------------------+
                                            |
                                            v
+---------------------------------------------------------------------------------------+
|   DATA & OBSERVABILITY                                                                |
|                                                                                       |
|   PostgreSQL (OLTP) | Redis | ClickHouse (OLAP) | Qdrant (RAG) | safety + cost        |
+---------------------------------------------------------------------------------------+
```

Everything is reachable through one process: `main.py` mounts the orchestration API,
governance API, webhook gateway, SAML/SCIM endpoints, and the AI gateway on port `8080`.
The operator console is a separate Next.js app on port `3000`.

---

## Highlights

| Area | What is implemented |
| :--- | :--- |
| **AI safety & governance** | Human-in-the-Loop approval queue, Level 1–3 kill-switches (account → tenant → global freeze), RBAC permission matrix, and a dispatch gate evaluated before every task execution |
| **Auditability** | Append-only, hash-chained (Merkle-style) audit trail with chain verification and SIEM export hooks (Splunk HEC / Datadog over TLS) |
| **Privacy** | In-flight PII redaction (Microsoft Presidio, deterministic regex fallback) applied before LLM dispatch and vector persistence |
| **Cryptography** | AES-256-GCM envelope encryption for credentials, cookies, and tokens, using a KMS/Vault-compatible master-key pattern |
| **Identity** | SAML 2.0 service-provider endpoints via the BoxyHQ Jackson pattern, plus SCIM 2.0 (RFC 7643/7644) user lifecycle management |
| **Universal AI Gateway** | One OpenAI-compatible endpoint (`/v1/chat/completions`) with Level 1 global config and Level 2 per-module provider overrides, plus an offline stub so pipelines run without cloud keys |
| **Orchestration** | Celery worker pool, Redis token-bucket rate limiting, Redlock distributed locks, jittered exponential backoff, and a dead-letter queue |
| **Observability** | Account Safety Score, cost-per-lead, append-only cost metering ledger, and circuit breakers for failure storms, proxy errors, HTTP 429 cooling, and token-cost anomalies |
| **Operator console** | Next.js 14 App Router UI for HITL review, kill-switch control, module configuration, and live workflow previews |

---

## Modules

| Module | Responsibility |
| :--- | :--- |
| **M1 — Workflow Automation** | Playwright-driven executor for authorized, rate-limited browser workflows: navigation, content capture, AI-assisted drafting, and policy-gated actions with per-account pacing ceilings plus start/pause/resume/skip controls |
| **M2 — Media Engine** | Two-stage generation-then-selection of captions and hashtags from media assets, followed by multi-destination publishing |
| **M3 — Lead Operations** | Comment-to-DM routing through the Meta Graph API, templated (spintax) responses, and campaign guardrails including ad-fatigue checks and capped budget scaling |
| **M4 — Orchestrator** | FastAPI dispatcher, Celery workers, Redis token-bucket limits, Redlock locks, retry policy, and DLQ |
| **M5 — Analytics** | Safety scoring, CPL, cost metering, circuit breakers, and incident logging |
| **M6 — Governance** | Multi-tenant control center API: RBAC, HITL queue, kill-switches, and the dispatch gate |
| **M7 — Integration** | HMAC-SHA256 signed webhooks, exponential retries with jitter, DLQ, and OAuth scope enforcement |
| **M8 — Multi-Agent** | LangGraph state machine with supervisor and specialist agents, self-healing reflection, and Qdrant-backed RAG memory (in-memory fallback) |
| **AI Gateway** | Provider-agnostic routing with per-module overrides and a deterministic local stub |
| **Security Governance** | SAML, SCIM, PII redaction, KMS envelope crypto, and the Merkle audit chain |

---

## Enterprise security & AI safety

### Kill-switches and HITL

Every dispatch passes through a single gate (`/api/v1/dispatch/allowed`) before rate
limiting or locking. A stop at any level denies execution immediately:

| Level | Scope | Effect |
| :--- | :--- | :--- |
| **1** | Account | Pauses one automated account |
| **2** | Tenant | Pauses all accounts in a workspace |
| **3** | Global | Freezes the platform and blocks every account |

HITL tasks are enqueued with a confidence score and can be approved or rejected through
`/api/v1/hitl/{task_id}/review`. The RBAC matrix (`/api/v1/rbac/matrix`) gates every
console action by role.

### Tamper-evident audit

Audit records are chained with HMAC-SHA256 over the previous record hash, so any edit or
deletion breaks verification:

```python
from services.shared.safety_bridge import safety_bridge

safety_bridge.audit.verify_chain()   # -> True only if the chain is intact
```

### Privacy and secrets

```python
from services.security_governance.pii_redactor import PIIRedactor
from services.security_governance.kms_crypto import KMSCryptoEngine

PIIRedactor().redact("Email jane@example.com or call 555-010-9999")
# -> "Email <EMAIL_ADDRESS> or call <PHONE_NUMBER>"

kms = KMSCryptoEngine()
sealed = kms.encrypt("account-session-token")   # AES-256-GCM, per-record DEK
assert kms.decrypt(sealed) == "account-session-token"
```

---

## Universal AI Gateway

One OpenAI-compatible contract for every provider. Modules call the gateway, never a
vendor SDK, so a model can be swapped per module by changing environment variables.

```bash
curl -s http://localhost:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
        "module": "module_8",
        "model": "<provider-model-id>",
        "messages": [{"role": "user", "content": "Summarise this lead in one line."}]
      }'
```

| Scope | Environment variables |
| :--- | :--- |
| Level 1 — global master | `MASTER_AI_BASE_URL`, `MASTER_AI_API_KEY`, `MASTER_AI_MODEL` |
| Level 2 — Module 1 override | `MOD1_AI_BASE_URL`, `MOD1_AI_API_KEY`, `MOD1_AI_MODEL` |
| Level 2 — Module 2 override | `MOD2_AI_BASE_URL`, `MOD2_AI_API_KEY`, `MOD2_AI_MODEL` |
| Level 2 — Module 3 override | `MOD3_AI_BASE_URL`, `MOD3_AI_API_KEY`, `MOD3_AI_MODEL` |
| Level 2 — Module 8 override | `MOD8_AI_BASE_URL`, `MOD8_AI_API_KEY`, `MOD8_AI_MODEL` |

Any endpoint that speaks the OpenAI chat schema works, including Anthropic (Claude),
Google Gemini, OpenAI, Groq, DeepSeek, and self-hosted Ollama or vLLM. If no key is
configured, the gateway returns deterministic local stubs so tests and demos run offline.
`GET /v1/models` reports the resolved routing table with secrets stripped.

---

## Observability & testing

- **261 automated tests** across 17 files, covering the M1 worker, scheduler, rotation,
  checkpointing, circuit breakers, safety bridge, and governance paths:
  `pytest -q` → `261 passed in ~18s`.
- **Cross-module smoke test** — `python scripts/smoke_integration.py` verifies the real
  wiring: safety-score banding, CPL maths, CAPTCHA-storm circuit breaker → Module 6 account
  pause, tenant/global freeze gating, KMS encrypt/decrypt round-trip, and Merkle chain
  verification.
- **Metrics that matter operationally**: Account Safety Score
  `Sa = 100 − (w1·CaptchaFails + w2·ProxyErrors + w3·RateThrottles)`, cost-per-lead, and a
  per-tenant cost metering ledger.
- **Circuit breakers** trigger on CAPTCHA failure storms, proxy-pool degradation, repeated
  HTTP 429s (cooling window extended), and single-task token-cost anomalies.

---

## Quick start

**Prerequisites:** Python 3.11+, Node.js 18+, Docker (optional but recommended).

```bash
# 1. Clone and configure
git clone https://github.com/gfxmaster7020410-cloud/The-Wolf-Suite-Test-Version.git
cd The-Wolf-Suite-Test-Version
cp .env.example .env          # Windows: copy .env.example .env

# 2. Infrastructure: PostgreSQL, Redis, ClickHouse, Qdrant
docker compose up -d

# 3. Python environment
python -m venv .venv
source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 4. Test before you run
pytest -q

# 5. Apply the PostgreSQL schema (once the database is up)
psql "$DATABASE_URL" -f db/schema.sql

# 6. Unified control plane -> http://localhost:8080/docs
uvicorn main:app --reload --port 8080

# 7. Operator console -> http://localhost:3000
cd apps/control_center && npm install && npm run dev
```

Optional verification and helpers:

```bash
python scripts/smoke_integration.py     # cross-module integration checks
python scripts/run_feed_observer.py     # M1 workflow runner (authorized accounts only)
```

No Docker? Set `REDIS_FORCE_MEMORY=true` and `QDRANT_FORCE_MEMORY=true` in `.env` to use
the in-memory fallbacks — that is how the test suite runs.

---

## Configuration

All configuration is environment-driven; `.env.example` is the annotated reference.
Never commit `.env` — it is git-ignored along with browser profiles, runtime data, and
local databases.

| Variable group | Purpose |
| :--- | :--- |
| `MASTER_AI_*`, `MOD{1,2,3,8}_AI_*` | AI gateway routing and per-module overrides |
| `POSTGRES_*`, `DATABASE_URL` | OLTP storage |
| `REDIS_URL`, `REDIS_FORCE_MEMORY` | Rate limiting, locks, Celery broker |
| `QDRANT_URL`, `QDRANT_FORCE_MEMORY` | Vector memory for Module 8 |
| `KMS_MASTER_KEY`, `JWT_SECRET` | Envelope encryption and session signing |
| `SAML_SP_ENTITY_ID`, `BOXYHQ_JACKSON_*` | Enterprise SSO |
| `META_APP_SECRET`, `META_VERIFY_TOKEN` | Webhook verification and signing |

---

## Repository layout

```
.
├── main.py                      # Unified FastAPI control plane (:8080)
├── services/
│   ├── ai_gateway/              # OpenAI-compatible universal gateway
│   ├── module_1_feed_observer/  # Workflow automation executor
│   ├── module_2_media_engine/   # Media generation and publishing
│   ├── module_3_lead_funnel/    # Lead operations and API orchestration
│   ├── module_4_orchestrator/   # Dispatcher, Celery, rate limits, DLQ
│   ├── module_5_analytics/      # Safety score, CPL, cost ledger, breakers
│   ├── module_6_control_center/ # RBAC, HITL, kill-switches, dispatch gate
│   ├── module_7_webhooks/       # Signed webhooks, retries, OAuth scopes
│   ├── module_8_multi_agent/    # LangGraph agents + Qdrant RAG memory
│   ├── security_governance/     # SAML, SCIM, PII, KMS, Merkle audit
│   └── shared/                  # Redis shim, safety bridge, pacing, stores
├── apps/control_center/         # Next.js 14 operator console
├── db/schema.sql                # PostgreSQL multi-tenant schema (9 tables)
├── scripts/                     # Smoke tests, runners, local bootstrap
├── tests/                       # 261 tests across 17 files
├── docker-compose.yml           # PostgreSQL, Redis, ClickHouse, Qdrant
├── requirements.txt
├── SOFTWARE_SPECIFICATION.md    # Full architecture and module specification
└── LICENSE                      # MIT
```

---

## Project status

Honest maturity map, so reviewers can tell implemented code from provisioning:

| Capability | State |
| :--- | :--- |
| Eight modules, unified API, 261 tests | **Implemented and green** |
| Kill-switches 1–3, HITL, RBAC, dispatch gate | **Implemented and test-covered** |
| Merkle audit chain, KMS envelope crypto, PII redaction | **Implemented and test-covered** |
| SAML 2.0 / SCIM 2.0 | **Implemented** — SAML proxies BoxyHQ Jackson when configured, otherwise issues local signed sessions for development |
| Redis, Qdrant, Celery | **Implemented with in-memory/inline fallbacks** for Docker-free development |
| ClickHouse OLAP | **Provisioned in `docker-compose.yml`**; the current analytics sink writes to SQLite locally and PostgreSQL in production, with the ClickHouse adapter on the roadmap |

---

## Responsible use

Wolf Suite automates workflows against third-party platforms. Use it only with accounts
you own or are explicitly authorized to operate, and only where the platform's Terms of
Service and applicable law permit automation.

- Prefer official APIs and documented OAuth scopes (the Meta Graph API path in Module 3)
  over browser automation wherever an API exists.
- Browser workflows in Module 1 are intended for testing your own workflows on platforms
  that allow automation.
- The built-in pacing limits, per-account ceilings, HITL approvals, audit trail, and
  kill-switches exist to keep deployments inside those limits — configure them and keep
  them enabled.
- You are responsible for the configuration you deploy and for the content your agents
  produce.

---

## Contributing

Contributions are welcome — especially around provider adapters, ClickHouse telemetry,
policy evaluation, and test coverage.

1. Fork the repository and create a branch: `feat/short-description`.
2. Install dependencies and run `pytest -q`; all 261 tests must stay green.
3. Keep pull requests focused, and describe the module touched plus any safety impact.
4. Update `SOFTWARE_SPECIFICATION.md` when you change an architecture contract.
5. Report security issues privately via a GitHub security advisory — not a public issue.
6. By contributing you agree that your work is licensed under the MIT License.

---

## License

Released under the [MIT License](LICENSE). Copyright (c) 2026 gfxmaster7020410-cloud.

See **[SOFTWARE_SPECIFICATION.md](SOFTWARE_SPECIFICATION.md)** for the full architecture,
data model, and module-level technical specification.
