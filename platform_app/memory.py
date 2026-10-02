"""Canonical, source-backed project memory with a scoped lexical fallback."""

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from platform_app.db import utcnow
from platform_app.models import MemoryFact, OutboxEvent
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
) -> MemoryFact:
    if not source_refs:
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
    return fact


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
    db.add(
        OutboxEvent(
            tenant_id=fact.tenant_id,
            topic="memory.project",
            payload={"fact_id": fact.id, "status": "verified"},
        )
    )


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


def delete_fact(db: Session, fact: MemoryFact) -> None:
    if fact.status == "deleted":
        return
    fact.status = "deleted"
    fact.deleted_at = utcnow()
    fact.valid_until = fact.deleted_at
    db.add(
        OutboxEvent(
            tenant_id=fact.tenant_id,
            topic="memory.project",
            payload={"fact_id": fact.id, "status": "deleted"},
        )
    )
