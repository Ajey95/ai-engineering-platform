"""Coordinator test; transport, model metering and guest checks have separate tests."""

import hashlib
import io
import json
import tarfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from platform_app.api import download_patch, review_packet
from platform_app.db import Base
from platform_app.environment_manifest import EnvironmentManifest
from platform_app.general_patch import parse_general_patch
from platform_app.hosted_worker import HostedWorker
from platform_app.models import (
    ModelEntry,
    OutboxEvent,
    Project,
    Run,
    RunEvent,
    SandboxLease,
    Task,
    Tenant,
    ToolAction,
)
from platform_app.repository_archive import SourceArchive
from platform_app.sandbox_broker import SandboxSpec
from platform_app.sandbox_bundle import build_guest_bundle
from platform_app.sandbox_transport import GuestOutput


def _source() -> tuple[SourceArchive, bytes]:
    data = b"def answer():\n    return 0\n"
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        item = tarfile.TarInfo("app.py")
        item.size = len(data)
        archive.addfile(item, io.BytesIO(data))
    raw = output.getvalue()
    return SourceArchive("a" * 40, hashlib.sha256(raw).hexdigest(), raw, 1), data


def test_hosted_worker_joins_two_guest_generations_and_review_receipt(
    tmp_path, monkeypatch,
):
    source, data = _source()
    manifest = EnvironmentManifest.model_validate({
        "schema_version": "1.0", "language": "python", "python_version": "3.12",
        "services": [{
            "name": "app", "port": 8001, "health_path": "/health",
            "command": {"argv": ["python", "app.py"], "timeout_seconds": 30},
        }],
        "named_tests": {"unit": {
            "argv": ["python", "-c", "print('ok')"], "timeout_seconds": 30,
        }},
        "browser_scenario": {"steps": [{"action": "goto", "path": "/"}]},
    })
    engine = create_engine(f"sqlite:///{(tmp_path / 'worker.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add(Tenant(id="tenant-a", name="A"))
        db.add(Project(id="project-a", tenant_id="tenant-a", name="A"))
        db.add(Task(
            id="task-a", tenant_id="tenant-a", project_id="project-a",
            report="Submit fails", expected_behavior="success",
            actual_behavior="HTTP 500", created_by="alice",
        ))
        db.add(ModelEntry(
            id="model-a", provider="openai", model_id="verified-model",
            registry_revision="r1", state="enabled", capabilities={},
            validated_at=datetime.now(UTC), context_limit=10000,
            output_limit=1000, price_revision="p1",
            price_per_m_input=Decimal("1"), price_per_m_output=Decimal("1"),
        ))
        db.add(Run(
            id="run-a", tenant_id="tenant-a", project_id="project-a",
            task_id="task-a", created_by="alice", idempotency_key="k",
            request_hash="b" * 64, base_commit=source.commit,
            model_entry_id="model-a", state="QUEUED",
                config_snapshot={
                    "execution_profile": "hosted_vm_v1", "repair_paths": ["app.py"],
                    "repository_url": "https://github.com/example/repo",
                    "environment_manifest": manifest.model_dump(mode="json"),
            },
        ))
        db.add(OutboxEvent(
            id="dispatch-a", tenant_id="tenant-a", topic="run.dispatch",
            payload={"run_id": "run-a"},
        ))
        db.commit()
    import platform_app.hosted_worker as module

    monkeypatch.setattr(module, "qualification_for_pinned_run", lambda _: True)
    monkeypatch.setattr(module, "fetch_authorized_run_source", lambda *_: source)
    phases = []
    bundles = {}

    def launch(_db, _run_id, _worker_id, _fence, archive, _spec, _ec2, _s3,
               _bucket, _key, *, phase):
        phases.append(phase)
        bundles[phase] = archive
        return SimpleNamespace(id=f"lease-{phase}")

    def collect(_db, lease_id, _worker_id, _fence, _ec2, _s3, _bucket):
        phase = lease_id.removeprefix("lease-")
        tree = "c" * 64 if phase == "baseline" else candidate_tree[0]
        named = {"unit": {
            "status": "PASSED", "tested_tree_sha256": tree,
            "post_test_tree_sha256": tree,
        }}
        browser = {
            "status": "FAILED" if phase == "baseline" else "PASSED",
            "tested_tree_sha256": tree, "post_browser_tree_sha256": tree,
        }
        observation = {
            "status": "BASELINE_RECORDED" if phase == "baseline"
            else "CANDIDATE_RECORDED",
            "manifest_sha256": build_guest_bundle(source, manifest).manifest_sha256,
            "named_tests": named, "browser": browser,
        }
        if phase == "candidate":
            observation["baseline_tree_sha256"] = tree
        return GuestOutput({
            "phase": phase, "lease_id": lease_id,
            "source_sha256": bundles[phase].sha256, "guest_exit_code": 0,
            phase: observation,
        }, b"")

    proposal = parse_general_patch(json.dumps({
        "diagnosis": "Incorrect return value", "files": [{
            "path": "app.py", "base_sha256": hashlib.sha256(data).hexdigest(),
            "content": "def answer():\n    return 1\n",
        }],
    }), frozenset({"app.py"}))
    candidate_tree = [None]
    original_build = module.build_candidate_tree

    def build(*args):
        value = original_build(*args)
        candidate_tree[0] = value.tree_sha256
        return value

    monkeypatch.setattr(module, "stage_and_launch_baseline", launch)
    monkeypatch.setattr(module, "seal_and_collect_baseline", collect)
    monkeypatch.setattr(module, "revoke_sandbox", lambda *_: None)
    monkeypatch.setattr(module, "terminate_revoked_sandbox", lambda *_: True)
    monkeypatch.setattr(module, "request_general_patch", lambda *_args, **_kwargs:
                        SimpleNamespace(proposal=proposal))
    monkeypatch.setattr(module, "build_candidate_tree", build)
    spec = SandboxSpec(
        "ami-12345678", "m6i.large", "subnet-12345678",
        "sg-12345678", "/dev/xvda",
    )
    worker = HostedWorker(
        spec, object(), object(), "bucket", b"0" * 32, tmp_path, tmp_path,
        session_factory=factory, poll_seconds=0.01,
    )
    assert worker.process_event("dispatch-a") == "run-a"
    assert phases == ["baseline", "candidate"]
    assert bundles["baseline"].sha256 != bundles["candidate"].sha256
    with factory() as db:
        run = db.get(Run, "run-a")
        assert (run.state, run.verdict) == ("REVIEW_READY", "PASSED")
        assert db.get(OutboxEvent, "dispatch-a").status == "delivered"
        event = db.scalar(select(RunEvent).where(
            RunEvent.run_id == run.id,
            RunEvent.event_type == "verification.completed",
        ))
        assert event.payload["scope"] == "declared_guest_checks"
        assert event.payload["status"] == "SUPPORTED"
        path = tmp_path / event.payload["artifact_ref"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == event.payload["artifact_sha256"]
        import platform_app.api as api_module

        monkeypatch.setattr(
            api_module, "settings", lambda: SimpleNamespace(artifact_dir=str(tmp_path))
        )
        packet = review_packet("run-a", ("tenant-a", "alice"), db)
        assert packet["qualification_scope"] == "declared_guest_checks"
        assert packet["patch_hash"] == proposal.patch_sha256
        assert packet["autonomous_repair"] is False
        patch_response = download_patch("run-a", ("tenant-a", "alice"), db)
        assert b"return 1" in patch_response.body
        db.add(Run(
            id="run-b", tenant_id="tenant-a", project_id="project-a",
            task_id="task-a", created_by="alice", idempotency_key="k2",
            request_hash="d" * 64, base_commit=source.commit,
            model_entry_id="model-a", state="VERIFYING",
            lease_owner="dead-worker", lease_fence=1,
            lease_until=datetime.now(UTC) - timedelta(seconds=1),
            config_snapshot=run.config_snapshot,
        ))
        db.add(OutboxEvent(
            id="dispatch-b", tenant_id="tenant-a", topic="run.dispatch",
            payload={"run_id": "run-b"}, status="processing",
        ))
        db.commit()
    assert worker.recover_stale() == 1
    with factory() as db:
        assert db.get(Run, "run-b").state == "FAILED"
        assert db.get(OutboxEvent, "dispatch-b").status == "failed"
        db.add(Run(
            id="run-c", tenant_id="tenant-a", project_id="project-a",
            task_id="task-a", created_by="alice", idempotency_key="k3",
            request_hash="e" * 64, base_commit=source.commit,
            model_entry_id="model-a", state="PREPARING",
            lease_owner="dead-worker", lease_fence=1,
            lease_until=datetime.now(UTC) - timedelta(seconds=1),
            config_snapshot=run.config_snapshot,
        ))
        db.add(OutboxEvent(
            id="dispatch-c", tenant_id="tenant-a", topic="run.dispatch",
            payload={"run_id": "run-c"}, status="processing", attempts=1,
        ))
        db.commit()
    assert worker.recover_stale() == 1
    with factory() as db:
        recovered = db.get(Run, "run-c")
        assert recovered.state == "QUEUED"
        assert recovered.lease_fence == 2
        assert recovered.lease_owner is None
        assert db.get(OutboxEvent, "dispatch-c").status == "pending"
    assert worker.process_event("dispatch-c") == "run-c"
    with factory() as db:
        assert db.get(Run, "run-c").state == "REVIEW_READY"
        assert db.get(OutboxEvent, "dispatch-c").status == "delivered"
        db.add(Run(
            id="run-d", tenant_id="tenant-a", project_id="project-a",
            task_id="task-a", created_by="alice", idempotency_key="k4",
            request_hash="f" * 64, base_commit=source.commit,
            model_entry_id="model-a", state="PREPARING",
            lease_owner="dead-worker", lease_fence=1,
            lease_until=datetime.now(UTC) - timedelta(seconds=1),
            config_snapshot=run.config_snapshot,
        ))
        db.add(OutboxEvent(
            id="dispatch-d", tenant_id="tenant-a", topic="run.dispatch",
            payload={"run_id": "run-d"}, status="processing", attempts=1,
        ))
        db.add(ToolAction(
            tenant_id="tenant-a", run_id="run-d", step_id="pending-effect",
            logical_action="model.generate", effect_key="a" * 64,
            arguments_hash="b" * 64, policy_result="allowed", status="INTENDED",
        ))
        db.commit()
    assert worker.recover_stale() == 1
    with factory() as db:
        assert db.get(Run, "run-d").state == "FAILED"
        assert db.get(OutboxEvent, "dispatch-d").status == "failed"
        baseline_result = {
            "phase": "baseline", "lease_id": "lease-old-baseline",
            "source_sha256": build_guest_bundle(source, manifest).sha256,
            "guest_exit_code": 0,
            "baseline": {
                "status": "BASELINE_RECORDED",
                "manifest_sha256": build_guest_bundle(source, manifest).manifest_sha256,
                "named_tests": {"unit": {
                    "status": "PASSED", "tested_tree_sha256": "c" * 64,
                    "post_test_tree_sha256": "c" * 64,
                }},
                "browser": {
                    "status": "FAILED", "tested_tree_sha256": "c" * 64,
                    "post_browser_tree_sha256": "c" * 64,
                },
            },
        }
        db.add(Run(
            id="run-e", tenant_id="tenant-a", project_id="project-a",
            task_id="task-a", created_by="alice", idempotency_key="k5",
            request_hash="f" * 64, base_commit=source.commit,
            model_entry_id="model-a", state="INVESTIGATING",
            lease_owner="dead-worker", lease_fence=1,
            lease_until=datetime.now(UTC) - timedelta(seconds=1),
            config_snapshot=run.config_snapshot,
        ))
        db.add(SandboxLease(
            id="lease-old-baseline", tenant_id="tenant-a", project_id="project-a",
            run_id="run-e", generation=1, phase="baseline", lease_fence=1,
            client_token="f" * 64, state="terminated", image_id=spec.image_id,
            instance_type=spec.instance_type, subnet_id=spec.subnet_id,
            security_group_id=spec.security_group_id,
            root_device_name=spec.root_device_name, disk_gib=spec.disk_gib,
            reserved_seconds=1800, expires_at=datetime.now(UTC),
            source_sha256=baseline_result["source_sha256"],
            result_summary=baseline_result,
            result_sha256=hashlib.sha256(json.dumps(
                baseline_result, sort_keys=True, separators=(",", ":")
            ).encode()).hexdigest(),
            result_received_at=datetime.now(UTC), terminated_at=datetime.now(UTC),
        ))
        db.add(OutboxEvent(
            id="dispatch-e", tenant_id="tenant-a", topic="run.dispatch",
            payload={"run_id": "run-e"}, status="processing", attempts=1,
        ))
        db.commit()
    monkeypatch.setattr(module, "fetch_guest_output", lambda *_args, **_kwargs:
                        GuestOutput(baseline_result, b""))
    assert worker.recover_stale() == 1
    with factory() as db:
        assert db.get(Run, "run-e").state == "QUEUED"
        assert db.get(OutboxEvent, "dispatch-e").status == "pending"
    launched_before = len(phases)
    assert worker.process_event("dispatch-e") == "run-e"
    assert phases[launched_before:] == ["candidate"]
    with factory() as db:
        assert db.get(Run, "run-e").state == "REVIEW_READY"
        assert db.get(OutboxEvent, "dispatch-e").status == "delivered"
        db.add(Run(
            id="run-f", tenant_id="tenant-a", project_id="project-a",
            task_id="task-a", created_by="alice", idempotency_key="k6",
            request_hash="f" * 64, base_commit=source.commit,
            model_entry_id="model-a", state="REPRODUCING",
            lease_owner="dead-worker", lease_fence=1,
            lease_until=datetime.now(UTC) - timedelta(seconds=1),
            config_snapshot=run.config_snapshot,
        ))
        db.add(OutboxEvent(
            id="dispatch-f", tenant_id="tenant-a", topic="run.dispatch",
            payload={"run_id": "run-f"}, status="processing", attempts=1,
        ))
        db.commit()
    assert worker.recover_stale() == 1
    with factory() as db:
        assert db.get(Run, "run-f").state == "QUEUED"
        assert db.get(OutboxEvent, "dispatch-f").status == "pending"
        tamper_receipt = {**baseline_result, "lease_id": "lease-tampered"}
        db.add(Run(
            id="run-g", tenant_id="tenant-a", project_id="project-a",
            task_id="task-a", created_by="alice", idempotency_key="k7",
            request_hash="f" * 64, base_commit=source.commit,
            model_entry_id="model-a", state="INVESTIGATING",
            lease_owner="dead-worker", lease_fence=1,
            lease_until=datetime.now(UTC) - timedelta(seconds=1),
            config_snapshot=run.config_snapshot,
        ))
        db.add(SandboxLease(
            id="lease-tampered", tenant_id="tenant-a", project_id="project-a",
            run_id="run-g", generation=1, phase="baseline", lease_fence=1,
            client_token="e" * 64, state="terminated", image_id=spec.image_id,
            instance_type=spec.instance_type, subnet_id=spec.subnet_id,
            security_group_id=spec.security_group_id,
            root_device_name=spec.root_device_name, disk_gib=spec.disk_gib,
            reserved_seconds=1800, expires_at=datetime.now(UTC),
            source_sha256=tamper_receipt["source_sha256"],
            result_summary=tamper_receipt,
            result_sha256=hashlib.sha256(json.dumps(
                tamper_receipt, sort_keys=True, separators=(",", ":")
            ).encode()).hexdigest(),
            result_received_at=datetime.now(UTC), terminated_at=datetime.now(UTC),
        ))
        db.add(OutboxEvent(
            id="dispatch-g", tenant_id="tenant-a", topic="run.dispatch",
            payload={"run_id": "run-g"}, status="processing", attempts=1,
        ))
        db.commit()
    monkeypatch.setattr(module, "fetch_guest_output", lambda *_args, **_kwargs:
                        GuestOutput({**tamper_receipt, "guest_exit_code": 9}, b""))
    assert worker.recover_stale() == 1
    launched_before = len(phases)
    assert worker.process_event("dispatch-g") == "run-g"
    assert len(phases) == launched_before
    with factory() as db:
        assert db.get(Run, "run-g").state == "INCONCLUSIVE"
        assert db.get(OutboxEvent, "dispatch-g").status == "failed"
    engine.dispose()
