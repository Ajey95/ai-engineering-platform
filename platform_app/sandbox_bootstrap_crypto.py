"""AEAD envelope for EC2 user data containing short-lived guest capabilities."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class SandboxBootstrapError(ValueError):
    pass


def _aad(tenant_id: str, run_id: str, lease_id: str, generation: int) -> bytes:
    if not tenant_id or not run_id or not lease_id or generation < 1:
        raise SandboxBootstrapError("Bootstrap scope is invalid")
    return json.dumps(
        [tenant_id, run_id, lease_id, generation], separators=(",", ":")
    ).encode()


def encrypt_user_data(
    user_data: str, key: bytes, tenant_id: str, run_id: str,
    lease_id: str, generation: int,
) -> tuple[str, str]:
    if len(key) != 32:
        raise SandboxBootstrapError("Bootstrap encryption key must be 256 bits")
    raw = user_data.encode("utf-8")
    if not raw.startswith(b"#!/bin/bash\n") or not 1 <= len(raw) <= 16_384:
        raise SandboxBootstrapError("EC2 user data is invalid or exceeds 16 KiB")
    nonce = os.urandom(12)
    cipher = AESGCM(key).encrypt(nonce, raw, _aad(tenant_id, run_id, lease_id, generation))
    envelope = "v1." + base64.urlsafe_b64encode(nonce + cipher).decode()
    return envelope, hashlib.sha256(raw).hexdigest()


def decrypt_user_data(
    envelope: str, expected_sha256: str, key: bytes,
    tenant_id: str, run_id: str, lease_id: str, generation: int,
) -> str:
    if len(key) != 32 or not envelope.startswith("v1."):
        raise SandboxBootstrapError("Bootstrap envelope is invalid")
    try:
        raw = base64.urlsafe_b64decode(envelope[3:])
        plaintext = AESGCM(key).decrypt(
            raw[:12], raw[12:], _aad(tenant_id, run_id, lease_id, generation)
        )
        decoded = plaintext.decode("utf-8")
    except (InvalidTag, UnicodeDecodeError, ValueError, binascii.Error) as error:
        raise SandboxBootstrapError("Bootstrap envelope cannot be authenticated") from error
    if hashlib.sha256(plaintext).hexdigest() != expected_sha256:
        raise SandboxBootstrapError("Bootstrap digest is inconsistent")
    return decoded
