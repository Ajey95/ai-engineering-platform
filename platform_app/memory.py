"""Canonical, source-backed project memory with a scoped lexical fallback."""

import re

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from platform_app.db import utcnow
from platform_app.models import MemoryFact, MemoryFactEvent, OutboxEvent
from platform_app.service import ServiceError
from platform_app.telemetry import tracer


def propose_fact(
    db: Session,
    tenant_id: str,
    project_id: str,
    repository_ref: str,
    source_revision: str,
    fact_type: str,
    subject: str,
    statement: str,
    source_refs: list[str],
    actor: str = "system",
) -> MemoryFact:
    if not source_refs or not actor.strip():
        raise ServiceError("UNSUPPORTED_MEMORY", "Memory requires source references", 400)
    fact = MemoryFact(
        tenant_id=tenant_id,
        project_id=project_id,
        repository_ref=repository_ref,
        source_revision=source_revision,
        fact_type=fact_type,
        subject=subject,
        statement=statement,
        source_refs=source_refs,
        status="proposed",
    )
    db.add(fact)
    db.flush()
    _event(db, fact, None, actor, "Fact proposed", source_refs[0])
    return fact


def _event(
    db: Session, fact: MemoryFact, previous: str | None, actor: str,
    reason: str, evidence_ref: str | None = None,
) -> None:
    db.add(MemoryFactEvent(
        tenant_id=fact.tenant_id, project_id=fact.project_id, fact_id=fact.id,
        previous_status=previous, status=fact.status, actor=actor,
        reason=reason, evidence_ref=evidence_ref,
    ))


def _project(db: Session, fact: MemoryFact) -> None:
    db.add(OutboxEvent(
        tenant_id=fact.tenant_id, topic="memory.project",
        payload={"fact_id": fact.id, "status": fact.status},
    ))


def verify_fact(
    db: Session, fact: MemoryFact, evidence_ref: str, verification_scope: str, reviewer_or_tool: str
) -> None:
    if fact.status != "proposed":
        raise ServiceError("MEMORY_STATE", "Only proposed facts can be verified", 409)
    if not evidence_ref or evidence_ref not in fact.source_refs:
        raise ServiceError("UNSUPPORTED_MEMORY", "Verification evidence must be linked", 400)
    if not verification_scope or not reviewer_or_tool:
        raise ServiceError("UNSUPPORTED_MEMORY", "Verification scope and actor are required", 400)
    fact.status = "verified"
    fact.verification_scope = verification_scope
    fact.valid_from = utcnow()
    _event(db, fact, "proposed", reviewer_or_tool, verification_scope, evidence_ref)
    _project(db, fact)


@tracer.start_as_current_span("memory.lookup")
def scoped_lookup(
    db: Session, tenant_id: str, project_id: str, source_revision: str, query: str, limit: int = 10
) -> list[MemoryFact]:
    if not 1 <= limit <= 50:
        raise ServiceError("INVALID_LIMIT", "Limit must be between 1 and 50", 400)
    now = utcnow()
    escaped_query = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    statement = (
        select(MemoryFact)
        .where(
            MemoryFact.tenant_id == tenant_id,
            MemoryFact.project_id == project_id,
            MemoryFact.source_revision == source_revision,
            MemoryFact.status == "verified",
            MemoryFact.valid_from.is_not(None),
            MemoryFact.valid_from <= now,
            or_(MemoryFact.valid_until.is_(None), MemoryFact.valid_until > now),
            or_(
                MemoryFact.subject.ilike(f"%{escaped_query}%", escape="\\"),
                MemoryFact.statement.ilike(f"%{escaped_query}%", escape="\\"),
            ),
        )
        .order_by(MemoryFact.created_at.desc())
        .limit(limit)
    )
    return list(db.scalars(statement).all())


def select_context_facts(
    db: Session, tenant_id: str, project_id: str, source_revision: str,
    task_report: str, limit: int = 5,
) -> list[MemoryFact]:
    """Select only current verified facts sharing terms with this run's report."""
    if not 1 <= limit <= 10:
        raise ServiceError("INVALID_LIMIT", "Memory selection limit is invalid", 400)
    terms = list(dict.fromkeys(
        word.lower() for word in re.findall(r"[A-Za-z0-9]{4,}", task_report)
        if word.lower() not in {"with", "from", "this", "that", "when", "then"}
    ))[:8]
    if not terms:
        return []
    now = utcnow()
    relevant = or_(*(
        or_(
            MemoryFact.subject.ilike(f"%{term}%"),
            MemoryFact.statement.ilike(f"%{term}%"),
        ) for term in terms
    ))
    statement = (
        select(MemoryFact)
        .where(
            MemoryFact.tenant_id == tenant_id,
            MemoryFact.project_id == project_id,
            MemoryFact.source_revision == source_revision,
            MemoryFact.status == "verified",
            MemoryFact.valid_from.is_not(None),
            MemoryFact.valid_from <= now,
            or_(MemoryFact.valid_until.is_(None), MemoryFact.valid_until > now),
            relevant,
        )
        .order_by(MemoryFact.created_at.desc(), MemoryFact.id)
        .limit(limit)
    )
    return list(db.scalars(statement).all())


def reject_fact(db: Session, fact: MemoryFact, actor: str, reason: str) -> None:
    if fact.status not in {"proposed", "verified"}:
        raise ServiceError("MEMORY_STATE", "Only proposed or verified facts can be rejected", 409)
    if not actor.strip() or len(reason.strip()) < 5:
        raise ServiceError("MEMORY_REASON_REQUIRED", "Rejection needs an actor and reason", 400)
    previous = fact.status
    fact.status = "rejected"
    fact.valid_until = utcnow() if previous == "verified" else fact.valid_until
    _event(db, fact, previous, actor, reason.strip())
    _project(db, fact)


def supersede_fact(
    db: Session, fact: MemoryFact, replacement: MemoryFact, actor: str, reason: str,
) -> None:
    if fact.status != "verified" or replacement.status != "verified":
        raise ServiceError("MEMORY_STATE", "Both facts must be verified", 409)
    if (
        fact.id == replacement.id or fact.tenant_id != replacement.tenant_id
        or fact.project_id != replacement.project_id
        or fact.repository_ref != replacement.repository_ref
        or fact.subject != replacement.subject
    ):
        raise ServiceError("MEMORY_SCOPE", "Replacement fact has a different scope", 409)
    if not actor.strip() or len(reason.strip()) < 5:
        raise ServiceError("MEMORY_REASON_REQUIRED", "Supersession needs a reason", 400)
    fact.status = "superseded"
    fact.valid_until = utcnow()
    _event(db, fact, "verified", actor, reason.strip(), replacement.id)
    _project(db, fact)


def expire_fact(db: Session, fact: MemoryFact, actor: str, reason: str) -> None:
    if fact.status != "verified":
        raise ServiceError("MEMORY_STATE", "Only verified facts can expire", 409)
    if not actor.strip() or len(reason.strip()) < 5:
        raise ServiceError("MEMORY_REASON_REQUIRED", "Expiry needs an actor and reason", 400)
    fact.status = "expired"
    fact.valid_until = utcnow()
    _event(db, fact, "verified", actor, reason.strip())
    _project(db, fact)


def delete_fact(
    db: Session, fact: MemoryFact, actor: str = "system", reason: str = "Deleted by policy"
) -> None:
    if fact.status == "deleted":
        return
    previous = fact.status
    fact.status = "deleted"
    fact.deleted_at = utcnow()
    fact.valid_until = fact.deleted_at
    _event(db, fact, previous, actor, reason)
    _project(db, fact)
