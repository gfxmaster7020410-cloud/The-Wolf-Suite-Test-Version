"""
SCIM 2.0 Identity Lifecycle Management (RFC 7643 / 7644).
Persists to SQLite by default; optional Postgres when DATABASE_URL is set.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, Response, status
from pydantic import BaseModel, Field


class SCIMName(BaseModel):
    givenName: str = ""
    familyName: str = ""


class SCIMEmail(BaseModel):
    value: str
    type: str = "work"
    primary: bool = True


class SCIMUserResource(BaseModel):
    schemas: List[str] = ["urn:ietf:params:scim:schemas:core:2.0:User"]
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    externalId: str
    userName: str
    name: Optional[SCIMName] = None
    emails: List[SCIMEmail]
    active: bool = True
    role_mapping: List[str] = Field(default_factory=lambda: ["CampaignManager"])
    tenant_id: str = "default"
    meta: Dict[str, Any] = Field(
        default_factory=lambda: {
            "resourceType": "User",
            "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "lastModified": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    )


class SCIMUserPatchOperation(BaseModel):
    op: str  # replace, add, remove
    path: Optional[str] = None
    value: Any = None


class SCIMUserPatchRequest(BaseModel):
    schemas: List[str] = ["urn:ietf:params:scim:api:messages:2.0:PatchOp"]
    Operations: List[SCIMUserPatchOperation]


DEFAULT_SCIM_DB = Path(__file__).resolve().parents[2] / "data" / "scim_users.db"


class SCIMStore:
    """SQLite/Postgres-backed SCIM user persistence."""

    def __init__(self, db_path: Optional[Path] = None):
        self.database_url = os.getenv("DATABASE_URL", "")
        env_db = (os.getenv("SCIM_DB_PATH") or "").strip()
        self.db_path = Path(db_path) if db_path else Path(env_db or DEFAULT_SCIM_DB)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._use_postgres = self.database_url.startswith("postgres")
        self._init_schema()

    def _connect_sqlite(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        if self._use_postgres:
            try:
                import psycopg2

                conn = psycopg2.connect(self.database_url)
                cur = conn.cursor()
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS scim_users (
                        id UUID PRIMARY KEY,
                        tenant_id VARCHAR(64) NOT NULL,
                        external_id VARCHAR(255) UNIQUE NOT NULL,
                        user_name VARCHAR(255) NOT NULL,
                        email VARCHAR(255) NOT NULL,
                        is_active BOOLEAN DEFAULT TRUE,
                        role_mapping JSONB NOT NULL DEFAULT '[]',
                        payload JSONB NOT NULL DEFAULT '{}',
                        created_at TIMESTAMPTZ DEFAULT NOW(),
                        updated_at TIMESTAMPTZ DEFAULT NOW()
                    )
                    """
                )
                conn.commit()
                cur.close()
                conn.close()
                return
            except Exception as exc:
                print(f"[SCIM] Postgres init failed, using SQLite: {exc}")
                self._use_postgres = False

        conn = self._connect_sqlite()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS scim_users (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    external_id TEXT UNIQUE NOT NULL,
                    user_name TEXT NOT NULL,
                    email TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    role_mapping TEXT NOT NULL DEFAULT '[]',
                    payload TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_scim_tenant_ext ON scim_users(tenant_id, external_id)"
            )
            conn.commit()
        finally:
            conn.close()

    def upsert(self, user: SCIMUserResource) -> SCIMUserResource:
        email = user.emails[0].value if user.emails else ""
        now = time.time()
        payload = user.model_dump()
        if self._use_postgres:
            import psycopg2
            from psycopg2.extras import Json

            conn = psycopg2.connect(self.database_url)
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO scim_users
                    (id, tenant_id, external_id, user_name, email, is_active, role_mapping, payload)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (external_id) DO UPDATE SET
                    user_name=EXCLUDED.user_name,
                    email=EXCLUDED.email,
                    is_active=EXCLUDED.is_active,
                    role_mapping=EXCLUDED.role_mapping,
                    payload=EXCLUDED.payload,
                    updated_at=NOW()
                RETURNING id::text
                """,
                (
                    user.id,
                    user.tenant_id,
                    user.externalId,
                    user.userName,
                    email,
                    user.active,
                    Json(user.role_mapping),
                    Json(payload),
                ),
            )
            conn.commit()
            cur.close()
            conn.close()
            return user

        conn = self._connect_sqlite()
        try:
            conn.execute(
                """
                INSERT INTO scim_users
                    (id, tenant_id, external_id, user_name, email, is_active,
                     role_mapping, payload, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(external_id) DO UPDATE SET
                    user_name=excluded.user_name,
                    email=excluded.email,
                    is_active=excluded.is_active,
                    role_mapping=excluded.role_mapping,
                    payload=excluded.payload,
                    updated_at=excluded.updated_at
                """,
                (
                    user.id,
                    user.tenant_id,
                    user.externalId,
                    user.userName,
                    email,
                    1 if user.active else 0,
                    json.dumps(user.role_mapping),
                    json.dumps(payload),
                    now,
                    now,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return user

    def get(self, user_id: str) -> Optional[SCIMUserResource]:
        if self._use_postgres:
            import psycopg2

            conn = psycopg2.connect(self.database_url)
            cur = conn.cursor()
            cur.execute("SELECT payload FROM scim_users WHERE id=%s", (user_id,))
            row = cur.fetchone()
            cur.close()
            conn.close()
            if not row:
                return None
            return SCIMUserResource.model_validate(row[0] if isinstance(row[0], dict) else json.loads(row[0]))

        conn = self._connect_sqlite()
        try:
            row = conn.execute("SELECT payload FROM scim_users WHERE id=?", (user_id,)).fetchone()
            if not row:
                return None
            return SCIMUserResource.model_validate(json.loads(row["payload"]))
        finally:
            conn.close()

    def find_by_external_id(self, external_id: str) -> Optional[SCIMUserResource]:
        if self._use_postgres:
            import psycopg2

            conn = psycopg2.connect(self.database_url)
            cur = conn.cursor()
            cur.execute("SELECT payload FROM scim_users WHERE external_id=%s", (external_id,))
            row = cur.fetchone()
            cur.close()
            conn.close()
            if not row:
                return None
            return SCIMUserResource.model_validate(row[0] if isinstance(row[0], dict) else json.loads(row[0]))

        conn = self._connect_sqlite()
        try:
            row = conn.execute(
                "SELECT payload FROM scim_users WHERE external_id=?", (external_id,)
            ).fetchone()
            if not row:
                return None
            return SCIMUserResource.model_validate(json.loads(row["payload"]))
        finally:
            conn.close()

    def list_users(
        self,
        start_index: int = 1,
        count: int = 100,
        filter_expr: Optional[str] = None,
    ) -> List[SCIMUserResource]:
        users: List[SCIMUserResource] = []
        if self._use_postgres:
            import psycopg2

            conn = psycopg2.connect(self.database_url)
            cur = conn.cursor()
            cur.execute("SELECT payload FROM scim_users ORDER BY created_at")
            rows = cur.fetchall()
            cur.close()
            conn.close()
            for row in rows:
                users.append(
                    SCIMUserResource.model_validate(
                        row[0] if isinstance(row[0], dict) else json.loads(row[0])
                    )
                )
        else:
            conn = self._connect_sqlite()
            try:
                rows = conn.execute(
                    "SELECT payload FROM scim_users ORDER BY created_at"
                ).fetchall()
                for row in rows:
                    users.append(SCIMUserResource.model_validate(json.loads(row["payload"])))
            finally:
                conn.close()

        if filter_expr:
            users = [u for u in users if self._matches_filter(u, filter_expr)]

        start = max(0, start_index - 1)
        return users[start : start + count]

    @staticmethod
    def _matches_filter(user: SCIMUserResource, filter_expr: str) -> bool:
        # Minimal filter: userName eq "x" / externalId eq "y" / emails.value eq "z"
        expr = filter_expr.strip()
        lower = expr.lower()
        if 'username eq "' in lower:
            value = expr.split('"', 2)[1]
            return user.userName == value
        if 'externalid eq "' in lower:
            value = expr.split('"', 2)[1]
            return user.externalId == value
        if "emails.value eq" in lower or 'email eq "' in lower:
            value = expr.split('"', 2)[1]
            return any(e.value == value for e in user.emails)
        if "active eq false" in lower:
            return user.active is False
        if "active eq true" in lower:
            return user.active is True
        return True


class SCIM2Controller:
    """
    SCIM 2.0 controller: paginated list/filter, idempotent create,
    PATCH (active lock), soft-delete.
    """

    def __init__(self, store: Optional[SCIMStore] = None):
        self.store = store or SCIMStore()
        # Keep an in-memory mirror for fast get during process lifetime
        self.user_store: Dict[str, SCIMUserResource] = {}

    def list_users(
        self,
        start_index: int = 1,
        count: int = 100,
        filter_expr: Optional[str] = None,
    ) -> Dict[str, Any]:
        users = self.store.list_users(start_index=start_index, count=count, filter_expr=filter_expr)
        for u in users:
            self.user_store[u.id] = u
        return {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
            "totalResults": len(users) if filter_expr else len(self.store.list_users(1, 10_000)),
            "startIndex": start_index,
            "itemsPerPage": len(users),
            "Resources": [u.model_dump() for u in users],
        }

    def create_user(self, user: SCIMUserResource) -> SCIMUserResource:
        existing = self.store.find_by_external_id(user.externalId)
        if existing:
            self.user_store[existing.id] = existing
            return existing
        user.meta["created"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        user.meta["lastModified"] = user.meta["created"]
        saved = self.store.upsert(user)
        self.user_store[saved.id] = saved
        return saved

    def get_user(self, user_id: str) -> SCIMUserResource:
        user = self.user_store.get(user_id) or self.store.get(user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        self.user_store[user.id] = user
        return user

    def patch_user(self, user_id: str, patch_req: SCIMUserPatchRequest) -> SCIMUserResource:
        user = self.get_user(user_id)
        for op in patch_req.Operations:
            op_name = op.op.lower()
            path = (op.path or "").lower()
            if op_name == "replace":
                if path in {"active", "urn:ietf:params:scim:schemas:core:2.0:user:active"} or (
                    isinstance(op.value, dict) and "active" in op.value
                ):
                    new_val = op.value if isinstance(op.value, bool) else op.value.get("active")
                    user.active = bool(new_val)
                elif path == "username" and isinstance(op.value, str):
                    user.userName = op.value
                elif path.startswith("emails") and isinstance(op.value, list):
                    user.emails = [SCIMEmail.model_validate(v) for v in op.value]
                elif path in {"role_mapping", "roles"} and isinstance(op.value, list):
                    user.role_mapping = [str(v) for v in op.value]
                elif isinstance(op.value, dict):
                    if "active" in op.value:
                        user.active = bool(op.value["active"])
                    if "userName" in op.value:
                        user.userName = op.value["userName"]
            elif op_name == "add" and path in {"role_mapping", "roles"}:
                values = op.value if isinstance(op.value, list) else [op.value]
                for v in values:
                    if v and str(v) not in user.role_mapping:
                        user.role_mapping.append(str(v))
            elif op_name == "remove" and path == "active":
                user.active = False

        user.meta["lastModified"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.store.upsert(user)
        self.user_store[user.id] = user
        return user

    def delete_user(self, user_id: str) -> None:
        user = self.get_user(user_id)
        user.active = False  # Soft-deletion → compliance retention
        user.meta["lastModified"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.store.upsert(user)
        self.user_store[user.id] = user


scim_controller = SCIM2Controller()
scim_app = FastAPI(title="SCIM 2.0 Identity Lifecycle Management API")


@scim_app.get("/scim/v2/Users")
def scim_list_users(
    startIndex: int = Query(1, ge=1),
    count: int = Query(100, ge=1, le=1000),
    filter: Optional[str] = Query(None),
):
    return scim_controller.list_users(start_index=startIndex, count=count, filter_expr=filter)


@scim_app.post("/scim/v2/Users", status_code=status.HTTP_201_CREATED)
def scim_create_user(user: SCIMUserResource):
    return scim_controller.create_user(user)


@scim_app.get("/scim/v2/Users/{user_id}")
def scim_get_user(user_id: str):
    return scim_controller.get_user(user_id)


@scim_app.patch("/scim/v2/Users/{user_id}")
def scim_patch_user(user_id: str, patch_req: SCIMUserPatchRequest):
    return scim_controller.patch_user(user_id, patch_req)


@scim_app.delete("/scim/v2/Users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def scim_delete_user(user_id: str):
    scim_controller.delete_user(user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
