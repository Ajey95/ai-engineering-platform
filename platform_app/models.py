from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from platform_app.model_base import Base, new_id, utcnow

JsonType = JSON().with_variant(JSONB, "postgresql")


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(24), default="active")
    policy_revision: Mapped[str] = mapped_column(String(64), default="1.0")


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("tenant_id", "id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    repository_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    test_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    environment_manifest: Mapped[dict] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (UniqueConstraint("tenant_id", "id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    report: Mapped[str] = mapped_column(Text)
    expected_behavior: Mapped[str] = mapped_column(Text)
    actual_behavior: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (
        Index("ix_runs_tenant_state", "tenant_id", "state", "updated_at"),
        UniqueConstraint("tenant_id", "created_by", "idempotency_key"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    created_by: Mapped[str] = mapped_column(String(200))
    idempotency_key: Mapped[str] = mapped_column(String(200))
    request_hash: Mapped[str] = mapped_column(String(64))
    base_commit: Mapped[str] = mapped_column(String(64))
    model_entry_id: Mapped[str] = mapped_column(String(100))
    state: Mapped[str] = mapped_column(String(40), default="QUEUED")
    verdict: Mapped[str] = mapped_column(String(24), default="NOT_RUN")
    media_status: Mapped[str] = mapped_column(String(24), default="PENDING")
    config_snapshot: Mapped[dict] = mapped_column(JsonType)
    lease_owner: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lease_fence: Mapped[int] = mapped_column(Integer, default=0)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    last_sequence: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    task: Mapped[Task] = relationship()


class RunEvent(Base):
    __tablename__ = "run_events"
    __table_args__ = (UniqueConstraint("run_id", "sequence"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict] = mapped_column(JsonType, default=dict)
    trace_id: Mapped[str] = mapped_column(String(64), default=new_id)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    topic: Mapped[str] = mapped_column(String(80), index=True)
    payload: Mapped[dict] = mapped_column(JsonType)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BudgetEntry(Base):
    __tablename__ = "budget_ledger"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    category: Mapped[str] = mapped_column(String(50))
    reserved_usd: Mapped[float] = mapped_column(Numeric(12, 6))
    actual_usd: Mapped[float] = mapped_column(Numeric(12, 6), default=0)
    status: Mapped[str] = mapped_column(String(20), default="reserved")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ModelEntry(Base):
    __tablename__ = "model_entries"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    provider: Mapped[str] = mapped_column(String(30))
    model_id: Mapped[str] = mapped_column(String(100))
    registry_revision: Mapped[str] = mapped_column(String(100))
    state: Mapped[str] = mapped_column(String(30), default="registered")
    context_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    capabilities: Mapped[dict] = mapped_column(JsonType, default=dict)
    price_revision: Mapped[str | None] = mapped_column(String(100), nullable=True)
    price_per_m_input: Mapped[float | None] = mapped_column(Numeric(12, 6), nullable=True)
    price_per_m_output: Mapped[float | None] = mapped_column(Numeric(12, 6), nullable=True)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    actor: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(100))
    target_ref: Mapped[str] = mapped_column(String(200))
    arguments_hash: Mapped[str] = mapped_column(String(64))
    policy_revision: Mapped[str] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ToolAction(Base):
    __tablename__ = "tool_actions"
    __table_args__ = (UniqueConstraint("effect_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    step_id: Mapped[str] = mapped_column(String(100))
    logical_action: Mapped[str] = mapped_column(String(100))
    effect_key: Mapped[str] = mapped_column(String(64))
    arguments_hash: Mapped[str] = mapped_column(String(64))
    policy_result: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(24), default="INTENDED")
    receipt: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MemoryFact(Base):
    __tablename__ = "memory_facts"
    __table_args__ = (
        Index("ix_memory_scope_revision", "tenant_id", "project_id", "source_revision", "status"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    repository_ref: Mapped[str] = mapped_column(Text)
    source_revision: Mapped[str] = mapped_column(String(64))
    fact_type: Mapped[str] = mapped_column(String(40))
    subject: Mapped[str] = mapped_column(String(300))
    statement: Mapped[str] = mapped_column(Text)
    source_refs: Mapped[list] = mapped_column(JsonType)
    verification_scope: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="proposed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
