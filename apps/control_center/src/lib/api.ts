/** Default operator session for local Control Center development. */
export const OPERATOR_HEADERS: Record<string, string> = {
  "Content-Type": "application/json",
  "X-User-Id": "op_wolf_admin",
  "X-Tenant-Id": "org_wolf_demo",
  "X-User-Email": "admin@wolf.local",
  "X-User-Role": "SuperAdmin",
};

const GOVERNANCE = "/api/governance";

async function parseJson<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export type HitlTask = {
  task_id: string;
  tenant_id: string;
  account_id: string;
  reason: string;
  confidence?: number | null;
  status: string;
  payload?: Record<string, unknown>;
};

export type KillSwitchEvent = {
  event_id: string;
  level: number;
  tenant_id?: string | null;
  account_id?: string | null;
  reason: string;
  issued_by: string;
  active: boolean;
};

export async function fetchHitlQueue(): Promise<HitlTask[]> {
  const res = await fetch(`${GOVERNANCE}/hitl/queue`, {
    headers: OPERATOR_HEADERS,
    cache: "no-store",
  });
  return parseJson<HitlTask[]>(res);
}

export async function seedHitlDemo(): Promise<unknown> {
  const params = new URLSearchParams({
    tenant_id: "org_wolf_demo",
    account_id: "acct_wolf_01",
    confidence: "0.72",
    first_touch: "false",
    high_intent: "false",
  });
  const res = await fetch(`${GOVERNANCE}/hitl/enqueue?${params}`, {
    method: "POST",
    headers: OPERATOR_HEADERS,
  });
  return parseJson(res);
}

export async function reviewHitl(taskId: string, approve: boolean): Promise<HitlTask> {
  const res = await fetch(
    `${GOVERNANCE}/hitl/${encodeURIComponent(taskId)}/review?approve=${approve}`,
    { method: "POST", headers: OPERATOR_HEADERS }
  );
  return parseJson<HitlTask>(res);
}

export async function issueKillSwitch(input: {
  level: number;
  reason: string;
  account_id?: string;
  tenant_id?: string;
}): Promise<KillSwitchEvent> {
  const body = {
    level: input.level,
    reason: input.reason || "operator action",
    tenant_id: input.tenant_id || "org_wolf_demo",
    account_id: input.account_id || (input.level === 1 ? "acct_wolf_01" : null),
  };
  const res = await fetch(`${GOVERNANCE}/kill-switch`, {
    method: "POST",
    headers: OPERATOR_HEADERS,
    body: JSON.stringify(body),
  });
  return parseJson<KillSwitchEvent>(res);
}

export async function fetchKillEvents(): Promise<KillSwitchEvent[]> {
  const res = await fetch(`${GOVERNANCE}/kill-switch/events`, {
    headers: OPERATOR_HEADERS,
    cache: "no-store",
  });
  return parseJson<KillSwitchEvent[]>(res);
}

export async function fetchHealth(): Promise<{ status: string; service: string }> {
  const res = await fetch("/api/platform/health", { cache: "no-store" });
  return parseJson(res);
}

export type SafetyDashboard = {
  pending_hitl: number;
  active_kill_events: number;
  audit_events: number;
  paused_accounts: number;
  paused_tenants: number;
  global_freeze: boolean;
  memory_pending_hitl: number;
  memory_paused_accounts: number;
  memory_global_freeze: boolean;
  audit_chain_verified: boolean;
  in_memory_audit_count: number;
  db_path: string;
};

export type AuditResponse = {
  verified: boolean;
  count: number;
  events: Array<{
    event_id: string;
    timestamp: string;
    actor: string;
    action: string;
    module: string;
  }>;
};

export async function fetchSafetyDashboard(
  tenantId = "org_wolf_demo"
): Promise<SafetyDashboard> {
  const res = await fetch(
    `/api/platform/api/v1/safety/dashboard?tenant_id=${encodeURIComponent(tenantId)}`,
    { cache: "no-store" }
  );
  return parseJson<SafetyDashboard>(res);
}

export async function fetchAuditTrail(limit = 8): Promise<AuditResponse> {
  const res = await fetch(`/api/platform/api/v1/safety/audit?limit=${limit}`, {
    cache: "no-store",
  });
  return parseJson<AuditResponse>(res);
}

export type SuiteModule = {
  id: string;
  name: string;
  role: string;
  status: string;
  metric?: string;
  depends_on: string[];
  href: string;
};

export type SuiteOverview = {
  product: string;
  tenant_id: string;
  global_freeze: boolean;
  dispatch_allowed: boolean;
  stats: SafetyDashboard;
  humanization?: HumanizationSnapshot;
  modules: SuiteModule[];
  connections: Array<{ from: string; to: string }>;
};

export type HumanizationSnapshot = {
  profile_name: string;
  enabled: boolean;
  warmup_multiplier: number;
  effective_max_actions_per_hour: number;
  effective_max_actions_per_day: number;
  key_delay_min: number;
  key_delay_max: number;
  action_delay_min: number;
  action_delay_max: number;
  group_share_delay_min: number;
  group_share_delay_max: number;
  typo_probability: number;
  behaviors?: string[];
  cap?: {
    allowed: boolean;
    hourly_used: number;
    hourly_cap: number;
    daily_used: number;
    daily_cap: number;
    warmup_day: number;
  };
};

export async function fetchHumanization(): Promise<HumanizationSnapshot> {
  const res = await fetch("/api/platform/api/v1/humanization/profile", {
    cache: "no-store",
  });
  return parseJson<HumanizationSnapshot>(res);
}

export async function updateHumanization(
  body: Partial<{
    profile_name: string;
    enabled: boolean;
    max_actions_per_hour: number;
    max_actions_per_day: number;
  }>
): Promise<HumanizationSnapshot> {
  const res = await fetch("/api/platform/api/v1/humanization/profile", {
    method: "POST",
    headers: OPERATOR_HEADERS,
    body: JSON.stringify(body),
  });
  return parseJson<HumanizationSnapshot>(res);
}

export async function fetchSuiteOverview(
  tenantId = "org_wolf_demo"
): Promise<SuiteOverview> {
  const res = await fetch(
    `/api/platform/api/v1/suite/overview?tenant_id=${encodeURIComponent(tenantId)}`,
    { cache: "no-store" }
  );
  return parseJson<SuiteOverview>(res);
}

export type PreviewStep = {
  name: string;
  detail: string;
  status: string;
};

export type PreviewSession = {
  session_id?: string;
  module_id?: string;
  title?: string;
  subtitle?: string;
  status: string;
  cards?: Array<{ label: string; value: string; sub?: string }>;
  steps?: PreviewStep[];
  result?: unknown;
};

export async function runSuiteModule(
  moduleId: string,
  options?: { max_posts?: number; dry_run?: boolean }
): Promise<{
  preview_session_id?: string;
  status?: string;
  module?: string;
  [key: string]: unknown;
}> {
  const res = await fetch("/api/platform/api/v1/suite/run", {
    method: "POST",
    headers: OPERATOR_HEADERS,
    body: JSON.stringify({
      module_id: moduleId,
      tenant_id: "org_wolf_demo",
      account_id: "acct_wolf_01",
      max_posts: options?.max_posts,
      dry_run: options?.dry_run,
    }),
  });
  return parseJson(res);
}

export type Module1Config = {
  user_data_dir: string;
  facebook_url: string;
  gemini_mode: string;
  gemini_url: string;
  default_reaction: string;
  live_engage: boolean;
  comment_paste: boolean;
  default_posts: number;
  wait_time?: number;
  loop?: string;
} & Record<string, any>;

export type Module1Status = {
  running: boolean;
  comments: number;
  likes: number;
  posts_seen?: number;
  last_comment?: string;
  last_error?: string;
  wait_time?: number;
  phase?: string;
  phase_detail?: string;
  profile?: string;
  paused?: boolean;
  stop_reason?: string;
  posts_engaged?: number;
  posts_skipped?: number;
  comments_done?: number;
  likes_done?: number;
  last_ai_source?: string;
  gemini_errors?: number;
  ai_fail_streak?: number;
  ai_breaker_tripped?: boolean;
  hourly_used?: number;
  hourly_cap?: number;
  daily_used?: number;
  daily_cap?: number;
  seen_count?: number;
  uptime_sec?: number;
  checkpoint_since?: number;
  browser_left_open?: boolean;
  schedule_mode?: string;
  next_scheduled_action?: string;
  last_auto_start?: string;
};

export type Module1Meta = {
  enums: Record<string, string[]>;
  help: Record<string, string>;
  secret_fields: string[];
};

export type Module1SaveResult = {
  ok?: boolean;
  error?: string;
  warnings?: string[];
  settings?: Record<string, any>;
};

export async function fetchModule1Meta(): Promise<Module1Meta> {
  const res = await fetch("/api/platform/api/v1/module1/config/meta", { cache: "no-store" });
  return parseJson<Module1Meta>(res);
}

export async function saveModule1Config(
  patch: Record<string, unknown>,
): Promise<Module1SaveResult> {
  const res = await fetch("/api/platform/api/v1/module1/config", {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...OPERATOR_HEADERS },
    body: JSON.stringify(patch),
  });
  return parseJson(res);
}

export async function testModule1AI(): Promise<{
  ok?: boolean;
  latency_ms?: number;
  source?: string;
  error?: string;
  key_configured?: boolean;
}> {
  const res = await fetch("/api/platform/api/v1/module1/test-ai", {
    method: "POST",
    headers: OPERATOR_HEADERS,
    body: JSON.stringify({}),
  });
  return parseJson(res);
}

export async function clearModule1Seen(): Promise<{ ok?: boolean; cleared?: number }> {
  const res = await fetch("/api/platform/api/v1/module1/seen/clear", {
    method: "POST",
    headers: OPERATOR_HEADERS,
  });
  return parseJson(res);
}

export async function fetchModule1Config(): Promise<Module1Config> {
  const res = await fetch("/api/platform/api/v1/module1/config", { cache: "no-store" });
  return parseJson<Module1Config>(res);
}

export async function startModule1(waitTime = 3): Promise<Module1Status & { ok?: boolean; message?: string; error?: string }> {
  const res = await fetch("/api/platform/api/v1/module1/start", {
    method: "POST",
    headers: OPERATOR_HEADERS,
    body: JSON.stringify({ wait_time: waitTime }),
  });
  return parseJson(res);
}

export async function stopModule1(): Promise<Module1Status & { ok?: boolean; message?: string }> {
  const res = await fetch("/api/platform/api/v1/module1/stop", {
    method: "POST",
    headers: OPERATOR_HEADERS,
  });
  return parseJson(res);
}

export async function fetchModule1Status(): Promise<Module1Status> {
  const res = await fetch("/api/platform/api/v1/module1/status", { cache: "no-store" });
  return parseJson<Module1Status>(res);
}

export async function fetchSuitePreview(sessionId: string): Promise<PreviewSession> {
  const path =
    sessionId === "latest"
      ? "/api/platform/api/v1/suite/preview/latest"
      : `/api/platform/api/v1/suite/preview/${encodeURIComponent(sessionId)}`;
  const res = await fetch(path, { cache: "no-store" });
  return parseJson<PreviewSession>(res);
}
