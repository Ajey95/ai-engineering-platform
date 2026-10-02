import hashlib
import io
import json
import tarfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from platform_app.db import Base, utcnow
from platform_app.general_agent import request_general_patch
from platform_app.models import (
    BudgetEntry,
    ModelEntry,
    Project,
    Run,
    SandboxLease,
    Task,
    Tenant,
    ToolAction,
)
from platform_app.providers import OpenAIResponses
from platform_app.repository_archive import SourceArchive
from platform_app.run_ledger import claim_run
from platform_app.sandbox_transport import GuestOutput
from platform_app.service import ServiceError


def _source():
    data = b"def answer():\n    return 0\n"
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        entry = tarfile.TarInfo("app.py")
        entry.size = len(data)
        archive.addfile(entry, io.BytesIO(data))
    raw = output.getvalue()
    return SourceArchive("a" * 40, hashlib.sha256(raw).hexdigest(), raw, 1), data


def _baseline():
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        data = b"HTTP 500 while submitting form\n"
        entry = tarfile.TarInfo("test-unit.log")
        entry.size = len(data)
        archive.addfile(entry, io.BytesIO(data))
    return GuestOutput({
        "version": 1, "lease_id": "lease-a", "phase": "baseline",
        "source_sha256": "s" * 64, "guest_exit_code": 0,
        "baseline": {
            "status": "BASELINE_RECORDED", "manifest_sha256": "m" * 64,
            "named_tests": {"unit": {
                "status": "PASSED", "output_file": "test-unit.log",
            }},
            "browser": {"status": "FAILED", "steps": [], "page_errors": ["HTTP 500"]},
        },
    }, output.getvalue())


def test_general_model_patch_reserves_settles_and_replays(tmp_path):
    source, before = _source()
    baseline = _baseline()
    patch = json.dumps({
        "diagnosis": "The handler returns the wrong value",
        "files": [{
            "path": "app.py", "base_sha256": hashlib.sha256(before).hexdigest(),
            "content": "def answer():\n    return 1\n",
        }],
    })
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={
            "model": "live-model", "status": "completed",
            "output": [{"type": "message", "content": [
                {"type": "output_text", "text": patch},
            ]}],
            "usage": {"input_tokens": 300, "output_tokens": 100},
        })

    adapter = OpenAIResponses(
        "fixture-key", client=httpx.Client(transport=httpx.MockTransport(respond))
    )
    engine = create_engine(f"sqlite:///{(tmp_path / 'general.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add(Tenant(id="tenant-a", name="A"))
        db.add(Project(id="project-a", tenant_id="tenant-a", name="A"))
        db.add(Task(
            id="task-a", tenant_id="tenant-a", project_id="project-a",
            report="Form submit fails", expected_behavior="success",
            actual_behavior="HTTP 500", created_by="alice",
        ))
        db.add(ModelEntry(
            id="model-a", provider="openai", model_id="live-model",
            registry_revision="rev-a", state="enabled",
            capabilities={"controlled_provider_fixture": True},
            validated_at=datetime.now(UTC), context_limit=18000,
            output_limit=4000, price_revision="price-a",
            price_per_m_input=Decimal("1"), price_per_m_output=Decimal("2"),
        ))
        db.add(Run(
            id="run-a", tenant_id="tenant-a", project_id="project-a",
            task_id="task-a", created_by="alice", idempotency_key="key-a",
            request_hash="a" * 64, base_commit=source.commit,
            model_entry_id="model-a", state="INVESTIGATING",
            config_snapshot={
                "policy_version": "1.0", "execution_profile": "hosted_vm_v1",
                "model_registry_revision": "rev-a", "model_price_revision": "price-a",
                "model_context_limit": 18000, "model_output_limit": 4000,
                "model_price_per_m_input": "1.000000",
                "model_price_per_m_output": "2.000000",
                "max_tool_calls": 20, "max_model_calls": 3, "spend_limit_usd": 5,
            },
        ))
        db.commit()
        _, fence = claim_run(db, "run-a", "worker-a")
        db.add(SandboxLease(
            id="lease-a", tenant_id="tenant-a", project_id="project-a",
            run_id="run-a", generation=1, phase="baseline", lease_fence=fence,
            client_token="a" * 64, state="terminated", image_id="ami-12345678",
            instance_type="m6i.large", subnet_id="subnet-12345678",
            security_group_id="sg-12345678", root_device_name="/dev/xvda",
            disk_gib=40, expires_at=utcnow() + timedelta(minutes=5),
            result_sha256="b" * 64, result_received_at=utcnow(),
            result_summary=baseline.result,
        ))
        db.commit()
    result = request_general_patch(
        factory, "run-a", "worker-a", fence, source, baseline,
        frozenset({"app.py"}), tmp_path, provider=adapter,
    )
    assert result.proposal.files[0].path == "app.py"
    assert b"HTTP 500 while submitting" in calls[0].content
    replay = request_general_patch(
        factory, "run-a", "worker-a", fence, source, baseline,
        frozenset({"app.py"}), tmp_path, provider=adapter,
    )
    assert replay == result and len(calls) == 1
    feedback = ({
        "status": "FAILED", "reason": "candidate_declared_check_failed",
        "candidate_checks": {"browser": "FAILED"},
        "patch_sha256": result.proposal.patch_sha256,
        "candidate_tree_sha256": "c" * 64,
    },)
    second = request_general_patch(
        factory, "run-a", "worker-a", fence, source, baseline,
        frozenset({"app.py"}), tmp_path, provider=adapter,
        attempt=2, feedback=feedback,
    )
    assert second.artifact_ref.endswith("general-model-2.json")
    assert len(calls) == 2 and b"candidate_declared_check_failed" in calls[1].content
    with factory() as db:
        db.get(Task, "task-a").report = "Changed report after model call"
        db.commit()
    with pytest.raises(ServiceError, match="context changed"):
        request_general_patch(
            factory, "run-a", "worker-a", fence, source, baseline,
            frozenset({"app.py"}), tmp_path, provider=adapter,
        )
    with factory() as db:
        assert db.scalar(select(ToolAction).where(
            ToolAction.step_id == "general-model-1"
        )).status == "COMPLETED"
        assert db.scalar(select(BudgetEntry).where(
            BudgetEntry.category == "call:general-model-1"
        )).status == "settled"
    engine.dispose()
