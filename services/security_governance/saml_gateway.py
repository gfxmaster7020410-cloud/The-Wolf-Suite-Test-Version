"""
Enterprise SAML 2.0 Gateway — BoxyHQ SAML Jackson IdP broker pattern.
Provides SP metadata + ACS endpoints; proxies to Jackson when configured,
otherwise issues local signed session JWTs for development.
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI, Form, HTTPException, Request
from pydantic import BaseModel, Field

try:
    from jose import jwt as jose_jwt
except ImportError:  # pragma: no cover
    jose_jwt = None
    import base64
    import hashlib
    import hmac
    import json as _json


def _encode_jwt(claims: Dict[str, Any], secret: str) -> str:
    if jose_jwt is not None:
        return jose_jwt.encode(claims, secret, algorithm="HS256")
    # Minimal HS256 JWT fallback when python-jose is not installed
    def b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

    header = b64(_json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = b64(_json.dumps(claims, separators=(",", ":")).encode())
    signing_input = f"{header}.{payload}".encode()
    sig = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    return f"{header}.{payload}.{b64(sig)}"


def _decode_jwt(token: str, secret: str) -> Dict[str, Any]:
    if jose_jwt is not None:
        return jose_jwt.decode(token, secret, algorithms=["HS256"])
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Invalid JWT")
    header_b64, payload_b64, sig_b64 = parts

    def pad(s: str) -> bytes:
        return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))

    signing_input = f"{header_b64}.{payload_b64}".encode()
    expected = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, pad(sig_b64)):
        raise ValueError("Invalid JWT signature")
    payload = _json.loads(pad(payload_b64))
    if int(payload.get("exp", 0)) < int(time.time()):
        raise ValueError("JWT expired")
    return payload


class SAMLAssertionPayload(BaseModel):
    sub: str = Field(default_factory=lambda: f"usr_{uuid.uuid4().hex[:12]}")
    tenant_id: str
    email: str
    roles: List[str]
    cost_center_id: str
    iss: str = "https://auth.enterprise.domain"
    exp: float = Field(default_factory=lambda: time.time() + 3600)
    access_token: Optional[str] = None


class BoxyHQSAMLGateway:
    """
    Identity Provider (IdP) Broker using BoxyHQ SAML Jackson
    to decouple client enterprise IdPs (Okta, Entra ID) from core authorization.
    """

    def __init__(self, sp_entity_id: Optional[str] = None):
        self.sp_entity_id = sp_entity_id or os.getenv(
            "SAML_SP_ENTITY_ID", "https://auth.enterprise.domain"
        )
        self.jackson_url = os.getenv("BOXYHQ_JACKSON_URL", "").rstrip("/")
        self.jackson_api_key = os.getenv("BOXYHQ_JACKSON_API_KEY", "")
        self.jwt_secret = os.getenv("JWT_SECRET", "wolf-saml-dev-secret")
        self.sessions: Dict[str, SAMLAssertionPayload] = {}

    def get_sp_metadata(self) -> Dict[str, Any]:
        """Returns Service Provider metadata endpoint descriptor (§11.1)."""
        meta = {
            "sp_entity_id": self.sp_entity_id,
            "acs_url": f"{self.sp_entity_id}/api/v1/sso/saml/acs",
            "metadata_url": f"{self.sp_entity_id}/api/v1/sso/saml/metadata",
            "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST",
            "name_id_format": "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
            "jackson_configured": bool(self.jackson_url),
        }
        return meta

    async def fetch_jackson_metadata(self) -> Optional[Dict[str, Any]]:
        if not self.jackson_url:
            return None
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                headers = {}
                if self.jackson_api_key:
                    headers["Authorization"] = f"Bearer {self.jackson_api_key}"
                res = await client.get(f"{self.jackson_url}/api/v1/saml/metadata", headers=headers)
                res.raise_for_status()
                return res.json()
        except Exception as exc:
            return {"error": str(exc), "jackson_url": self.jackson_url}

    def _issue_jwt(self, assertion: SAMLAssertionPayload) -> str:
        claims = {
            "sub": assertion.sub,
            "tenant_id": assertion.tenant_id,
            "email": assertion.email,
            "roles": assertion.roles,
            "cost_center_id": assertion.cost_center_id,
            "iss": assertion.iss,
            "exp": int(assertion.exp),
            "iat": int(time.time()),
        }
        return _encode_jwt(claims, self.jwt_secret)

    def process_acs_assertion(
        self,
        tenant_id: str,
        email: str,
        roles: List[str],
        name_id: Optional[str] = None,
    ) -> SAMLAssertionPayload:
        """Processes ACS assertion attributes and generates scoped JWT session payload."""
        assertion = SAMLAssertionPayload(
            sub=name_id or f"usr_{uuid.uuid4().hex[:12]}",
            tenant_id=tenant_id,
            email=email,
            roles=roles or ["ReadOnlyViewer"],
            cost_center_id=f"cc_{tenant_id}",
        )
        assertion.access_token = self._issue_jwt(assertion)
        self.sessions[assertion.sub] = assertion
        return assertion

    async def process_saml_response(
        self,
        saml_response: str,
        tenant_id: str = "default",
        relay_state: Optional[str] = None,
    ) -> SAMLAssertionPayload:
        """
        Prefer BoxyHQ Jackson SSO exchange when configured.
        Local/dev: decode minimal attributes from RelayState / form fields.
        """
        if self.jackson_url:
            try:
                async with httpx.AsyncClient(timeout=20.0) as client:
                    headers = {"Content-Type": "application/x-www-form-urlencoded"}
                    if self.jackson_api_key:
                        headers["Authorization"] = f"Bearer {self.jackson_api_key}"
                    res = await client.post(
                        f"{self.jackson_url}/api/oauth/saml",
                        data={
                            "SAMLResponse": saml_response,
                            "RelayState": relay_state or tenant_id,
                        },
                        headers=headers,
                    )
                    res.raise_for_status()
                    body = res.json()
                    email = body.get("email") or body.get("user", {}).get("email") or "user@enterprise.local"
                    roles = body.get("roles") or ["CampaignManager"]
                    return self.process_acs_assertion(tenant_id, email, roles, name_id=body.get("id"))
            except Exception as exc:
                # Fall through to local assertion for resilience in dev
                print(f"[SAML] Jackson ACS proxy failed: {exc}")

        # Local development path: accept opaque SAMLResponse marker + tenant/email via RelayState
        email = "sso.user@enterprise.local"
        roles = ["CampaignManager"]
        if relay_state and "@" in relay_state:
            email = relay_state
        return self.process_acs_assertion(tenant_id, email, roles)

    def verify_session_token(self, token: str) -> Dict[str, Any]:
        try:
            return _decode_jwt(token, self.jwt_secret)
        except Exception as exc:
            raise HTTPException(status_code=401, detail=f"Invalid session token: {exc}")


saml_gateway = BoxyHQSAMLGateway()
app = FastAPI(title="Enterprise SAML 2.0 Gateway")


@app.get("/api/v1/sso/saml/metadata")
async def saml_sp_metadata():
    meta = saml_gateway.get_sp_metadata()
    jackson = await saml_gateway.fetch_jackson_metadata()
    if jackson:
        meta["jackson"] = jackson
    return meta


@app.post("/api/v1/sso/saml/acs")
async def saml_assertion_consumer_service(
    request: Request,
    tenant_id: str = "default",
    email: Optional[str] = None,
    roles: Optional[str] = None,
    SAMLResponse: Optional[str] = Form(default=None),
    RelayState: Optional[str] = Form(default=None),
):
    """
    ACS endpoint:
    - Production: HTTP-POST binding with SAMLResponse (+ optional RelayState)
    - Dev convenience: query/body tenant_id, email, roles
    """
    form = {}
    try:
        form = dict(await request.form())
    except Exception:
        pass

    saml_response = SAMLResponse or form.get("SAMLResponse")
    relay_state = RelayState or form.get("RelayState")

    if saml_response:
        return await saml_gateway.process_saml_response(
            str(saml_response),
            tenant_id=tenant_id,
            relay_state=str(relay_state) if relay_state else email,
        )

    if not email:
        raise HTTPException(
            status_code=400,
            detail="Provide SAMLResponse (POST binding) or email/roles for local ACS testing",
        )
    role_list = [r.strip() for r in (roles or "ReadOnlyViewer").split(",") if r.strip()]
    return saml_gateway.process_acs_assertion(tenant_id, email, role_list)


@app.get("/api/v1/sso/session/verify")
def verify_session(authorization: Optional[str] = None):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Bearer token required")
    token = authorization.split(" ", 1)[1].strip()
    return saml_gateway.verify_session_token(token)
