"""
Module 7: External Webhook Gateway — HMAC SHA-256 signing, real HTTP delivery,
5-window exponential retry + DLQ, OAuth2 scope validation.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Set

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from services.shared.redis_client import get_redis


class OAuth2Scope:
    LEADS_READ = "leads:read"
    LEADS_WRITE = "leads:write"
    CAMPAIGNS_READ = "campaigns:read"
    CAMPAIGNS_CONTROL = "campaigns:control"
    WEBHOOKS_MANAGE = "webhooks:manage"
    SYSTEM_ADMIN = "system:admin"

    ALL = {
        LEADS_READ,
        LEADS_WRITE,
        CAMPAIGNS_READ,
        CAMPAIGNS_CONTROL,
        WEBHOOKS_MANAGE,
        SYSTEM_ADMIN,
    }


EVENT_REQUIRED_SCOPE = {
    "lead.created": OAuth2Scope.LEADS_READ,
    "lead.updated": OAuth2Scope.LEADS_WRITE,
    "campaign.stats": OAuth2Scope.CAMPAIGNS_READ,
    "campaign.pause": OAuth2Scope.CAMPAIGNS_CONTROL,
    "campaign.resume": OAuth2Scope.CAMPAIGNS_CONTROL,
}


class WebhookEndpointRegistration(BaseModel):
    endpoint_id: str = Field(default_factory=lambda: f"wh_{uuid.uuid4().hex[:8]}")
    tenant_id: str
    target_url: str
    secret_key: str
    subscribed_events: List[str]
    scopes: List[str] = Field(
        default_factory=lambda: [OAuth2Scope.LEADS_READ, OAuth2Scope.CAMPAIGNS_READ]
    )
    is_active: bool = True


class WebhookPayload(BaseModel):
    event_id: str = Field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:8]}")
    tenant_id: str
    event_type: str
    data: Dict[str, Any] = Field(default_factory=dict)
    timestamp: float = Field(default_factory=time.time)


class WebhookDeliveryLog(BaseModel):
    delivery_id: str = Field(default_factory=lambda: f"del_{uuid.uuid4().hex[:8]}")
    payload_id: str
    endpoint_id: str
    attempt_count: int = 0
    http_status_code: Optional[int] = None
    signature_header: str = ""
    status: str = "PENDING"  # DELIVERED, RETRY_QUEUED, DLQ
    error_message: Optional[str] = None
    next_retry_at: Optional[float] = None
    target_url: Optional[str] = None
    tenant_id: Optional[str] = None
    payload_body: Optional[str] = None


class OAuthToken(BaseModel):
    token: str = Field(default_factory=lambda: f"wtok_{uuid.uuid4().hex}")
    tenant_id: str
    scopes: List[str]
    expires_at: float = Field(default_factory=lambda: time.time() + 3600)


class Module7WebhookGateway:
    """
    HMAC SHA-256 signed webhook delivery, 5-stage exponential backoff retries,
    Dead-Letter Queue quarantine, and OAuth2 scope validation.
    """

    RETRY_WINDOWS_SEC = [30, 300, 1800, 7200, 43200]  # 30s, 5m, 30m, 2h, 12h
    DELIVERY_TIMEOUT_MS = 5000

    def __init__(self):
        self.registered_endpoints: Dict[str, WebhookEndpointRegistration] = {}
        self.delivery_logs: Dict[str, WebhookDeliveryLog] = {}
        self.dead_letter_queue: Dict[str, WebhookDeliveryLog] = {}
        self.oauth_tokens: Dict[str, OAuthToken] = {}
        self._retry_timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()
        self.redis = get_redis()
        self._start_retry_worker()

    def _start_retry_worker(self) -> None:
        def _tick():
            try:
                self.process_due_retries()
            finally:
                self._retry_timer = threading.Timer(5.0, _tick)
                self._retry_timer.daemon = True
                self._retry_timer.start()

        self._retry_timer = threading.Timer(5.0, _tick)
        self._retry_timer.daemon = True
        self._retry_timer.start()

    def register_endpoint(self, config: WebhookEndpointRegistration) -> WebhookEndpointRegistration:
        invalid = [s for s in config.scopes if s not in OAuth2Scope.ALL]
        if invalid:
            raise HTTPException(status_code=400, detail=f"Invalid scopes: {invalid}")
        self.registered_endpoints[config.endpoint_id] = config
        try:
            self.redis.set(
                f"wolf:wh:endpoint:{config.endpoint_id}",
                config.model_dump_json(),
                ex=30 * 86400,
            )
        except Exception:
            pass
        return config

    def issue_oauth_token(self, tenant_id: str, scopes: List[str]) -> OAuthToken:
        invalid = [s for s in scopes if s not in OAuth2Scope.ALL]
        if invalid:
            raise HTTPException(status_code=400, detail=f"Invalid scopes: {invalid}")
        token = OAuthToken(tenant_id=tenant_id, scopes=scopes)
        self.oauth_tokens[token.token] = token
        return token

    def validate_oauth_token(
        self, authorization: Optional[str], required_scope: str
    ) -> OAuthToken:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="Missing Bearer token")
        raw = authorization.split(" ", 1)[1].strip()
        token = self.oauth_tokens.get(raw)
        if not token or token.expires_at < time.time():
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        if (
            required_scope not in token.scopes
            and OAuth2Scope.SYSTEM_ADMIN not in token.scopes
        ):
            raise HTTPException(status_code=403, detail=f"Missing scope: {required_scope}")
        return token

    @staticmethod
    def generate_hmac_signature(secret_key: str, payload_bytes: bytes) -> str:
        signature = hmac.new(
            secret_key.encode("utf-8"),
            payload_bytes,
            hashlib.sha256,
        ).hexdigest()
        return f"sha256={signature}"

    def dispatch_event(
        self,
        payload: WebhookPayload,
        required_scope: Optional[str] = None,
    ) -> List[WebhookDeliveryLog]:
        required_scope = required_scope or EVENT_REQUIRED_SCOPE.get(
            payload.event_type, OAuth2Scope.LEADS_READ
        )
        logs: List[WebhookDeliveryLog] = []
        payload_bytes = json.dumps(payload.model_dump(), sort_keys=True).encode("utf-8")

        for endpoint_id, endpoint in list(self.registered_endpoints.items()):
            if not endpoint.is_active or endpoint.tenant_id != payload.tenant_id:
                continue
            if payload.event_type not in endpoint.subscribed_events:
                continue
            if (
                required_scope not in endpoint.scopes
                and OAuth2Scope.SYSTEM_ADMIN not in endpoint.scopes
            ):
                continue

            signature = self.generate_hmac_signature(endpoint.secret_key, payload_bytes)
            log = WebhookDeliveryLog(
                payload_id=payload.event_id,
                endpoint_id=endpoint_id,
                signature_header=signature,
                target_url=endpoint.target_url,
                tenant_id=endpoint.tenant_id,
                payload_body=payload_bytes.decode("utf-8"),
            )
            # Human-like micro-jitter between endpoint deliveries
            from services.shared.humanization import humanization

            humanization.apply_delay(humanization.webhook_jitter_sec(), fast_cap=0.15)
            self._attempt_delivery(log, endpoint)
            self.delivery_logs[log.delivery_id] = log
            logs.append(log)

        return logs

    def _attempt_delivery(
        self, log: WebhookDeliveryLog, endpoint: Optional[WebhookEndpointRegistration] = None
    ) -> None:
        log.attempt_count += 1
        target_url = log.target_url or (endpoint.target_url if endpoint else None)
        if not target_url:
            log.status = "DLQ"
            log.error_message = "Missing target URL"
            self.dead_letter_queue[log.delivery_id] = log
            return

        secret = endpoint.secret_key if endpoint else ""
        body = (log.payload_body or "{}").encode("utf-8")
        if not log.signature_header and secret:
            log.signature_header = self.generate_hmac_signature(secret, body)

        # Demo / example.com hosts stay simulated unless M7_FORCE_HTTP=true
        force_http = os.getenv("M7_FORCE_HTTP", "false").lower() in {"1", "true", "yes"}
        simulate = (not force_http) and (
            "example.com" in target_url
            or os.getenv("M7_SIMULATE", "false").lower() in {"1", "true", "yes"}
        )

        if simulate:
            # Deterministic-ish success for demos; allow force_fail via URL query
            if "force_fail" in target_url:
                log.http_status_code = 502
                log.error_message = "HTTP 502 - Simulated target failure"
                self._schedule_retry_or_dlq(log)
            else:
                log.http_status_code = 200
                log.status = "DELIVERED"
                log.next_retry_at = None
            return

        try:
            timeout_s = self.DELIVERY_TIMEOUT_MS / 1000.0
            with httpx.Client(timeout=timeout_s) as client:
                response = client.post(
                    target_url,
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Signature-SHA256": log.signature_header,
                        "X-Wolf-Delivery-Id": log.delivery_id,
                        "X-Wolf-Event-Id": log.payload_id,
                    },
                )
            log.http_status_code = response.status_code
            if 200 <= response.status_code < 300:
                log.status = "DELIVERED"
                log.error_message = None
                log.next_retry_at = None
            elif response.status_code >= 500:
                log.error_message = (
                    f"HTTP {response.status_code} - Target server error or timeout "
                    f"(>{self.DELIVERY_TIMEOUT_MS}ms)"
                )
                self._schedule_retry_or_dlq(log)
            else:
                # Non-retryable 4xx → DLQ
                log.status = "DLQ"
                log.error_message = f"HTTP {response.status_code} - non-retryable"
                self.dead_letter_queue[log.delivery_id] = log
        except Exception as exc:
            log.http_status_code = None
            log.error_message = f"Delivery error/timeout: {exc}"
            self._schedule_retry_or_dlq(log)

    def _schedule_retry_or_dlq(self, log: WebhookDeliveryLog) -> None:
        from services.shared.humanization import humanization

        idx = log.attempt_count - 1
        if idx < len(self.RETRY_WINDOWS_SEC):
            delay = self.RETRY_WINDOWS_SEC[idx]
            # Spec: exponential window + random human jitter
            when = humanization.retry_jitter_sec(float(delay))
            log.status = "RETRY_QUEUED"
            log.next_retry_at = time.time() + when
        else:
            log.status = "DLQ"
            log.next_retry_at = None
            self.dead_letter_queue[log.delivery_id] = log

    def process_due_retries(self) -> int:
        now = time.time()
        processed = 0
        with self._lock:
            due = [
                log
                for log in self.delivery_logs.values()
                if log.status == "RETRY_QUEUED"
                and log.next_retry_at is not None
                and log.next_retry_at <= now
            ]
        for log in due:
            endpoint = self.registered_endpoints.get(log.endpoint_id)
            self._attempt_delivery(log, endpoint)
            processed += 1
        return processed


# FastAPI Router Engine for Module 7
app = FastAPI(title="Module 7 External Webhook Gateway API")
gateway = Module7WebhookGateway()


def require_scope(scope: str):
    def _dep(authorization: Optional[str] = Header(default=None)) -> OAuthToken:
        # Allow unauthenticated local demos when M7_AUTH_REQUIRED=false
        if os.getenv("M7_AUTH_REQUIRED", "false").lower() not in {"1", "true", "yes"}:
            return OAuthToken(
                token="dev",
                tenant_id="*",
                scopes=list(OAuth2Scope.ALL),
                expires_at=time.time() + 86400,
            )
        return gateway.validate_oauth_token(authorization, scope)

    return _dep


@app.post("/api/v1/webhooks/endpoints")
def register_webhook_endpoint(
    registration: WebhookEndpointRegistration,
    _: OAuthToken = Depends(require_scope(OAuth2Scope.WEBHOOKS_MANAGE)),
):
    return gateway.register_endpoint(registration)


@app.post("/api/v1/webhooks/oauth/token")
def issue_token(tenant_id: str, scopes: List[str]):
    return gateway.issue_oauth_token(tenant_id, scopes)


@app.post("/api/v1/webhooks/dispatch")
def dispatch_webhook_event(
    payload: WebhookPayload,
    scope: Optional[str] = None,
    _: OAuthToken = Depends(require_scope(OAuth2Scope.LEADS_WRITE)),
):
    return gateway.dispatch_event(payload, required_scope=scope)


@app.post("/api/v1/webhooks/retries/process")
def process_retries():
    return {"processed": gateway.process_due_retries()}


@app.get("/api/v1/webhooks/dlq")
def get_webhook_dead_letter_queue(
    _: OAuthToken = Depends(require_scope(OAuth2Scope.WEBHOOKS_MANAGE)),
):
    return list(gateway.dead_letter_queue.values())


@app.get("/api/v1/webhooks/deliveries")
def list_deliveries(
    _: OAuthToken = Depends(require_scope(OAuth2Scope.WEBHOOKS_MANAGE)),
):
    return list(gateway.delivery_logs.values())
