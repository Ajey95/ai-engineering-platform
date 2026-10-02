"""Approval and publication must bind the same hosted patch and VM evidence."""

import hashlib
import io
import json
import tarfile
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from platform_app.db import Base, utcnow
from platform_app.general_patch import build_candidate_tree, parse_general_patch
from platform_app.github_publication import DraftPR, GitHubDraftPublisher
from platform_app.models import (
    ModelEntry,
    OutboxEvent,
    Project,
    PublicationApproval,
    RepositoryConnection,
    Run,
    RunEvent,
    SandboxLease,
    Task,
    Tenant,
    ToolAction,
)
from platform_app.publication import approve_draft_pr, verify_draft_pr_approval
from platform_app.publication_dispatch import publish_approved_run
from platform_app.publication_queue import (
    _draft_text,
    dispatch_one_approved_draft,
    enqueue_approved_draft,
)
from platform_app.repository_archive import SourceArchive
from platform_app.service import ServiceError


def test_hosted_approval_rebuilds_exact_candidate_before_draft_write(tmp_path, monkeypatch):
    source_file = b"def answer():\n    return 0\n"
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        entry = tarfile.TarInfo("app.py")
        entry.size = len(source_file)
        archive.addfile(entry, io.BytesIO(source_file))
    raw_source = output.getvalue()
    source = SourceArchive(
        "a" * 40, hashlib.sha256(raw_source).hexdigest(), raw_source, 1
    )
    model_text = json.dumps({
        "diagnosis": "Correct the return value", "files": [{
            "path": "app.py", "base_sha256": hashlib.sha256(source_file).hexdigest(),
            "content": "def answer():\n    return 1\n",
        }],
    })
    proposal = parse_general_patch(model_text, frozenset({"app.py"}))
    candidate = build_candidate_tree(source, proposal, tmp_path)
    model_dir = tmp_path / "run-a" / "model"
    model_dir.mkdir(parents=True)
    (model_dir / "general-model-1.json").write_text(
        json.dumps({"text": model_text}), encoding="utf-8"
    )
    packet = {
        "scope": "declared_guest_checks", "attempt": 1,
        "status": "SUPPORTED", "reason": "declared_checks_improved",
        "patch_sha256": proposal.patch_sha256,
        "candidate_tree_sha256": candidate.tree_sha256,
        "candidate_source_sha256": candidate.source.sha256,
        "baseline_lease_id": "lease-baseline",
        "candidate_lease_id": "lease-candidate",
    }
    review_dir = tmp_path / "run-a" / "candidate"
    review_dir.mkdir()
    review_bytes = json.dumps({
        **packet, "diagnosis": proposal.diagnosis, "diff": candidate.diff,
    }, sort_keys=True).encode()
    review_path = review_dir / "guest-review.json"
    review_path.write_bytes(review_bytes)
    packet["artifact_ref"] = "run-a/candidate/guest-review.json"
    packet["artifact_sha256"] = hashlib.sha256(review_bytes).hexdigest()
    engine = create_engine(f"sqlite:///{(tmp_path / 'publication.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add(Tenant(id="tenant-a", name="A"))
        db.add(Project(id="project-a", tenant_id="tenant-a", name="A"))
        db.add(Task(
            id="task-a", tenant_id="tenant-a", project_id="project-a",
            report="Failure", expected_behavior="pass", actual_behavior="fail",
            created_by="alice",
        ))
        db.add(ModelEntry(
            id="model-a", provider="openai", model_id="model",
            registry_revision="r", state="enabled", capabilities={},
            context_limit=10000, output_limit=1000, price_revision="p",
            price_per_m_input=Decimal("1"), price_per_m_output=Decimal("1"),
        ))
        connection = RepositoryConnection(
            id="connection-a", tenant_id="tenant-a", project_id="project-a",
            provider="github", repository_ref="example/repo",
            credential_ref="secret://env/AIP_TEST_GITHUB_TOKEN", status="ready",
            created_by="alice",
        )
        db.add(connection)
        run = Run(
            id="run-a", tenant_id="tenant-a", project_id="project-a",
            task_id="task-a", created_by="alice", idempotency_key="k",
            request_hash="b" * 64, base_commit=source.commit,
            model_entry_id="model-a", state="COMPLETED", verdict="PASSED",
            config_snapshot={
                "policy_version": "1.0", "execution_profile": "hosted_vm_v1",
                "repair_paths": ["app.py"], "repository_connection_id": connection.id,
                "repository_url": "https://github.com/example/repo",
            },
        )
        db.add(run)
        db.flush()
        for generation, phase in ((1, "baseline"), (2, "candidate")):
            db.add(SandboxLease(
                id=f"lease-{phase}", tenant_id="tenant-a", project_id="project-a",
                run_id=run.id, generation=generation, phase=phase, lease_fence=1,
                client_token=str(generation) * 64, state="terminated",
                image_id="ami-12345678", instance_type="m6i.large",
                subnet_id="subnet-12345678", security_group_id="sg-12345678",
                root_device_name="/dev/xvda", disk_gib=40,
                expires_at=utcnow() + timedelta(minutes=5),
                result_sha256=str(generation) * 64, result_received_at=utcnow(),
                result_summary={"phase": phase},
            ))
        db.add(ToolAction(
            tenant_id="tenant-a", run_id=run.id, step_id="general-model-1",
            logical_action="model.generate", effect_key="model-effect",
            arguments_hash="a" * 64, policy_result="allowed", status="COMPLETED",
            receipt={
                "artifact_ref": "run-a/model/general-model-1.json",
                "output_sha256": hashlib.sha256(model_text.encode()).hexdigest(),
            },
        ))
        db.add(RunEvent(
            tenant_id="tenant-a", run_id=run.id, sequence=1,
            event_type="verification.completed", payload=packet,
        ))
        db.add(RunEvent(
            tenant_id="tenant-a", run_id=run.id, sequence=2,
            event_type="review.decision", payload={"decision": "accepted"},
        ))
        db.commit()
    import platform_app.publication as publication
    import platform_app.publication_dispatch as dispatch

    monkeypatch.setattr(
        publication, "settings", lambda: SimpleNamespace(artifact_dir=str(tmp_path))
    )
    monkeypatch.setattr(dispatch, "fetch_authorized_run_source", lambda *_: source)
    with factory() as db:
        approval = approve_draft_pr(
            db, tenant_id="tenant-a", run_id="run-a",
            connection_id="connection-a", base_branch="main", actor="alice",
        )
        enqueue_approved_draft(db, approval)
        db.commit()
        approval_id = approval.id
        review_path.write_bytes(b"changed")
        with pytest.raises(ServiceError, match="could not be verified"):
            verify_draft_pr_approval(db, approval)
        review_path.write_bytes(review_bytes)
        verify_draft_pr_approval(db, approval)
        stale = db.scalar(select(OutboxEvent).where(
            OutboxEvent.topic == "publication.dispatch"
        ))
        stale.status = "processing"
        stale.processing_token = "stale-worker"
        stale.processing_lease_until = utcnow() - timedelta(seconds=1)
        db.commit()

    class FakePublisher:
        def __init__(self, token):
            assert token == "fixture-key"

        def publish(self, db, approval, workspace, receipt, *, title, body):
            verify_draft_pr_approval(db, approval)
            files = GitHubDraftPublisher("fixture-key")._validated_files(
                approval, workspace, receipt
            )
            assert files == {"app.py": b"def answer():\n    return 1\n"}
            assert title.startswith("Proposed repair for run")
            assert approval.patch_sha256 in body
            return DraftPR(1, "https://github.com/example/repo/pull/1", "aip/run", "c" * 40, False)

    monkeypatch.setenv("AIP_TEST_GITHUB_TOKEN", "fixture-key")
    assert dispatch_one_approved_draft(
        factory, tmp_path, publisher_factory=FakePublisher
    ) is not None
    with factory() as db:
        approval = db.get(PublicationApproval, approval_id)
        title, body = _draft_text(approval)
        event = db.scalar(select(OutboxEvent).where(
            OutboxEvent.topic == "publication.dispatch"
        ))
        assert approval.status == "consumed", event.payload
        assert event.status == "delivered"
        assert event.processing_token is None
        with pytest.raises(ServiceError, match="already published"):
            approve_draft_pr(
                db, tenant_id="tenant-a", run_id="run-a",
                connection_id="connection-a", base_branch="main", actor="alice",
            )
    assert publish_approved_run(
        factory, approval_id, tmp_path, FakePublisher("fixture-key"),
        title=title, body=body,
    ).reconciled is True
    engine.dispose()
