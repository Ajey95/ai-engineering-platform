"""Durable intent and receipt around a reconciled GitHub draft PR write."""

from __future__ import annotations

import hashlib
import json
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from platform_app.general_patch import GeneralPatchError, build_candidate_tree, parse_general_patch
from platform_app.github_publication import DraftPR, GitHubDraftPublisher
from platform_app.hosted_reporting import verified_hosted_artifact
from platform_app.models import AuditEvent, PublicationApproval, Run, RunEvent, ToolAction
from platform_app.publication import verify_draft_pr_approval
from platform_app.repository_fetch import RepositoryFetchError, fetch_authorized_run_source
from platform_app.safe_archive import UnsafeArchive, extract_regular_tar
from platform_app.service import ServiceError, canonical_hash

SessionFactory = Callable[[], Session]
STEP = "draft_pr_publication"


@contextmanager
def _hosted_candidate(db: Session, approval: PublicationApproval, artifact_root: Path):
    run = db.get(Run, approval.run_id)
    events = db.scalars(select(RunEvent).where(
        RunEvent.tenant_id == approval.tenant_id,
        RunEvent.run_id == run.id,
        RunEvent.event_type == "verification.completed",
    ).order_by(RunEvent.sequence.desc())).all()
    event = next((item for item in events if (
        item.payload or {}).get("scope") == "declared_guest_checks"
    ), None)
    if event is None:
        raise ServiceError("PUBLICATION_NOT_READY", "Hosted review receipt is missing", 409)
    verified_hosted_artifact(run, event, artifact_root)
    attempt = event.payload.get("attempt")
    if type(attempt) is not int or not 1 <= attempt <= 3:
        raise ServiceError("PUBLICATION_NOT_READY", "Hosted proposal step is invalid", 409)
    step = f"general-model-{attempt}"
    action = db.scalar(select(ToolAction).where(
        ToolAction.tenant_id == approval.tenant_id,
        ToolAction.run_id == run.id,
        ToolAction.step_id == step,
        ToolAction.logical_action == "model.generate",
        ToolAction.status == "COMPLETED",
    ))
    relative = f"{run.id}/model/{step}.json"
    if action is None or (action.receipt or {}).get("artifact_ref") != relative:
        raise ServiceError("PUBLICATION_NOT_READY", "Model artifact is not pinned", 409)
    model_path = artifact_root / run.id / "model" / f"{step}.json"
    try:
        if model_path.is_symlink() or model_path.stat().st_size > 300_000:
            raise ValueError("Model artifact exceeds policy")
        saved = json.loads(model_path.read_bytes())
        text = saved["text"]
        if hashlib.sha256(text.encode()).hexdigest() != action.receipt.get("output_sha256"):
            raise ValueError("Model output digest changed")
        proposal = parse_general_patch(
            text, frozenset(run.config_snapshot["repair_paths"])
        )
    except (OSError, KeyError, TypeError, ValueError, GeneralPatchError) as error:
        raise ServiceError("PUBLICATION_NOT_READY", "Model proposal changed", 409) from error
    if proposal.patch_sha256 != approval.patch_sha256:
        raise ServiceError("PUBLICATION_NOT_READY", "Approved patch changed", 409)
    work_root = artifact_root / "_trusted_work"
    work_root.mkdir(parents=True, exist_ok=True)
    try:
        source = fetch_authorized_run_source(db, run.id, work_root)
        candidate = build_candidate_tree(source, proposal, work_root)
    except (RepositoryFetchError, GeneralPatchError) as error:
        raise ServiceError(
            "PUBLICATION_NOT_READY", "Pinned candidate is unavailable", 409
        ) from error
    if (
        candidate.tree_sha256 != event.payload.get("candidate_tree_sha256")
        or candidate.source.sha256 != event.payload.get("candidate_source_sha256")
    ):
        raise ServiceError("PUBLICATION_NOT_READY", "Candidate differs from review", 409)
    with tempfile.TemporaryDirectory(prefix="aip-publication-", dir=work_root) as temporary:
        workspace = Path(temporary)
        try:
            extract_regular_tar(candidate.source.archive, workspace)
        except UnsafeArchive as error:
            raise ServiceError(
                "PUBLICATION_NOT_READY", "Candidate archive is invalid", 409
            ) from error
        yield workspace, {
            "scope": "declared_guest_checks",
            "candidate_tree_sha256": candidate.tree_sha256,
            "changed_files": [file.path for file in proposal.files],
            "base_sha256_by_path": {
                file.path: file.base_sha256 for file in proposal.files
            },
        }


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
    artifact_root = Path(artifact_root).resolve()
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
        run = db.get(Run, approval.run_id)
        if (run.config_snapshot or {}).get("execution_profile") == "hosted_vm_v1":
            with _hosted_candidate(db, approval, artifact_root) as (workspace, receipt):
                result = publisher.publish(
                    db, approval, workspace, receipt, title=title, body=body
                )
        else:
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
