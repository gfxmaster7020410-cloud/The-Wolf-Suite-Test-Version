-- Project Wolf — Core PostgreSQL Schema
-- Aligns with SOFTWARE_SPECIFICATION.md Sections 13.x + Module 4/5/6 entities

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ---------------------------------------------------------------------------
-- Multi-tenant workspaces
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tenants (
    id              VARCHAR(64) PRIMARY KEY,
    name            VARCHAR(255) NOT NULL,
    plan_tier       VARCHAR(32) NOT NULL DEFAULT 'standard',
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- SCIM / IAM users (RFC 7643)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS scim_users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       VARCHAR(64) NOT NULL REFERENCES tenants(id),
    external_id     VARCHAR(255) UNIQUE NOT NULL,
    user_name       VARCHAR(255) NOT NULL,
    email           VARCHAR(255) NOT NULL,
    is_active       BOOLEAN DEFAULT TRUE,
    role_mapping    JSONB NOT NULL DEFAULT '[]',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_scim_tenant_ext ON scim_users(tenant_id, external_id);

-- ---------------------------------------------------------------------------
-- Social profiles / automation accounts
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS social_accounts (
    id              VARCHAR(64) PRIMARY KEY,
    tenant_id       VARCHAR(64) NOT NULL REFERENCES tenants(id),
    platform        VARCHAR(32) NOT NULL,
    display_name    VARCHAR(255),
    encrypted_session TEXT,
    safety_score    NUMERIC(5, 2) NOT NULL DEFAULT 100.00,
    is_paused       BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_accounts_tenant ON social_accounts(tenant_id);

-- ---------------------------------------------------------------------------
-- Module 4 task state store
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS orchestration_tasks (
    task_id         VARCHAR(64) PRIMARY KEY,
    tenant_id       VARCHAR(64) NOT NULL REFERENCES tenants(id),
    account_id      VARCHAR(64) REFERENCES social_accounts(id),
    module_target   VARCHAR(32) NOT NULL,
    action_type     VARCHAR(64) NOT NULL,
    state           VARCHAR(16) NOT NULL DEFAULT 'ENQUEUED',
    attempt_count   INT NOT NULL DEFAULT 0,
    max_retries     INT NOT NULL DEFAULT 3,
    payload_data    JSONB NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_tasks_state ON orchestration_tasks(state);
CREATE INDEX IF NOT EXISTS idx_tasks_tenant ON orchestration_tasks(tenant_id, created_at);

-- ---------------------------------------------------------------------------
-- Module 3 leads
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS leads (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       VARCHAR(64) NOT NULL REFERENCES tenants(id),
    external_user_id VARCHAR(128),
    display_name    VARCHAR(255),
    source_post_id  VARCHAR(128),
    status          VARCHAR(32) NOT NULL DEFAULT 'new',
    conversation    JSONB NOT NULL DEFAULT '[]',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_leads_tenant ON leads(tenant_id, created_at);

-- ---------------------------------------------------------------------------
-- Module 6 HITL queue
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS hitl_tasks (
    task_id         VARCHAR(64) PRIMARY KEY,
    tenant_id       VARCHAR(64) NOT NULL REFERENCES tenants(id),
    account_id      VARCHAR(64),
    reason          VARCHAR(64) NOT NULL,
    confidence      NUMERIC(5, 4),
    payload         JSONB NOT NULL DEFAULT '{}',
    status          VARCHAR(16) NOT NULL DEFAULT 'PENDING',
    reviewed_by     VARCHAR(128),
    reviewed_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_hitl_tenant_status ON hitl_tasks(tenant_id, status);

-- ---------------------------------------------------------------------------
-- Module 5 / Enterprise cost metering ledger
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cost_metering_ledger (
    id                  BIGSERIAL PRIMARY KEY,
    tenant_id           VARCHAR(64) NOT NULL,
    cost_center_id      VARCHAR(64) NOT NULL,
    module_name         VARCHAR(32) NOT NULL,
    resource_type       VARCHAR(32) NOT NULL,
    units_consumed      NUMERIC(14, 4) NOT NULL,
    calculated_cost_usd NUMERIC(10, 6) NOT NULL,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_metering_tenant_cc
    ON cost_metering_ledger(tenant_id, cost_center_id, created_at);

-- ---------------------------------------------------------------------------
-- Immutable audit trail (Merkle chain metadata)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_events (
    event_id        VARCHAR(64) PRIMARY KEY,
    tenant_id       VARCHAR(64) NOT NULL,
    actor           VARCHAR(255) NOT NULL,
    action          VARCHAR(128) NOT NULL,
    module          VARCHAR(64) NOT NULL,
    payload_hash    CHAR(64) NOT NULL,
    prev_block_hash CHAR(64) NOT NULL,
    block_hash      CHAR(64) NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_audit_tenant ON audit_events(tenant_id, created_at);

-- ---------------------------------------------------------------------------
-- Kill-switch events
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS kill_switch_events (
    event_id        VARCHAR(64) PRIMARY KEY,
    level           SMALLINT NOT NULL CHECK (level BETWEEN 1 AND 3),
    tenant_id       VARCHAR(64),
    account_id      VARCHAR(64),
    reason          TEXT NOT NULL,
    issued_by       VARCHAR(128) NOT NULL,
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    issued_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
