from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
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
    __table_args__ = (
        CheckConstraint("daily_export_cap_bytes > 0", name="ck_tenant_export_cap_positive"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(24), default="active")
    policy_revision: Mapped[str] = mapped_column(String(64), default="1.0")
    daily_inference_cap_usd: Mapped[float] = mapped_column(Numeric(12, 6), default=50)
    monthly_inference_cap_usd: Mapped[float] = mapped_column(Numeric(12, 6), default=500)
    max_concurrent_runs: Mapped[int] = mapped_column(Integer, default=4)
    daily_export_cap_bytes: Mapped[int] = mapped_column(Integer, default=100_000_000)
    model_routing_policy: Mapped[dict] = mapped_column(JsonType, default=dict)
    plugin_allowlist: Mapped[list] = mapped_column(JsonType, default=list)


class TenantMembership(Base):
    __tablename__ = "tenant_memberships"
    __table_args__ = (
        UniqueConstraint("tenant_id", "subject"),
        CheckConstraint("role IN ('owner', 'member')", name="ck_tenant_membership_role"),
        CheckConstraint("status IN ('active', 'disabled')", name="ck_tenant_membership_status"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    subject: Mapped[str] = mapped_column(String(200), index=True)
    role: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(24), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BrowserSession(Base):
    __tablename__ = "browser_sessions"
    __table_args__ = (
        Index("ix_browser_sessions_tenant_subject", "tenant_id", "subject"),
        Index("ix_browser_sessions_expires_at", "expires_at"),
    )
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    subject: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


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


class ProjectMembership(Base):
    __tablename__ = "project_memberships"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "project_id"], ["projects.tenant_id", "projects.id"]),
        UniqueConstraint("tenant_id", "project_id", "subject"),
        CheckConstraint(
            "role IN ('maintainer', 'contributor', 'reviewer', 'viewer')",
            name="ck_project_membership_role",
        ),
        CheckConstraint("status IN ('active', 'disabled')", name="ck_project_membership_status"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    subject: Mapped[str] = mapped_column(String(200), index=True)
    role: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(24), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RepositoryConnection(Base):
    __tablename__ = "repository_connections"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "project_id"], ["projects.tenant_id", "projects.id"],
            name="fk_repository_connections_project_scope",
        ),
        UniqueConstraint(
            "tenant_id", "project_id", "provider", "repository_ref",
            name="uq_repository_connections_scope_ref",
        ),
        UniqueConstraint(
            "tenant_id", "project_id", "id", name="uq_repository_connections_scope_id"
        ),
        CheckConstraint("provider = 'github'", name="ck_repository_connection_provider"),
        CheckConstraint(
            "status IN ('unverified', 'ready', 'disabled')",
            name="ck_repository_connection_status",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    provider: Mapped[str] = mapped_column(String(20))
    repository_ref: Mapped[str] = mapped_column(String(220))
    credential_ref: Mapped[str | None] = mapped_column(String(300), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="unverified")
    created_by: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PublicationApproval(Base):
    __tablename__ = "publication_approvals"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "project_id", "run_id"],
            ["runs.tenant_id", "runs.project_id", "runs.id"],
            name="fk_publication_approvals_run_scope",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "project_id"], ["projects.tenant_id", "projects.id"],
            name="fk_publication_approvals_project_scope",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "project_id", "connection_id"],
            [
                "repository_connections.tenant_id", "repository_connections.project_id",
                "repository_connections.id",
            ],
            name="fk_publication_approvals_connection_scope",
        ),
        UniqueConstraint(
            "tenant_id", "run_id", "action", name="uq_publication_approval_run_action"
        ),
        CheckConstraint("action = 'draft_pr'", name="ck_publication_approval_action"),
        CheckConstraint(
            "status IN ('approved', 'revoked', 'consumed')",
            name="ck_publication_approval_status",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    connection_id: Mapped[str] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(24), default="draft_pr")
    destination: Mapped[str] = mapped_column(String(300))
    base_commit: Mapped[str] = mapped_column(String(40))
    patch_sha256: Mapped[str] = mapped_column(String(64))
    test_evidence_sha256: Mapped[str] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(24), default="approved")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "project_id", "id", name="uq_tasks_scope_id"),
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_tasks_project_scope",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
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
        UniqueConstraint("tenant_id", "id", name="uq_runs_tenant_id"),
        UniqueConstraint("tenant_id", "project_id", "id", name="uq_runs_scope_id"),
        ForeignKeyConstraint(
            ["tenant_id", "project_id", "task_id"],
            ["tasks.tenant_id", "tasks.project_id", "tasks.id"],
            name="fk_runs_task_scope",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_runs_project_scope",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    task_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    created_by: Mapped[str] = mapped_column(String(200))
    idempotency_key: Mapped[str] = mapped_column(String(200))
    request_hash: Mapped[str] = mapped_column(String(64))
    base_commit: Mapped[str] = mapped_column(String(64))
    model_entry_id: Mapped[str] = mapped_column(String(100))
    state: Mapped[str] = mapped_column(String(40), default="QUEUED")
    resume_target: Mapped[str | None] = mapped_column(String(40), nullable=True)
    resume_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    resume_input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
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
    __table_args__ = (
        UniqueConstraint("run_id", "sequence"),
        ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            ["runs.tenant_id", "runs.id"],
            name="fk_run_events_run_scope",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict] = mapped_column(JsonType, default=dict)
    trace_id: Mapped[str] = mapped_column(String(64), default=new_id)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RecordingDeletion(Base):
    __tablename__ = "recording_deletions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "run_id", "label", name="uq_recording_deletion_scope"),
        ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            ["runs.tenant_id", "runs.id"],
            name="fk_recording_deletions_run_scope",
        ),
        CheckConstraint("label IN ('baseline', 'candidate')", name="ck_recording_deletion_label"),
        CheckConstraint(
            "status IN ('pending', 'complete', 'failed')",
            name="ck_recording_deletion_status",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    label: Mapped[str] = mapped_column(String(16))
    actor: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(24), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PrivateMediaPublication(Base):
    __tablename__ = "private_media_publications"
    __table_args__ = (
        UniqueConstraint("tenant_id", "run_id", "label", name="uq_private_media_scope"),
        ForeignKeyConstraint(
            ["tenant_id", "project_id", "run_id"],
            ["runs.tenant_id", "runs.project_id", "runs.id"],
            name="fk_private_media_run_scope",
        ),
        CheckConstraint("label IN ('baseline', 'candidate')", name="ck_private_media_label"),
        CheckConstraint("status IN ('ready', 'deleted')", name="ck_private_media_status"),
        CheckConstraint("object_count > 0 AND byte_count > 0", name="ck_private_media_nonempty"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(String(36))
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    label: Mapped[str] = mapped_column(String(16))
    effect_hash: Mapped[str] = mapped_column(String(64))
    manifest_sha256: Mapped[str] = mapped_column(String(64))
    object_count: Mapped[int] = mapped_column(Integer)
    byte_count: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="ready")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SandboxLease(Base):
    __tablename__ = "sandbox_leases"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "project_id", "run_id"],
            ["runs.tenant_id", "runs.project_id", "runs.id"],
            name="fk_sandbox_lease_run_scope",
        ),
        UniqueConstraint("tenant_id", "run_id", "generation", name="uq_sandbox_generation"),
        UniqueConstraint("client_token", name="uq_sandbox_client_token"),
        UniqueConstraint("instance_id", name="uq_sandbox_instance_id"),
        CheckConstraint("generation > 0", name="ck_sandbox_generation_positive"),
        CheckConstraint("disk_gib BETWEEN 8 AND 100", name="ck_sandbox_disk_bounds"),
        CheckConstraint(
            "state IN ('intended', 'provisioned', 'revoked', 'terminating', 'terminated')",
            name="ck_sandbox_state",
        ),
        Index("ix_sandbox_leases_expiry", "state", "expires_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(String(36))
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    generation: Mapped[int] = mapped_column(Integer)
    lease_fence: Mapped[int] = mapped_column(Integer)
    client_token: Mapped[str] = mapped_column(String(64))
    instance_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    state: Mapped[str] = mapped_column(String(20), default="intended")
    image_id: Mapped[str] = mapped_column(String(32))
    instance_type: Mapped[str] = mapped_column(String(32))
    subnet_id: Mapped[str] = mapped_column(String(32))
    security_group_id: Mapped[str] = mapped_column(String(32))
    root_device_name: Mapped[str] = mapped_column(String(32))
    disk_gib: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", name="fk_outbox_events_tenant"), index=True
    )
    topic: Mapped[str] = mapped_column(String(80), index=True)
    payload: Mapped[dict] = mapped_column(JsonType)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BudgetEntry(Base):
    __tablename__ = "budget_ledger"
    __table_args__ = (
        Index("ix_budget_ledger_tenant_category_created", "tenant_id", "category", "created_at"),
        ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            ["runs.tenant_id", "runs.id"],
            name="fk_budget_ledger_run_scope",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    category: Mapped[str] = mapped_column(String(50))
    reserved_usd: Mapped[float] = mapped_column(Numeric(12, 6))
    actual_usd: Mapped[float] = mapped_column(Numeric(12, 6), default=0)
    status: Mapped[str] = mapped_column(String(20), default="reserved")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ExportCharge(Base):
    __tablename__ = "export_ledger"
    __table_args__ = (
        Index("ix_export_ledger_tenant_created", "tenant_id", "created_at"),
        CheckConstraint("bytes_count > 0", name="ck_export_ledger_bytes_positive"),
        ForeignKeyConstraint(
            ["tenant_id", "run_id"], ["runs.tenant_id", "runs.id"],
            name="fk_export_ledger_run_scope",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    actor: Mapped[str] = mapped_column(String(200))
    bytes_count: Mapped[int] = mapped_column(Integer)
    archive_sha256: Mapped[str] = mapped_column(String(64))
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


class ModelRoutingEvidence(Base):
    __tablename__ = "model_routing_evidence"
    __table_args__ = (
        UniqueConstraint(
            "model_entry_id", "registry_revision", "task_class", "suite_revision",
            name="uq_model_routing_evidence_revision",
        ),
        CheckConstraint("sample_count >= 0", name="ck_model_routing_samples"),
        CheckConstraint(
            "success_rate >= 0 AND success_rate <= 1", name="ck_model_routing_success_rate"
        ),
        CheckConstraint("p95_latency_ms >= 0", name="ck_model_routing_latency"),
        CheckConstraint("mean_cost_usd >= 0", name="ck_model_routing_cost"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    model_entry_id: Mapped[str] = mapped_column(ForeignKey("model_entries.id"), index=True)
    registry_revision: Mapped[str] = mapped_column(String(100))
    task_class: Mapped[str] = mapped_column(String(80))
    suite_revision: Mapped[str] = mapped_column(String(100))
    sample_count: Mapped[int] = mapped_column(Integer)
    success_rate: Mapped[float] = mapped_column(Numeric(5, 4))
    p95_latency_ms: Mapped[int] = mapped_column(Integer)
    mean_cost_usd: Mapped[float] = mapped_column(Numeric(12, 6))
    available: Mapped[bool] = mapped_column(Boolean, default=False)
    source_sha256: Mapped[str] = mapped_column(String(64))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PluginEntry(Base):
    __tablename__ = "plugin_entries"
    __table_args__ = (UniqueConstraint("plugin_id", "version", name="uq_plugin_version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    plugin_id: Mapped[str] = mapped_column(String(100), index=True)
    version: Mapped[str] = mapped_column(String(60))
    manifest: Mapped[dict] = mapped_column(JsonType)
    manifest_sha256: Mapped[str] = mapped_column(String(64))
    artifact_sha256: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(24), default="registered")
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PluginRegistryEvent(Base):
    __tablename__ = "plugin_registry_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    plugin_entry_id: Mapped[str] = mapped_column(ForeignKey("plugin_entries.id"), index=True)
    actor: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(40))
    outcome: Mapped[str] = mapped_column(String(40))
    manifest_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", name="fk_audit_events_tenant"), index=True
    )
    actor: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(100))
    target_ref: Mapped[str] = mapped_column(String(200))
    arguments_hash: Mapped[str] = mapped_column(String(64))
    policy_revision: Mapped[str] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OperationalAlert(Base):
    __tablename__ = "operational_alerts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "alert_id", name="uq_operational_alert_tenant_kind"),
        UniqueConstraint("tenant_id", "id", name="uq_operational_alert_scope_id"),
        CheckConstraint(
            "state IN ('observing', 'firing', 'resolved')",
            name="ck_operational_alert_state",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    alert_id: Mapped[str] = mapped_column(String(100))
    state: Mapped[str] = mapped_column(String(20))
    generation: Mapped[int] = mapped_column(Integer, default=1)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_cursor: Mapped[str | None] = mapped_column(String(36), nullable=True)
    evidence: Mapped[dict] = mapped_column(JsonType, default=dict)


class OperationalAlertEvent(Base):
    __tablename__ = "operational_alert_events"
    __table_args__ = (
        Index(
            "ix_operational_alert_delivery_queue",
            "notification_status", "notification_next_attempt_at",
        ),
        CheckConstraint(
            "notification_status IN ('not_required', 'pending', 'delivered')",
            name="ck_operational_alert_event_notification_status",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "alert_id"],
            ["operational_alerts.tenant_id", "operational_alerts.id"],
            name="fk_operational_alert_events_scope",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    alert_id: Mapped[str] = mapped_column(String(36), index=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    generation: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(20))
    actor: Mapped[str] = mapped_column(String(200))
    reason: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    notification_status: Mapped[str] = mapped_column(String(20), default="not_required")
    notification_attempts: Mapped[int] = mapped_column(Integer, default=0)
    notification_next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    notification_delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    notification_last_error: Mapped[str | None] = mapped_column(String(40), nullable=True)


class ModelRegistryEvent(Base):
    __tablename__ = "model_registry_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    model_entry_id: Mapped[str] = mapped_column(ForeignKey("model_entries.id"), index=True)
    actor: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(40))
    registry_revision: Mapped[str] = mapped_column(String(100))
    metadata_hash: Mapped[str] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ToolAction(Base):
    __tablename__ = "tool_actions"
    __table_args__ = (
        UniqueConstraint("effect_key"),
        ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            ["runs.tenant_id", "runs.id"],
            name="fk_tool_actions_run_scope",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
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
        UniqueConstraint("tenant_id", "project_id", "id", name="uq_memory_facts_scope_id"),
        CheckConstraint(
            "status IN ('proposed', 'verified', 'rejected', 'superseded', 'expired', 'deleted')",
            name="ck_memory_fact_status",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_memory_facts_project_scope",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
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


class MemoryFactEvent(Base):
    __tablename__ = "memory_fact_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "project_id", "fact_id"],
            ["memory_facts.tenant_id", "memory_facts.project_id", "memory_facts.id"],
            name="fk_memory_fact_events_fact_scope",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    fact_id: Mapped[str] = mapped_column(String(36), index=True)
    previous_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    status: Mapped[str] = mapped_column(String(24))
    actor: Mapped[str] = mapped_column(String(200))
    reason: Mapped[str] = mapped_column(Text)
    evidence_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
