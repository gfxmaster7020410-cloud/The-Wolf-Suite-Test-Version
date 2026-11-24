"""
Immutable append-only audit trail with HMAC-SHA256 hash chaining (Merkle-style).
Exportable to SIEM (Splunk HEC / Datadog) over TLS.
"""

import hashlib
import hmac
import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class AuditRecord(BaseModel):
    event_id: str = Field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:10]}")
    timestamp: str = ""
    tenant_id: str
    actor: str
    action: str
    module: str
    payload_hash: str
    prev_block_hash: str
    block_hash: str = ""

    def model_post_init(self, __context: Any) -> None:
        if not self.timestamp:
            self.timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class MerkleAuditLogger:
    """
    Cryptographically signed audit records with hash chaining.
    Each block embeds prev_block_hash to detect tampering.
    """

    GENESIS_HASH = "0" * 64

    def __init__(self, hmac_secret: Optional[str] = None):
        self.hmac_secret = (
            hmac_secret
            or os.getenv("WEBHOOK_HMAC_SECRET")
            or os.getenv("JWT_SECRET")
            or "wolf-audit-dev-secret"
        ).encode("utf-8")
        self.chain: List[AuditRecord] = []
        self._last_hash = self.GENESIS_HASH

    def _hash_payload(self, payload: Dict[str, Any]) -> str:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _compute_block_hash(self, record: AuditRecord) -> str:
        material = "|".join([
            record.event_id,
            record.timestamp,
            record.tenant_id,
            record.actor,
            record.action,
            record.module,
            record.payload_hash,
            record.prev_block_hash,
        ])
        return hmac.new(self.hmac_secret, material.encode("utf-8"), hashlib.sha256).hexdigest()

    def append(
        self,
        tenant_id: str,
        actor: str,
        action: str,
        module: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> AuditRecord:
        payload = payload or {}
        record = AuditRecord(
            tenant_id=tenant_id,
            actor=actor,
            action=action,
            module=module,
            payload_hash=self._hash_payload(payload),
            prev_block_hash=self._last_hash,
        )
        record.block_hash = self._compute_block_hash(record)
        self.chain.append(record)
        self._last_hash = record.block_hash
        try:
            from services.shared.sqlite_store import store
            store.append_audit(record.model_dump())
        except Exception:
            pass  # Persistence is best-effort; in-memory chain remains authoritative
        return record

    def verify_chain(self) -> bool:
        prev = self.GENESIS_HASH
        for record in self.chain:
            if record.prev_block_hash != prev:
                return False
            if self._compute_block_hash(record) != record.block_hash:
                return False
            prev = record.block_hash
        return True

    def export_siem_batch(self) -> List[Dict[str, Any]]:
        """Serialize chain for Splunk HEC / Datadog Logs API export."""
        return [r.model_dump() for r in self.chain]
