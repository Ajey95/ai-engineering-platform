"""Live, account-specific native provider qualification.

The operator supplies independently reviewed limit and price metadata. The
probe verifies complete-JSON and streamed text, schema-checked tool calls,
continuation, usage and the resolved model on the configured provider account.
No credential or probe transcript is persisted.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from sqlalchemy import select

from platform_app.db import utcnow
from platform_app.models import ModelEntry, ModelRegistryEvent, OutboxEvent, Run, SandboxLease
from platform_app.providers import ProviderTurn
from platform_app.schemas import ModelRegister
from platform_app.tool_broker import ToolDefinition


class QualificationError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _registry_event(
    db, model: ModelEntry, actor: str, action: str, outcome: str, reason: str | None = None
) -> None:
    details = {
        "provider": model.provider,
        "model_id": model.model_id,
        "registry_revision": model.registry_revision,
        "context_limit": model.context_limit,
        "output_limit": model.output_limit,
        "price_revision": model.price_revision,
        "price_per_m_input": str(model.price_per_m_input),
        "price_per_m_output": str(model.price_per_m_output),
        "qualification": (model.capabilities or {}).get("qualification"),
    }
    digest = hashlib.sha256(
        json.dumps(details, sort_keys=True, default=str, separators=(",", ":")).encode()
    ).hexdigest()
    db.add(
        ModelRegistryEvent(
            model_entry_id=model.id,
            actor=actor,
            action=action,
            registry_revision=model.registry_revision,
            metadata_hash=digest,
            outcome=outcome,
            reason=reason,
        )
    )


def register_model_entry(db, body: ModelRegister, operator: str) -> ModelEntry:
    if not operator or len(operator) > 200:
        raise QualificationError("INVALID_OPERATOR", "Operator identity is required")
    if db.get(ModelEntry, body.id) is not None:
        raise QualificationError("MODEL_EXISTS", "Model entry already exists")
    model = ModelEntry(
        **{key: value for key, value in body.model_dump().items() if key != "capabilities"},
        capabilities={"declared": body.capabilities},
        state="registered",
    )
    db.add(model)
    _registry_event(db, model, operator, "register", "registered")
    return model


class QualificationAdapter(Protocol):
    provider: str

    def generate(
        self,
        model: str,
        instruction: str,
        prompt: str,
        tools: dict[str, ToolDefinition],
        max_output_tokens: int,
        previous: ProviderTurn | None = None,
        results: dict[str, dict] | None = None,
        stream: bool = False,
    ) -> ProviderTurn: ...


@dataclass(frozen=True)
class MetadataAttestation:
    registry_revision: str
    context_limit: int
    output_limit: int
    price_revision: str
    price_per_m_input: Decimal
    price_per_m_output: Decimal
    limits_source_url: str
    pricing_source_url: str
    effective_date: str

    @classmethod
    def from_dict(cls, value: dict) -> "MetadataAttestation":
        try:
            item = cls(
                registry_revision=str(value["registry_revision"]),
                context_limit=int(value["context_limit"]),
                output_limit=int(value["output_limit"]),
                price_revision=str(value["price_revision"]),
                price_per_m_input=Decimal(str(value["price_per_m_input"])),
                price_per_m_output=Decimal(str(value["price_per_m_output"])),
                limits_source_url=str(value["limits_source_url"]),
                pricing_source_url=str(value["pricing_source_url"]),
                effective_date=str(value["effective_date"]),
            )
            date.fromisoformat(item.effective_date)
        except (KeyError, ValueError, TypeError, ArithmeticError) as error:
            raise QualificationError(
                "INVALID_ATTESTATION", "Model metadata attestation is invalid"
            ) from error
        for source in (item.limits_source_url, item.pricing_source_url):
            parsed = urlsplit(source)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
            ):
                raise QualificationError(
                    "INVALID_ATTESTATION", "Metadata source must be an HTTPS URL"
                )
        if (
            item.context_limit < 1024
            or item.output_limit < 256
            or item.output_limit > item.context_limit
            or not item.price_revision
            or not item.registry_revision
            or not item.price_per_m_input.is_finite()
            or not item.price_per_m_output.is_finite()
            or item.price_per_m_input < 0
            or item.price_per_m_output < 0
        ):
            raise QualificationError("INVALID_ATTESTATION", "Model limits or pricing are invalid")
        return item


def adapter_digest() -> str:
    from platform_app import provider_streams, providers, tool_broker

    digest = hashlib.sha256()
    for module in (providers, provider_streams, tool_broker):
        source = Path(module.__file__)
        digest.update(source.name.encode("utf-8"))
        digest.update(source.read_bytes())
    return digest.hexdigest()


def _qualification_evidence_current(model: ModelEntry) -> bool:
    record = (model.capabilities or {}).get("qualification") or {}
    return bool(
        model.validated_at
        and (model.capabilities or {}).get("live_qualified") is True
        and record.get("registry_revision") == model.registry_revision
        and record.get("provider") == model.provider
        and record.get("requested_model") == model.model_id
        and record.get("context_limit") == model.context_limit
        and record.get("output_limit") == model.output_limit
        and record.get("price_revision") == model.price_revision
        and record.get("price_per_m_input") == str(model.price_per_m_input)
        and record.get("price_per_m_output") == str(model.price_per_m_output)
        and record.get("adapter_digest") == adapter_digest()
        and record.get("status") == "passed"
    )


def qualification_current(model: ModelEntry) -> bool:
    """A new run may be admitted only to an enabled, currently qualified model."""
    return model.state == "enabled" and _qualification_evidence_current(model)


def qualification_for_pinned_run(model: ModelEntry) -> bool:
    """Deprecation stops new admission while an already pinned run can finish."""
    return model.state in {"enabled", "deprecated"} and _qualification_evidence_current(model)


def change_model_state(
    session_factory, model_entry_id: str, action: str, operator: str, reason: str
) -> dict:
    if not operator.strip() or len(operator) > 200:
        raise QualificationError("INVALID_OPERATOR", "Operator identity is required")
    if not 8 <= len(reason.strip()) <= 2000:
        raise QualificationError("INVALID_REASON", "A lifecycle reason is required")
    if action not in {"enable", "deprecate", "disable"}:
        raise QualificationError("INVALID_ACTION", "Model lifecycle action is invalid")
    affected_runs = 0
    with session_factory() as db:
        model = db.scalar(select(ModelEntry).where(
            ModelEntry.id == model_entry_id
        ).with_for_update())
        if model is None:
            raise QualificationError("NOT_FOUND", "Model entry not found")
        before = model.state
        if action == "enable":
            if before not in {"qualified", "deprecated"} or not (
                _qualification_evidence_current(model)
            ):
                raise QualificationError(
                    "MODEL_QUALIFICATION_REQUIRED", "Current qualification is required"
                )
            model.state = "enabled"
        elif action == "deprecate":
            if before != "enabled":
                raise QualificationError("MODEL_STATE_INVALID", "Only enabled models deprecate")
            model.state = "deprecated"
        else:
            if before == "disabled":
                raise QualificationError("MODEL_STATE_INVALID", "Model is already disabled")
            model.state = "disabled"
            model.capabilities = {
                **(model.capabilities or {}),
                "live_qualified": False,
                "qualification": {
                    **((model.capabilities or {}).get("qualification") or {}),
                    "status": "disabled",
                },
            }
            # Revoke worker fencing and guest capabilities in the same commit
            # as the security disable. An in-flight provider request may still
            # bill; its old fence cannot turn this pause into a success.
            from platform_app.service import append_event

            open_states = {
                "QUEUED", "PREPARING", "REPRODUCING", "INVESTIGATING",
                "PATCHING", "VERIFYING", "REVIEW_READY",
            }
            runs = db.scalars(select(Run).where(
                Run.model_entry_id == model.id, Run.state.in_(open_states),
            ).order_by(Run.id).with_for_update()).all()
            for run in runs:
                prior = run.state
                run.state = "PAUSED_APPROVAL"
                run.resume_target = prior
                run.lease_fence += 1
                run.lease_owner = None
                run.lease_until = None
                append_event(db, run, "run.state_changed", {
                    "state": run.state, "verdict": run.verdict,
                })
                append_event(db, run, "approval.required", {
                    "kind": "model_emergency_disable", "model_entry_id": model.id,
                })
                leases = db.scalars(select(SandboxLease).where(
                    SandboxLease.run_id == run.id,
                    SandboxLease.state.in_(["intended", "bootstrapping", "provisioned"]),
                ).with_for_update()).all()
                for lease in leases:
                    lease.state = "revoked"
                    lease.updated_at = utcnow()
                    db.add(OutboxEvent(
                        tenant_id=run.tenant_id, topic="sandbox.cleanup",
                        payload={"run_id": run.id, "sandbox_lease_id": lease.id},
                    ))
                    append_event(db, run, "sandbox.revoked", {
                        "sandbox_lease_id": lease.id, "reason": "unsafe",
                    })
                affected_runs += 1
        _registry_event(db, model, operator, action, model.state, reason.strip())
        db.commit()
        return {
            "model_entry_id": model.id, "previous_state": before,
            "state": model.state, "affected_runs": affected_runs,
        }


def _check_usage(turn: ProviderTurn) -> None:
    if (
        not isinstance(turn.usage.get("input_tokens"), int)
        or turn.usage["input_tokens"] < 1
        or not isinstance(turn.usage.get("output_tokens"), int)
        or turn.usage["output_tokens"] < 1
    ):
        raise QualificationError("MODEL_QUALIFICATION_FAILED", "Probe usage is missing or empty")


def probe_provider(adapter: QualificationAdapter, model_id: str, output_limit: int) -> dict:
    nonce = secrets.token_hex(12)
    instruction = "Follow the user's request. Keep the reply short."
    text_turn = adapter.generate(
        model_id, instruction, f"Reply with this token: {nonce}", {}, min(output_limit, 256)
    )
    _check_usage(text_turn)
    if (
        text_turn.provider != adapter.provider
        or text_turn.calls
        or nonce not in text_turn.text
        or text_turn.stop_reason not in {"completed", "end_turn", "STOP"}
    ):
        raise QualificationError("MODEL_QUALIFICATION_FAILED", "Text probe did not complete")
    tool = ToolDefinition(
        name="qualification_echo",
        permission="qualification.echo",
        effect_class="read",
        source_version="1.0",
        input_schema={
            "type": "object",
            "properties": {"nonce": {"const": nonce}},
            "required": ["nonce"],
            "additionalProperties": False,
        },
    )
    tool_turn = adapter.generate(
        model_id,
        "Call qualification_echo once with the requested nonce. Do not answer in text yet.",
        f"Call qualification_echo with nonce {nonce}.",
        {tool.name: tool},
        min(output_limit, 256),
    )
    _check_usage(tool_turn)
    if (
        tool_turn.provider != adapter.provider
        or len(tool_turn.calls) != 1
        or tool_turn.calls[0].name != tool.name
        or tool_turn.calls[0].arguments != {"nonce": nonce}
        or tool_turn.stop_reason not in {"completed", "tool_use", "STOP"}
    ):
        raise QualificationError("MODEL_QUALIFICATION_FAILED", "Tool probe did not complete")
    completed = adapter.generate(
        model_id,
        "Reply with the nonce from the completed tool result.",
        f"Return the nonce {nonce}.",
        {tool.name: tool},
        min(output_limit, 256),
        previous=tool_turn,
        results={tool_turn.calls[0].call_id: {"status": "ok", "nonce": nonce}},
    )
    _check_usage(completed)
    if (
        completed.provider != adapter.provider
        or completed.calls
        or nonce not in completed.text
        or completed.stop_reason not in {"completed", "end_turn", "STOP"}
        or len({text_turn.model, tool_turn.model, completed.model}) != 1
    ):
        raise QualificationError(
            "MODEL_QUALIFICATION_FAILED", "Continuation probe did not complete"
        )
    streamed_text = adapter.generate(
        model_id, instruction, f"Reply with this token: {nonce}", {},
        min(output_limit, 256), stream=True,
    )
    _check_usage(streamed_text)
    if (
        streamed_text.provider != adapter.provider
        or streamed_text.calls
        or nonce not in streamed_text.text
        or streamed_text.stop_reason not in {"completed", "end_turn", "STOP"}
    ):
        raise QualificationError("MODEL_QUALIFICATION_FAILED", "Streamed text did not complete")
    streamed_tool = adapter.generate(
        model_id,
        "Call qualification_echo once with the requested nonce. Do not answer in text yet.",
        f"Call qualification_echo with nonce {nonce}.",
        {tool.name: tool}, min(output_limit, 256), stream=True,
    )
    _check_usage(streamed_tool)
    if (
        streamed_tool.provider != adapter.provider
        or len(streamed_tool.calls) != 1
        or streamed_tool.calls[0].name != tool.name
        or streamed_tool.calls[0].arguments != {"nonce": nonce}
        or streamed_tool.stop_reason not in {"completed", "tool_use", "STOP"}
    ):
        raise QualificationError("MODEL_QUALIFICATION_FAILED", "Streamed tool did not complete")
    streamed_completed = adapter.generate(
        model_id,
        "Reply with the nonce from the completed tool result.",
        f"Return the nonce {nonce}.",
        {tool.name: tool}, min(output_limit, 256),
        previous=streamed_tool,
        results={streamed_tool.calls[0].call_id: {"status": "ok", "nonce": nonce}},
        stream=True,
    )
    _check_usage(streamed_completed)
    if (
        streamed_completed.provider != adapter.provider
        or streamed_completed.calls
        or nonce not in streamed_completed.text
        or streamed_completed.stop_reason not in {"completed", "end_turn", "STOP"}
        or len({
            text_turn.model, tool_turn.model, completed.model,
            streamed_text.model, streamed_tool.model, streamed_completed.model,
        }) != 1
    ):
        raise QualificationError(
            "MODEL_QUALIFICATION_FAILED", "Streamed continuation did not complete"
        )
    all_turns = (
        text_turn, tool_turn, completed, streamed_text, streamed_tool, streamed_completed
    )
    return {
        "status": "passed",
        "provider": adapter.provider,
        "requested_model": model_id,
        "resolved_model": text_turn.model,
        "nonce_sha256": hashlib.sha256(nonce.encode()).hexdigest(),
        "checks": [
            "text", "schema_validated_tool", "continuation",
            "streamed_text", "streamed_tool", "streamed_continuation", "usage",
        ],
        "usage": {
            "input_tokens": sum(
                turn.usage["input_tokens"] for turn in all_turns
            ),
            "output_tokens": sum(
                turn.usage["output_tokens"] for turn in all_turns
            ),
        },
    }


def _attestation_matches(model: ModelEntry, attestation: MetadataAttestation) -> bool:
    return bool(
        model.registry_revision == attestation.registry_revision
        and model.context_limit == attestation.context_limit
        and model.output_limit == attestation.output_limit
        and model.price_revision == attestation.price_revision
        and model.price_per_m_input is not None
        and model.price_per_m_output is not None
        and Decimal(model.price_per_m_input) == attestation.price_per_m_input
        and Decimal(model.price_per_m_output) == attestation.price_per_m_output
    )


def qualify_model_entry(
    session_factory,
    model_entry_id: str,
    adapter: QualificationAdapter,
    attestation: MetadataAttestation,
    operator: str,
    enable: bool = False,
) -> dict:
    if not operator or len(operator) > 200:
        raise QualificationError("INVALID_OPERATOR", "Operator identity is required")
    with session_factory() as db:
        model = db.scalar(
            select(ModelEntry).where(ModelEntry.id == model_entry_id).with_for_update()
        )
        if model is None:
            raise QualificationError("NOT_FOUND", "Model entry not found")
        if model.provider != adapter.provider or not _attestation_matches(model, attestation):
            raise QualificationError(
                "MODEL_REVISION_CHANGED", "Provider or attested metadata changed"
            )
        if model.state == "validating":
            raise QualificationError("MODEL_VALIDATING", "Qualification is already in progress")
        active_run = db.scalar(
            select(Run.id)
            .where(
                Run.model_entry_id == model.id,
                Run.state.not_in({"COMPLETED", "INCONCLUSIVE", "FAILED", "CANCELLED"}),
            )
            .limit(1)
        )
        if active_run is not None:
            raise QualificationError("MODEL_IN_USE", "An active run pins this model revision")
        previous_state = model.state
        model_id = model.model_id
        revision = model.registry_revision
        model.state = "validating"
        model.validated_at = None
        model.capabilities = {
            **(model.capabilities or {}),
            "live_qualified": False,
            "qualification": {
                "status": "validating",
                "registry_revision": revision,
                "started_at": utcnow().isoformat(),
                "operator": operator,
            },
        }
        _registry_event(db, model, operator, "qualify", "validating")
        db.commit()
    try:
        report = probe_provider(adapter, model_id, attestation.output_limit)
    except Exception as error:
        with session_factory() as db:
            model = db.scalar(
                select(ModelEntry).where(ModelEntry.id == model_entry_id).with_for_update()
            )
            if model and model.state == "validating" and model.registry_revision == revision:
                model.state = "disabled" if previous_state == "enabled" else "registered"
                model.capabilities = {
                    **(model.capabilities or {}),
                    "live_qualified": False,
                    "qualification": {
                        "status": "failed",
                        "registry_revision": revision,
                        "checked_at": utcnow().isoformat(),
                        "operator": operator,
                        "failure_class": type(error).__name__,
                    },
                }
                _registry_event(db, model, operator, "qualify", "failed")
                db.commit()
        raise QualificationError(
            "MODEL_QUALIFICATION_FAILED", f"Provider probe failed: {type(error).__name__}"
        ) from error
    with session_factory() as db:
        model = db.scalar(
            select(ModelEntry).where(ModelEntry.id == model_entry_id).with_for_update()
        )
        if (
            model is None
            or model.state != "validating"
            or model.registry_revision != revision
            or model.model_id != model_id
            or model.provider != adapter.provider
            or not _attestation_matches(model, attestation)
        ):
            raise QualificationError("MODEL_REVISION_CHANGED", "Model changed during qualification")
        checked_at = utcnow()
        model.validated_at = checked_at
        model.state = "enabled" if enable else "qualified"
        model.capabilities = {
            **(model.capabilities or {}),
            "live_qualified": True,
            "qualification": {
                **report,
                "registry_revision": revision,
                "context_limit": model.context_limit,
                "output_limit": model.output_limit,
                "price_revision": model.price_revision,
                "price_per_m_input": str(model.price_per_m_input),
                "price_per_m_output": str(model.price_per_m_output),
                "adapter_digest": adapter_digest(),
                "limits_source_url": attestation.limits_source_url,
                "pricing_source_url": attestation.pricing_source_url,
                "metadata_effective_date": attestation.effective_date,
                "checked_at": checked_at.isoformat(),
                "operator": operator,
            },
        }
        _registry_event(db, model, operator, "qualify", model.state)
        db.commit()
        return {
            "model_entry_id": model.id,
            "state": model.state,
            "provider": model.provider,
            "resolved_model": report["resolved_model"],
            "registry_revision": revision,
            "checked_at": checked_at.isoformat(),
            "checks": report["checks"],
            "usage": report["usage"],
        }


def load_attestation(path: Path) -> MetadataAttestation:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise QualificationError("INVALID_ATTESTATION", "Attestation file is unreadable") from error
    if not isinstance(raw, dict):
        raise QualificationError("INVALID_ATTESTATION", "Attestation must be an object")
    return MetadataAttestation.from_dict(raw)
