from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectCreate(StrictModel):
    name: str = Field(min_length=2, max_length=200)
    repository_url: HttpUrl | None = None
    test_url: HttpUrl | None = None
    environment_manifest: dict[str, Any] = Field(default_factory=dict)


class ProjectRead(StrictModel):
    id: str
    name: str
    repository_url: str | None
    test_url: str | None
    created_at: datetime
    fixture_case_id: str | None = None


class RepositoryConnectionCreate(StrictModel):
    repository_url: str = Field(min_length=20, max_length=500)
    credential_ref: str | None = Field(default=None, max_length=300)


class RepositoryConnectionRead(StrictModel):
    id: str
    project_id: str
    provider: Literal["github"]
    repository_ref: str
    status: str
    readiness: Literal["credential_required", "verification_required", "ready", "disabled"]
    created_at: datetime
    checked_at: datetime | None


class TenantMembershipSet(StrictModel):
    role: Literal["owner", "member"]
    status: Literal["active", "disabled"] = "active"


class ProjectMembershipSet(StrictModel):
    role: Literal["maintainer", "contributor", "reviewer", "viewer"]
    status: Literal["active", "disabled"] = "active"


class TaskCreate(StrictModel):
    project_id: str
    report: str = Field(min_length=10, max_length=20000)
    expected_behavior: str = Field(min_length=1, max_length=10000)
    actual_behavior: str = Field(min_length=1, max_length=10000)


class TaskRead(StrictModel):
    id: str
    project_id: str
    report: str
    expected_behavior: str
    actual_behavior: str
    created_at: datetime


class RunCreate(StrictModel):
    base_commit: str = Field(pattern=r"^[0-9a-fA-F]{40}$")
    selected_model_entry: str
    mode: Literal["investigate_and_propose"] = "investigate_and_propose"
    reproduction: dict[str, Any] = Field(default_factory=dict)
    max_spend_usd: Decimal | None = Field(
        default=None, gt=0, max_digits=12, decimal_places=6
    )


class RunRead(StrictModel):
    id: str
    task_id: str
    project_id: str
    state: str
    verdict: str
    media_status: str
    base_commit: str
    model_entry_id: str
    spend_limit_usd: str
    cancel_requested: bool
    created_at: datetime
    updated_at: datetime


class ReviewDecisionCreate(StrictModel):
    decision: Literal["accepted", "rejected"]
    reason: str = Field(default="", max_length=2000)


class PublicationApprovalCreate(StrictModel):
    connection_id: str = Field(min_length=1, max_length=36)
    base_branch: str = Field(min_length=1, max_length=100)


class PublicationApprovalRead(StrictModel):
    id: str
    run_id: str
    action: Literal["draft_pr"]
    destination: str
    base_commit: str
    patch_sha256: str
    test_evidence_sha256: str
    status: str
    expires_at: datetime


class MemoryTransitionCreate(StrictModel):
    action: Literal["reject", "supersede", "expire"]
    reason: str = Field(min_length=5, max_length=2000)
    replacement_fact_id: str | None = Field(default=None, max_length=36)

    @model_validator(mode="after")
    def validate_replacement(self):
        if (self.action == "supersede") != (self.replacement_fact_id is not None):
            raise ValueError("A replacement fact is required only for supersession")
        return self


class ResumeInputCreate(StrictModel):
    input_text: str = Field(min_length=5, max_length=4000)


class ResumeApprovalCreate(StrictModel):
    reason: str = Field(min_length=8, max_length=2000)


class ResumeBudgetCreate(StrictModel):
    reason: str = Field(min_length=8, max_length=2000)
    new_spend_limit_usd: Decimal = Field(gt=0, max_digits=12, decimal_places=6)


class AlertResolutionCreate(StrictModel):
    reason: str = Field(min_length=8, max_length=2000)


class EventRead(StrictModel):
    schema_version: str = "1.0"
    event_id: str
    run_id: str
    sequence: int
    event_type: str
    timestamp: datetime
    trace_id: str
    payload: dict[str, Any]


class ErrorBody(StrictModel):
    code: str
    message: str
    request_id: str
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class ModelRegister(StrictModel):
    id: str = Field(pattern=r"^[a-z0-9_\-]{3,100}$")
    provider: Literal["openai", "anthropic", "google"]
    model_id: str = Field(min_length=2, max_length=100)
    registry_revision: str = Field(min_length=1, max_length=100)
    context_limit: int | None = Field(default=None, gt=0)
    output_limit: int | None = Field(default=None, gt=0)
    capabilities: dict[str, Any] = Field(default_factory=dict)
    price_revision: str | None = None
    price_per_m_input: float | None = Field(default=None, ge=0)
    price_per_m_output: float | None = Field(default=None, ge=0)
