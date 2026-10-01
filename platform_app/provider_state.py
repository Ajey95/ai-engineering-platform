"""Encrypt provider continuation state before durable storage (FR-MOD-03)."""

from __future__ import annotations

import base64
import binascii
import json
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from platform_app.providers import ProviderTurn


class ProviderStateError(Exception):
    pass


def _associated_data(tenant_id: str, run_id: str, provider: str) -> bytes:
    if not tenant_id or not run_id or provider not in {"openai", "anthropic", "google"}:
        raise ValueError("Invalid provider state scope")
    return json.dumps([tenant_id, run_id, provider], separators=(",", ":")).encode()


def encrypt_state(turn: ProviderTurn, key: bytes, tenant_id: str, run_id: str) -> str:
    if len(key) != 32:
        raise ValueError("Provider state key must be 256 bits")
    plaintext = json.dumps(turn.opaque_state, separators=(",", ":")).encode()
    if len(plaintext) > 2_000_000:
        raise ProviderStateError("Provider state exceeds storage limit")
    nonce = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(
        nonce, plaintext, _associated_data(tenant_id, run_id, turn.provider)
    )
    return "v1." + base64.urlsafe_b64encode(nonce + ciphertext).decode()


def decrypt_state(
    blob: str, key: bytes, tenant_id: str, run_id: str, provider: str
) -> dict:
    if len(key) != 32 or not blob.startswith("v1."):
        raise ProviderStateError("Invalid provider state envelope")
    try:
        raw = base64.urlsafe_b64decode(blob[3:])
        plaintext = AESGCM(key).decrypt(
            raw[:12], raw[12:], _associated_data(tenant_id, run_id, provider)
        )
        state = json.loads(plaintext)
    except (InvalidTag, ValueError, TypeError, binascii.Error) as error:
        raise ProviderStateError("Provider state could not be authenticated") from error
    if not isinstance(state, dict):
        raise ProviderStateError("Provider state is malformed")
    return state
