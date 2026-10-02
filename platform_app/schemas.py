from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


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


class RunRead(StrictModel):
    id: str
    task_id: str
    project_id: str
    state: str
    verdict: str
    media_status: str
    base_commit: str
    model_entry_id: str
    cancel_requested: bool
    created_at: datetime
    updated_at: datetime


class ReviewDecisionCreate(StrictModel):
    decision: Literal["accepted", "rejected"]
    reason: str = Field(default="", max_length=2000)


class ResumeInputCreate(StrictModel):
    input_text: str = Field(min_length=5, max_length=4000)


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
