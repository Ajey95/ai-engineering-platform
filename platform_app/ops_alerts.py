"""Versioned alert ownership and runbook catalog for the operations surface."""

from dataclasses import dataclass


@dataclass(frozen=True)
class AlertSpec:
    alert_id: str
    severity: str
    owner: str
    impact: str
    runbook: str
    condition: str


ALERTS = {
    item.alert_id: item for item in (
        AlertSpec(
            "cross_tenant_access", "page", "security-on-call",
            "Customer data may have crossed a tenant boundary.",
            "docs/operations/runbooks.md#security-incident",
            "Any confirmed cross-tenant access evidence",
        ),
        AlertSpec(
            "secret_exposure", "page", "security-on-call",
            "A credential may be disclosed or usable outside its intended scope.",
            "docs/operations/runbooks.md#security-incident",
            "Any confirmed secret exposure",
        ),
        AlertSpec(
            "uncontrolled_tool_effect", "page", "platform-on-call",
            "A tool may have performed an unapproved external side effect.",
            "docs/operations/runbooks.md#security-incident",
            "Any uncontrolled side effect evidence",
        ),
        AlertSpec(
            "budget_breach", "page", "platform-on-call",
            "Spend may exceed a tenant authorization ceiling.",
            "docs/operations/runbooks.md#budget-breach",
            "Any confirmed tenant budget breach",
        ),
        AlertSpec(
            "media_edge_auth_missing", "page", "security-on-call",
            "Private recordings may be served without authorization.",
            "docs/operations/runbooks.md#security-incident",
            "Any missing authorization at a media edge",
        ),
        AlertSpec(
            "runnable_queue_over_5_minutes", "warning", "platform-on-call",
            "Runs wait longer than the pilot queue target.",
            "docs/operations/runbooks.md#worker-crash",
            "Oldest runnable run >5 minutes sustained for 10 minutes",
        ),
        AlertSpec(
            "graph_projection_over_60_seconds", "warning", "platform-on-call",
            "Project memory graph freshness is delayed.",
            "docs/operations/runbooks.md#memory-unavailable",
            "Oldest graph outbox item >60 seconds",
        ),
        AlertSpec(
            "sandbox_orphan", "warning", "sandbox-on-call",
            "A guest may consume capacity beyond its cleanup grace.",
            "docs/operations/runbooks.md#sandbox-failure",
            "Orphan count nonzero beyond cleanup grace",
        ),
        AlertSpec(
            "provider_error_rate", "warning", "model-on-call",
            "Provider failures may prevent safe model continuation.",
            "docs/operations/runbooks.md#provider-unavailable",
            "Error rate >20% for 5 minutes with at least 20 calls",
        ),
        AlertSpec(
            "media_encode_age", "warning", "media-on-call",
            "Review recordings may be delayed.",
            "docs/operations/runbooks.md#media-failure",
            "Oldest media job >10 minutes",
        ),
    )
}


def current_warning_details(warning_ids: list[str]) -> list[dict[str, str]]:
    return [
        {
            "alert_id": item.alert_id,
            "severity": item.severity,
            "owner": item.owner,
            "impact": item.impact,
            "runbook": item.runbook,
            "condition": item.condition,
            "evaluation": "snapshot_only",
        }
        for alert_id in warning_ids
        if (item := ALERTS.get(alert_id)) is not None
    ]
