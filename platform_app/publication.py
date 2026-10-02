"""Explicit, expiring draft PR authority bound to an exact verified run."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from platform_app.models import (
    AuditEvent,
    PublicationApproval,
    RepositoryConnection,
    Run,
    RunEvent,
    ToolAction,
)
from platform_app.service import ServiceError, canonical_hash

BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")
DIGEST = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_STEPS = ("candidate_patch", "candidate_named", "candidate_browser", "candidate_oracle")


def _binding(db: Session, run: Run) -> tuple[str, str]:
    if run.state != "COMPLETED" or run.verdict != "PASSED":
        raise ServiceError("PUBLICATION_NOT_READY", "Run is not accepted and verified", 409)
    decision = db.scalar(select(RunEvent).where(
        RunEvent.run_id == run.id, RunEvent.event_type == "review.decision"
    ).order_by(RunEvent.sequence.desc()).limit(1))
    if decision is None or (decision.payload or {}).get("decision") != "accepted":
        raise ServiceError("PUBLICATION_NOT_READY", "Reviewer acceptance is required", 409)
    actions = db.scalars(select(ToolAction).where(
        ToolAction.tenant_id == run.tenant_id, ToolAction.run_id == run.id,
        ToolAction.step_id.in_(REQUIRED_STEPS),
    )).all()
    by_step = {action.step_id: action for action in actions}
    if len(actions) != len(REQUIRED_STEPS) or set(by_step) != set(REQUIRED_STEPS):
        raise ServiceError("PUBLICATION_NOT_READY", "Verification evidence is incomplete", 409)
    for action in actions:
        if action.status != "COMPLETED" or not isinstance(action.receipt, dict):
            raise ServiceError("PUBLICATION_NOT_READY", "Verification evidence is incomplete", 409)
    patch = by_step["candidate_patch"].receipt
    patch_hash = patch.get("patch_sha256")
    if (
        patch.get("status") != "COMPLETED" or patch.get("provider_mode") != "native_api"
        or not isinstance(patch_hash, str) or not DIGEST.fullmatch(patch_hash)
        or not patch.get("changed_files")
    ):
        raise ServiceError("PUBLICATION_NOT_READY", "Verified live patch is required", 409)
    for step in REQUIRED_STEPS[1:]:
        if by_step[step].receipt.get("status") != "PASSED":
            raise ServiceError("PUBLICATION_NOT_READY", "Candidate checks did not pass", 409)
    evidence = {
        step: {
            "effect_key": by_step[step].effect_key,
            "arguments_hash": by_step[step].arguments_hash,
            "receipt": by_step[step].receipt,
        }
        for step in REQUIRED_STEPS
    }
    return patch_hash, canonical_hash(evidence)


def _now(value: datetime | None) -> datetime:
    return value or datetime.now(UTC)


def _active(approval: PublicationApproval, now: datetime) -> bool:
    expiry = approval.expires_at
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=UTC)
    return approval.status == "approved" and expiry > now


def approve_draft_pr(
    db: Session, *, tenant_id: str, run_id: str, connection_id: str,
    base_branch: str, actor: str, now: datetime | None = None,
) -> PublicationApproval:
    if (
        not BRANCH.fullmatch(base_branch) or ".." in base_branch
        or "//" in base_branch or base_branch.endswith(("/", "."))
    ):
        raise ServiceError("PUBLICATION_DESTINATION_INVALID", "Base branch is invalid", 400)
    now = _now(now)
    run = db.scalar(select(Run).where(
        Run.id == run_id, Run.tenant_id == tenant_id
    ).with_for_update())
    if run is None:
        raise ServiceError("NOT_FOUND", "Run not found", 404)
    connection = db.scalar(select(RepositoryConnection).where(
        RepositoryConnection.id == connection_id,
        RepositoryConnection.tenant_id == tenant_id,
        RepositoryConnection.project_id == run.project_id,
    ))
    if (
        connection is None or connection.status != "ready"
        or run.config_snapshot.get("repository_connection_id") != connection.id
    ):
        raise ServiceError(
            "PUBLICATION_DESTINATION_UNAVAILABLE", "Run destination is not ready", 409
        )
    patch_hash, evidence_hash = _binding(db, run)
    destination = f"github:{connection.repository_ref}@{base_branch}"
    row = db.scalar(select(PublicationApproval).where(
        PublicationApproval.tenant_id == tenant_id,
        PublicationApproval.run_id == run_id,
        PublicationApproval.action == "draft_pr",
    ).with_for_update())
    if row is not None and _active(row, now):
        if (
            row.connection_id == connection_id and row.destination == destination
            and row.base_commit == run.base_commit and row.patch_sha256 == patch_hash
            and row.test_evidence_sha256 == evidence_hash
        ):
            return row
        raise ServiceError("PUBLICATION_APPROVAL_CONFLICT", "Existing approval differs", 409)
    if row is None:
        row = PublicationApproval(
            tenant_id=tenant_id, project_id=run.project_id, run_id=run_id,
            connection_id=connection_id,
        )
        db.add(row)
    row.action = "draft_pr"
    row.destination = destination
    row.base_commit = run.base_commit
    row.patch_sha256 = patch_hash
    row.test_evidence_sha256 = evidence_hash
    row.actor = actor
    row.status = "approved"
    row.expires_at = now + timedelta(hours=24)
    db.flush()
    db.add(AuditEvent(
        tenant_id=tenant_id, actor=actor, action="publication.approve_draft_pr",
        target_ref=row.id, arguments_hash=canonical_hash({
            "destination": destination, "base_commit": run.base_commit,
            "patch_sha256": patch_hash, "test_evidence_sha256": evidence_hash,
            "expires_at": row.expires_at.isoformat(),
        }), policy_revision=run.config_snapshot.get("policy_version", "1.0"),
        outcome="approved",
    ))
    return row


def verify_draft_pr_approval(
    db: Session, approval: PublicationApproval, *, now: datetime | None = None,
) -> None:
    if not _active(approval, _now(now)):
        raise ServiceError(
            "PUBLICATION_APPROVAL_INVALID", "Approval has expired or was revoked", 409
        )
    run = db.scalar(select(Run).where(
        Run.id == approval.run_id, Run.tenant_id == approval.tenant_id,
        Run.project_id == approval.project_id,
    ))
    connection = db.scalar(select(RepositoryConnection).where(
        RepositoryConnection.id == approval.connection_id,
        RepositoryConnection.tenant_id == approval.tenant_id,
        RepositoryConnection.project_id == approval.project_id,
    ))
    if run is None or connection is None or connection.status != "ready":
        raise ServiceError("PUBLICATION_APPROVAL_INVALID", "Destination is no longer ready", 409)
    patch_hash, evidence_hash = _binding(db, run)
    if (
        run.config_snapshot.get("repository_connection_id") != connection.id
        or approval.base_commit != run.base_commit or approval.patch_sha256 != patch_hash
        or approval.test_evidence_sha256 != evidence_hash
        or not approval.destination.startswith(f"github:{connection.repository_ref}@")
    ):
        raise ServiceError("PUBLICATION_APPROVAL_INVALID", "Approval binding changed", 409)
