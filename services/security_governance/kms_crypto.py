"""
Envelope encryption utilizing AES-256-GCM for tokens, cookies, and proxy keys.
Compatible with HashiCorp Vault / AWS KMS master-key patterns.
"""

import os
import base64
import hashlib
from typing import Optional

from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes


class KMSCryptoEngine:
    """
    Envelope encryption:
    1. Generate a random data encryption key (DEK)
    2. Encrypt plaintext with DEK using AES-256-GCM
    3. Wrap DEK with the master KMS key (simulated local wrap)
    """

    def __init__(self, master_key_hex: Optional[str] = None):
        raw = master_key_hex or os.getenv("KMS_MASTER_KEY", "")
        if not raw:
            # Deterministic fallback for local development only
            raw = hashlib.sha256(b"wolf-dev-kms-master").hexdigest()
        # Normalize to 32-byte key
        if len(raw) == 64:
            self.master_key = bytes.fromhex(raw)
        else:
            self.master_key = hashlib.sha256(raw.encode()).digest()

    def encrypt(self, plaintext: str) -> str:
        dek = get_random_bytes(32)
        nonce = get_random_bytes(12)
        cipher = AES.new(dek, AES.MODE_GCM, nonce=nonce)
        ciphertext, tag = cipher.encrypt_and_digest(plaintext.encode("utf-8"))

        wrapped_dek = self._wrap_dek(dek)
        blob = b"|".join([
            base64.b64encode(wrapped_dek),
            base64.b64encode(nonce),
            base64.b64encode(tag),
            base64.b64encode(ciphertext),
        ])
        return base64.b64encode(blob).decode("utf-8")

    def decrypt(self, envelope_b64: str) -> str:
        blob = base64.b64decode(envelope_b64.encode("utf-8"))
        wrapped_dek_b64, nonce_b64, tag_b64, ciphertext_b64 = blob.split(b"|")
        dek = self._unwrap_dek(base64.b64decode(wrapped_dek_b64))
        nonce = base64.b64decode(nonce_b64)
        tag = base64.b64decode(tag_b64)
        ciphertext = base64.b64decode(ciphertext_b64)

        cipher = AES.new(dek, AES.MODE_GCM, nonce=nonce)
        plaintext = cipher.decrypt_and_verify(ciphertext, tag)
        return plaintext.decode("utf-8")

    def _wrap_dek(self, dek: bytes) -> bytes:
        nonce = get_random_bytes(12)
        cipher = AES.new(self.master_key, AES.MODE_GCM, nonce=nonce)
        wrapped, tag = cipher.encrypt_and_digest(dek)
        return nonce + tag + wrapped

    def _unwrap_dek(self, wrapped: bytes) -> bytes:
        nonce, tag, ciphertext = wrapped[:12], wrapped[12:28], wrapped[28:]
        cipher = AES.new(self.master_key, AES.MODE_GCM, nonce=nonce)
        return cipher.decrypt_and_verify(ciphertext, tag)
