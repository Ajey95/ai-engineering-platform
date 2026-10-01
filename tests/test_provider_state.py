import os

import pytest

from platform_app.provider_state import ProviderStateError, decrypt_state, encrypt_state
from platform_app.providers import ProviderTurn


def test_provider_continuation_is_encrypted_and_bound_to_run():
    key = os.urandom(32)
    turn = ProviderTurn(
        provider="anthropic", model="test", text="", calls=(), stop_reason="tool_use",
        usage={}, opaque_state={"content": [{"type": "thinking", "signature": "secret"}]},
    )
    blob = encrypt_state(turn, key, "tenant-a", "run-a")
    assert "secret" not in blob
    assert decrypt_state(blob, key, "tenant-a", "run-a", "anthropic") == turn.opaque_state
    with pytest.raises(ProviderStateError):
        decrypt_state(blob, key, "tenant-b", "run-a", "anthropic")
    with pytest.raises(ProviderStateError):
        decrypt_state(blob, key, "tenant-a", "run-a", "openai")
