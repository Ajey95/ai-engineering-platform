"""Durable intent and receipt around a reconciled GitHub draft PR write."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from platform_app.github_publication import DraftPR, GitHubDraftPublisher
from platform_app.models import AuditEvent, PublicationApproval, Run, ToolAction
from platform_app.publication import verify_draft_pr_approval
from platform_app.service import ServiceError, canonical_hash

SessionFactory = Callable[[], Session]
STEP = "draft_pr_publication"


def _existing_result(action: ToolAction) -> DraftPR:
    receipt = action.receipt or {}
    if action.status != "COMPLETED" or receipt.get("status") != "PUBLISHED":
        raise ServiceError("PUBLICATION_OUTCOME_UNKNOWN", "Publication needs reconciliation", 409)
    return DraftPR(
        number=receipt["pr_number"], url=receipt["pr_url"],
        branch=receipt["branch"], commit_sha=receipt["commit_sha"], reconciled=True,
    )


def publish_approved_run(
    session_factory: SessionFactory, approval_id: str, artifact_root: Path,
    publisher: GitHubDraftPublisher, *, title: str, body: str,
) -> DraftPR:
    if not 1 <= len(title) <= 200 or len(body) > 20_000:
        raise ServiceError("PUBLICATION_TEXT_INVALID", "Draft PR text exceeds policy", 400)
    request_hash = canonical_hash({
        "approval_id": approval_id, "title": title,
        "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
    })
    with session_factory() as db:
        approval = db.scalar(select(PublicationApproval).where(
            PublicationApproval.id == approval_id
        ).with_for_update())
        if approval is None:
            raise ServiceError("NOT_FOUND", "Publication approval not found", 404)
        action = db.scalar(select(ToolAction).where(
            ToolAction.tenant_id == approval.tenant_id,
            ToolAction.run_id == approval.run_id, ToolAction.step_id == STEP,
        ).with_for_update())
        if action is not None:
            if action.arguments_hash != request_hash:
                raise ServiceError(
                    "PUBLICATION_INTENT_CONFLICT", "Publication request changed", 409
                )
            if action.status == "COMPLETED":
                return _existing_result(action)
        else:
            verify_draft_pr_approval(db, approval)
            action = ToolAction(
                tenant_id=approval.tenant_id, run_id=approval.run_id, step_id=STEP,
                logical_action="repository.publish_draft_pr",
                effect_key=canonical_hash({"approval_id": approval.id, "action": STEP}),
                arguments_hash=request_hash, policy_result="approved", status="INTENDED",
            )
            db.add(action)
        db.commit()  # The intent is durable before the external write.

    with session_factory() as db:
        approval = db.scalar(select(PublicationApproval).where(
            PublicationApproval.id == approval_id
        ).with_for_update())
        action = db.scalar(select(ToolAction).where(
            ToolAction.tenant_id == approval.tenant_id,
            ToolAction.run_id == approval.run_id, ToolAction.step_id == STEP,
        ).with_for_update())
        if action.arguments_hash != request_hash:
            raise ServiceError("PUBLICATION_INTENT_CONFLICT", "Publication request changed", 409)
        if action.status == "COMPLETED":
            return _existing_result(action)
        verify_draft_pr_approval(db, approval)
        patch_action = db.scalar(select(ToolAction).where(
            ToolAction.tenant_id == approval.tenant_id,
            ToolAction.run_id == approval.run_id,
            ToolAction.step_id == "candidate_patch",
            ToolAction.status == "COMPLETED",
        ))
        if patch_action is None or not isinstance(patch_action.receipt, dict):
            raise ServiceError("PUBLICATION_NOT_READY", "Patch evidence is unavailable", 409)
        workspace = artifact_root / approval.run_id / "candidate" / "workspace"
        result = publisher.publish(
            db, approval, workspace, patch_action.receipt, title=title, body=body
        )
        action.status = "COMPLETED"
        action.receipt = {
            "status": "PUBLISHED", "approval_id": approval.id,
            "pr_number": result.number, "pr_url": result.url,
            "branch": result.branch, "commit_sha": result.commit_sha,
            "patch_sha256": approval.patch_sha256,
            "test_evidence_sha256": approval.test_evidence_sha256,
            "reconciled": result.reconciled,
        }
        approval.status = "consumed"
        run = db.get(Run, approval.run_id)
        db.add(AuditEvent(
            tenant_id=approval.tenant_id, actor=approval.actor,
            action="publication.draft_pr_published", target_ref=approval.id,
            arguments_hash=request_hash,
            policy_revision=run.config_snapshot.get("policy_version", "1.0"),
            outcome="published",
        ))
        db.commit()
        return result
