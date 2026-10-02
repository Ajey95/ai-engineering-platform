"""Deterministic, evidence-gated automatic selection for a tenant's models."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from platform_app.model_qualification import qualification_current
from platform_app.models import ModelEntry, ModelRoutingEvidence, Project, Tenant

REQUIRED_CHECKS = {"text", "schema_validated_tool", "continuation", "usage"}
EVIDENCE_MAX_AGE = timedelta(days=30)
MIN_PLATFORM_SAMPLES = 30


class RoutingError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class RoutingDecision:
    model: ModelEntry
    evidence: ModelRoutingEvidence
    score: Decimal
    policy_revision: str


def _positive_weight(value: object) -> Decimal:
    try:
        weight = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as error:
        raise RoutingError("MODEL_ROUTING_POLICY_INVALID", "Routing weight is invalid") from error
    if not weight.is_finite() or weight < 0 or weight > 100:
        raise RoutingError("MODEL_ROUTING_POLICY_INVALID", "Routing weight is invalid")
    return weight


def validate_routing_policy(policy: dict) -> tuple[set[str], set[str], dict, str]:
    if not isinstance(policy, dict) or policy.get("enabled") is not True:
        raise RoutingError("MODEL_ROUTING_DISABLED", "Automatic routing is not enabled")
    allowed_ids = policy.get("allowed_model_entry_ids")
    allowed_classes = policy.get("allowed_data_classes")
    weights = policy.get("weights")
    if set(policy) != {
        "enabled", "allowed_model_entry_ids", "allowed_data_classes", "weights"
    }:
        raise RoutingError("MODEL_ROUTING_POLICY_INVALID", "Routing policy is incomplete")
    if (
        not isinstance(allowed_ids, list)
        or not allowed_ids
        or any(not isinstance(item, str) or not item for item in allowed_ids)
        or not isinstance(allowed_classes, list)
        or not allowed_classes
        or any(not isinstance(item, str) or not item for item in allowed_classes)
        or not isinstance(weights, dict)
        or set(weights) != {"utility", "latency", "cost"}
    ):
        raise RoutingError("MODEL_ROUTING_POLICY_INVALID", "Routing policy is incomplete")
    if len(set(allowed_ids)) != len(allowed_ids):
        raise RoutingError("MODEL_ROUTING_POLICY_INVALID", "Model allowlist has duplicates")
    parsed_weights = {name: _positive_weight(value) for name, value in weights.items()}
    if not any(parsed_weights.values()):
        raise RoutingError("MODEL_ROUTING_POLICY_INVALID", "Routing weights cannot all be zero")
    digest = hashlib.sha256(
        json.dumps(policy, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return set(allowed_ids), set(allowed_classes), parsed_weights, digest


def _policy(tenant: Tenant, project: Project) -> tuple[set[str], dict, str]:
    allowed_ids, allowed_classes, weights, digest = validate_routing_policy(
        tenant.model_routing_policy or {}
    )
    data_class = (project.environment_manifest or {}).get("data_class", "source_code")
    if not isinstance(data_class, str) or data_class not in allowed_classes:
        raise RoutingError("MODEL_DATA_POLICY_DENIED", "Project data class is not authorized")
    return allowed_ids, weights, digest


def select_qualified_model(
    db: Session,
    tenant: Tenant,
    project: Project,
    *,
    task_class: str = "repair",
    required_context_tokens: int = 8192,
    required_output_tokens: int = 1024,
    now: datetime | None = None,
) -> RoutingDecision:
    """Route only on tenant policy, current qualification and platform evidence."""
    if tenant.id != project.tenant_id:
        raise RoutingError("MODEL_DATA_POLICY_DENIED", "Project is outside the tenant")
    if required_context_tokens < 1 or required_output_tokens < 1:
        raise RoutingError("MODEL_ROUTING_POLICY_INVALID", "Required capacity is invalid")
    allowed_ids, weights, policy_digest = _policy(tenant, project)
    now = now or datetime.now(UTC)
    models = db.scalars(select(ModelEntry).where(ModelEntry.id.in_(allowed_ids))).all()
    decisions: list[RoutingDecision] = []
    for model in models:
        if (
            not qualification_current(model)
            or (model.context_limit or 0) < required_context_tokens
            or (model.output_limit or 0) < required_output_tokens
        ):
            continue
        checks = set((model.capabilities or {}).get("qualification", {}).get("checks", []))
        if not REQUIRED_CHECKS.issubset(checks):
            continue
        evidence = db.scalars(
            select(ModelRoutingEvidence)
            .where(
                ModelRoutingEvidence.model_entry_id == model.id,
                ModelRoutingEvidence.registry_revision == model.registry_revision,
                ModelRoutingEvidence.task_class == task_class,
                ModelRoutingEvidence.available.is_(True),
                ModelRoutingEvidence.sample_count >= MIN_PLATFORM_SAMPLES,
            )
            .order_by(ModelRoutingEvidence.observed_at.desc())
        ).first()
        if evidence is None:
            continue
        observed = evidence.observed_at
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=UTC)
        if observed > now or now - observed > EVIDENCE_MAX_AGE:
            continue
        utility = Decimal(evidence.success_rate)
        latency_seconds = Decimal(evidence.p95_latency_ms) / Decimal(1000)
        cost = Decimal(evidence.mean_cost_usd)
        score = (
            utility * weights["utility"]
            - latency_seconds * weights["latency"]
            - cost * weights["cost"]
        )
        decisions.append(RoutingDecision(model, evidence, score, policy_digest))
    if not decisions:
        raise RoutingError(
            "MODEL_ROUTE_UNAVAILABLE", "No authorized, qualified model has current evidence"
        )
    return sorted(decisions, key=lambda item: (-item.score, item.model.id))[0]
