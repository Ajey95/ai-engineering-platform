"""Operator-run live conformance check for a registered model entry.

Credentials come only from the local environment or the host's secret manager.
This command never prints a key or saves the provider transcript.
"""

from __future__ import annotations

import argparse
import json
import os

import httpx

from platform_app.db import SessionLocal
from platform_app.model_qualification import (
    QualificationError,
    load_attestation,
    qualify_model_entry,
)
from platform_app.models import ModelEntry
from platform_app.providers import AnthropicMessages, GeminiGenerateContent, OpenAIResponses

ADAPTERS = {
    "openai": ("OPENAI_API_KEY", OpenAIResponses),
    "anthropic": ("ANTHROPIC_API_KEY", AnthropicMessages),
    "google": ("GOOGLE_API_KEY", GeminiGenerateContent),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-entry-id", required=True)
    parser.add_argument("--attestation", required=True, type=str)
    parser.add_argument("--operator", required=True)
    parser.add_argument("--enable", action="store_true")
    args = parser.parse_args()
    try:
        from pathlib import Path

        attestation = load_attestation(Path(args.attestation))
        with SessionLocal() as db:
            model = db.get(ModelEntry, args.model_entry_id)
            if model is None:
                raise QualificationError("NOT_FOUND", "Model entry not found")
            provider = model.provider
        if provider not in ADAPTERS:
            raise QualificationError("MODEL_UNAVAILABLE", "Provider is not supported")
        key_name, adapter_type = ADAPTERS[provider]
        key = os.environ.get(key_name)
        if not key:
            raise QualificationError("MODEL_CREDENTIAL_MISSING", f"{key_name} is unavailable")
        with httpx.Client(follow_redirects=False, trust_env=False, timeout=120) as client:
            adapter = adapter_type(key, client)
            result = qualify_model_entry(
                SessionLocal,
                args.model_entry_id,
                adapter,
                attestation,
                args.operator,
                enable=args.enable,
            )
        print(json.dumps(result, sort_keys=True))
        return 0
    except QualificationError as error:
        print(json.dumps({"status": "failed", "code": error.code, "message": str(error)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
