"""Durable publication wakeups created only by explicit draft PR approval."""

from __future__ import annotations

import os
from datetime import UTC, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from platform_app.db import utcnow
from platform_app.github_publication import GitHubDraftPublisher, GitHubPublicationError
from platform_app.models import OutboxEvent, PublicationApproval, RepositoryConnection
from platform_app.publication_dispatch import publish_approved_run
from platform_app.repository_connections import environment_secret_name
from platform_app.run_ledger import aware
from platform_app.service import ServiceError, canonical_hash


def _event_id(approval_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"aip-publication:{approval_id}"))


def _binding(approval: PublicationApproval) -> str:
    return canonical_hash({
        "connection_id": approval.connection_id,
        "destination": approval.destination,
        "base_commit": approval.base_commit,
        "patch_sha256": approval.patch_sha256,
        "test_evidence_sha256": approval.test_evidence_sha256,
        "expires_at": aware(approval.expires_at).astimezone(UTC).isoformat(),
    })


def enqueue_approved_draft(db: Session, approval: PublicationApproval) -> OutboxEvent:
    if approval.status != "approved":
        raise ServiceError("PUBLICATION_APPROVAL_INVALID", "Draft approval is not active", 409)
    event = db.get(OutboxEvent, _event_id(approval.id))
    payload = {
        "approval_id": approval.id, "run_id": approval.run_id,
        "binding_sha256": _binding(approval),
    }
    if event is None:
        event = OutboxEvent(
            id=_event_id(approval.id), tenant_id=approval.tenant_id,
            topic="publication.dispatch", payload=payload,
        )
        db.add(event)
    elif event.tenant_id != approval.tenant_id or event.topic != "publication.dispatch":
        raise ServiceError("PUBLICATION_OUTBOX_CONFLICT", "Publication event changed", 409)
    elif {key: event.payload.get(key) for key in payload} != payload:
        event.payload = payload
        event.status = "pending"
        event.attempts = 0
        event.processing_token = None
        event.processing_lease_until = None
    elif event.status == "failed":
        event.status = "pending"
        event.attempts = 0
    return event


def _draft_text(approval: PublicationApproval) -> tuple[str, str]:
    title = f"Proposed repair for run {approval.run_id[:12]}"
    body = (
        "AI Engineering Platform draft repair. Human review is required before merge.\n\n"
        f"Run: {approval.run_id}\n"
        f"Base commit: {approval.base_commit}\n"
        f"Patch SHA-256: {approval.patch_sha256}\n"
        f"Verification evidence SHA-256: {approval.test_evidence_sha256}\n\n"
        "Verification covers only the checks named in the run review packet. "
        "This draft does not deploy the change."
    )
    return title, body


def dispatch_one_approved_draft(
    session_factory, artifact_root: Path,
    *, publisher_factory=GitHubDraftPublisher,
) -> str | None:
    """Lease one event; a crash permits later GitHub reconciliation."""
    with session_factory() as db:
        event = db.scalar(select(OutboxEvent).where(
            OutboxEvent.topic == "publication.dispatch",
            or_(
                OutboxEvent.status == "pending",
                (OutboxEvent.status == "processing") & (
                    OutboxEvent.processing_lease_until <= utcnow()
                ),
            ),
        ).order_by(OutboxEvent.created_at, OutboxEvent.id)
            .with_for_update(skip_locked=True).limit(1))
        if event is None:
            return None
        event_id = event.id
        token = str(uuid4())
        event.status = "processing"
        event.attempts += 1
        event.processing_token = token
        event.processing_lease_until = utcnow() + timedelta(minutes=10)
        payload = event.payload
        tenant_id = event.tenant_id
        db.commit()
    outcome, error_code = "delivered", None
    skip_publish = False
    try:
        with session_factory() as db:
            approval = db.get(PublicationApproval, payload.get("approval_id"))
            if (
                approval is None or approval.tenant_id != tenant_id
                or approval.run_id != payload.get("run_id")
                or event_id != _event_id(approval.id)
            ):
                raise ServiceError("PUBLICATION_OUTBOX_CONFLICT", "Dispatch scope changed", 409)
            if approval.status in {"consumed", "revoked"}:
                skip_publish = True
            else:
                if payload.get("binding_sha256") != _binding(approval):
                    raise ServiceError(
                        "PUBLICATION_OUTBOX_CONFLICT", "Approval binding changed", 409
                    )
                connection = db.get(RepositoryConnection, approval.connection_id)
                if connection is None or connection.status != "ready":
                    raise ServiceError("REPOSITORY_UNAVAILABLE", "Connection is unavailable", 409)
                credential = os.environ.get(
                    environment_secret_name(connection.credential_ref), ""
                )
                if not credential:
                    raise ServiceError("CREDENTIAL_UNAVAILABLE", "GitHub token is unavailable", 409)
                title, body = _draft_text(approval)
                approval_id = approval.id
        if not skip_publish:
            publisher = publisher_factory(credential)
            publish_approved_run(
                session_factory, approval_id, artifact_root, publisher,
                title=title, body=body,
            )
    except (ServiceError, GitHubPublicationError, OSError, ValueError) as error:
        outcome = "failed" if isinstance(error, ServiceError) and (
            error.code == "PUBLICATION_OUTBOX_CONFLICT"
        ) else "pending"
        error_code = error.code if isinstance(error, ServiceError) else type(error).__name__
    return _finish(session_factory, event_id, token, outcome, error_code)


def _finish(
    session_factory, event_id: str, token: str,
    status: str, error_code: str | None = None,
) -> str:
    with session_factory() as db:
        event = db.scalar(select(OutboxEvent).where(
            OutboxEvent.id == event_id,
            OutboxEvent.topic == "publication.dispatch",
        ).with_for_update())
        if event is None or event.processing_token != token:
            return event_id
        if status == "pending" and event.attempts >= 5:
            status = "failed"
        event.status = status
        event.processing_token = None
        event.processing_lease_until = None
        if error_code:
            event.payload = {**event.payload, "last_error_code": error_code}
        elif "last_error_code" in event.payload:
            event.payload = {
                key: value for key, value in event.payload.items()
                if key != "last_error_code"
            }
        db.commit()
    return event_id
