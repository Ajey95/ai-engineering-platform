"""Local replay of the joined broker, S3, native provider and review path."""

import hashlib
import io
import json
import tarfile
import tempfile
from datetime import UTC, datetime
from decimal import Decimal

import boto3
import httpx
import pytest
from botocore.config import Config
from botocore.exceptions import ClientError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from platform_app.db import Base
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
)
from platform_app.providers import OpenAIResponses
from platform_app.repository_archive import SourceArchive
from platform_app.safe_archive import extract_regular_tar
from platform_app.sandbox_broker import SandboxSpec
from platform_app.sandbox_transport import SandboxObjectKeys
from platform_app.verifier import tree_hash


class FakeEC2:
    def __init__(self):
        self.instances = {}
        self.metadata_seals = 0

    def run_instances(self, **request):
        token = request["ClientToken"]
        if token not in self.instances:
            network = request["NetworkInterfaces"][0]
            self.instances[token] = {
                "InstanceId": f"i-{0x12345678 + len(self.instances):08x}",
                "ClientToken": token, "State": {"Name": "running"},
                "NetworkInterfaces": [{
                    "SubnetId": network["SubnetId"],
                    "Groups": [{"GroupId": network["Groups"][0]}],
                }],
                "MetadataOptions": {**request["MetadataOptions"], "State": "applied"},
            }
        return {"Instances": [self.instances[token]]}

    def describe_instances(self, **request):
        if "Filters" in request:
            found = self.instances.get(request["Filters"][0]["Values"][0])
        else:
            found = next((item for item in self.instances.values()
                          if item["InstanceId"] == request["InstanceIds"][0]), None)
        return {"Reservations": [{"Instances": [found]}] if found else []}

    def modify_instance_metadata_options(self, **request):
        self.metadata_seals += 1
        for item in self.instances.values():
            if item["InstanceId"] == request["InstanceId"]:
                item["MetadataOptions"].update(request)
                item["MetadataOptions"].pop("InstanceId")
                item["MetadataOptions"]["State"] = "applied"
        return {}

    def terminate_instances(self, **request):
        for item in self.instances.values():
            if item["InstanceId"] == request["InstanceIds"][0]:
                item["State"] = {"Name": "terminated"}
        return {}


class FakeS3:
    def __init__(self, factory, root, *, retry=False):
        self.factory, self.root = factory, root
        self.retry = retry
        self.objects = {}
        self.signer = boto3.client(
            "s3", region_name="us-east-1", aws_access_key_id="fixture-key",
            aws_secret_access_key="fixture-secret",
            config=Config(signature_version="s3v4"),
        )

    def generate_presigned_url(self, *args, **kwargs):
        return self.signer.generate_presigned_url(*args, **kwargs)

    def put_object(self, **request):
        key = request["Key"]
        if key in self.objects:
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        self.objects[key] = request["Body"]
        return {}

    def head_object(self, **request):
        import base64

        data = self.objects[request["Key"]]
        return {
            "ContentLength": len(data),
            "ChecksumSHA256": base64.b64encode(hashlib.sha256(data).digest()).decode(),
        }

    def get_object(self, **request):
        key = request["Key"]
        if key not in self.objects and key.endswith(("ready.json", "result.json")):
            lease_id = key.split("/")[-2]
            with self.factory() as db:
                lease = db.get(SandboxLease, lease_id)
                if lease is not None:
                    keys = SandboxObjectKeys.scoped(
                        lease.tenant_id, lease.project_id, lease.run_id, lease.id
                    )
                    if key == keys.ready:
                        self.objects[key] = json.dumps({
                            "version": 1, "lease_id": lease.id,
                            "fence": lease.lease_fence,
                            "source_sha256": lease.source_sha256,
                        }).encode()
                    elif key == keys.result and keys.go in self.objects:
                        self._produce_result(lease, keys)
        data = self.objects.get(key)
        if data is None:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"ContentLength": len(data), "Body": io.BytesIO(data)}

    def _produce_result(self, lease, keys):
        with tempfile.TemporaryDirectory(dir=self.root) as temporary:
            from pathlib import Path

            folder = Path(temporary)
            extract_regular_tar(self.objects[keys.source], folder)
            tree = tree_hash(folder / "workspace")
            plan = (folder / "control" / "manifest.json").read_bytes()
        evidence_file = io.BytesIO()
        with tarfile.open(fileobj=evidence_file, mode="w") as archive:
            log = b"HTTP 500\n" if lease.phase == "baseline" else b"HTTP 201\n"
            entry = tarfile.TarInfo("test-unit.log")
            entry.size = len(log)
            archive.addfile(entry, io.BytesIO(log))
            video = b"controlled browser recording"
            clip = tarfile.TarInfo("browser/video.webm")
            clip.size = len(video)
            archive.addfile(clip, io.BytesIO(video))
        evidence = evidence_file.getvalue()
        observation = {
            "status": "BASELINE_RECORDED" if lease.phase == "baseline"
            else "CANDIDATE_RECORDED",
            "manifest_sha256": hashlib.sha256(plan).hexdigest(),
            "baseline_tree_sha256": tree,
            "named_tests": {"unit": {
                "status": "PASSED", "output_file": "test-unit.log",
                "tested_tree_sha256": tree, "post_test_tree_sha256": tree,
            }},
            "browser": {
                "status": "FAILED" if (
                    lease.phase == "baseline"
                    or (self.retry and lease.generation == 2)
                ) else "PASSED",
                "tested_tree_sha256": tree, "post_browser_tree_sha256": tree,
                "steps": [], "page_errors": [],
                "recording": "video.webm",
            },
        }
        result = {
            "version": 1, "lease_id": lease.id, "fence": lease.lease_fence,
            "source_sha256": lease.source_sha256, "phase": lease.phase,
            "guest_exit_code": 0, lease.phase: observation,
            "evidence_sha256": hashlib.sha256(evidence).hexdigest(),
            "evidence_bytes": len(evidence),
        }
        self.objects[keys.evidence] = evidence
        self.objects[keys.result] = json.dumps(result).encode()


@pytest.mark.parametrize("retry", [False, True])
def test_hosted_pipeline_replays_full_control_path(tmp_path, monkeypatch, retry):
    source_file = b"def answer():\n    return 0\n"
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        entry = tarfile.TarInfo("app.py")
        entry.size = len(source_file)
        archive.addfile(entry, io.BytesIO(source_file))
    source_bytes = output.getvalue()
    source = SourceArchive(
        "a" * 40, hashlib.sha256(source_bytes).hexdigest(), source_bytes, 1
    )
    manifest = {
        "schema_version": "1.0", "language": "python", "python_version": "3.12",
        "services": [{
            "name": "app", "port": 8001, "health_path": "/health",
            "command": {"argv": ["python", "app.py"], "timeout_seconds": 30},
        }],
        "named_tests": {"unit": {
            "argv": ["python", "-c", "print('ok')"], "timeout_seconds": 30,
        }},
        "browser_scenario": {"steps": [{"action": "goto", "path": "/"}]},
    }
    engine = create_engine(f"sqlite:///{(tmp_path / 'pipeline.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add(Tenant(id="tenant-a", name="A"))
        db.add(Project(id="project-a", tenant_id="tenant-a", name="A"))
        db.add(Task(
            id="task-a", tenant_id="tenant-a", project_id="project-a",
            report="Submission fails", expected_behavior="201", actual_behavior="500",
            created_by="alice",
        ))
        db.add(ModelEntry(
            id="model-a", provider="openai", model_id="fixture-model",
            registry_revision="rev-a", state="enabled",
            capabilities={"controlled_provider_fixture": True},
            validated_at=datetime.now(UTC), context_limit=18000,
            output_limit=4000, price_revision="price-a",
            price_per_m_input=Decimal("1"), price_per_m_output=Decimal("2"),
        ))
        db.add(Run(
            id="run-a", tenant_id="tenant-a", project_id="project-a",
            task_id="task-a", created_by="alice", idempotency_key="key-a",
            request_hash="b" * 64, base_commit=source.commit,
            model_entry_id="model-a", state="QUEUED",
            config_snapshot={
                "policy_version": "1.0", "execution_profile": "hosted_vm_v1",
                "repair_paths": ["app.py"], "environment_manifest": manifest,
                "model_registry_revision": "rev-a", "model_price_revision": "price-a",
                "model_context_limit": 18000, "model_output_limit": 4000,
                "model_price_per_m_input": "1.000000",
                "model_price_per_m_output": "2.000000",
                "max_tool_calls": 20, "max_model_calls": 3, "spend_limit_usd": 5,
                "max_patch_attempts": 2 if retry else 1,
            },
        ))
        db.add(OutboxEvent(
            id="dispatch-a", tenant_id="tenant-a", topic="run.dispatch",
            payload={"run_id": "run-a"},
        ))
        db.commit()
    import platform_app.hosted_worker as module

    monkeypatch.setattr(module, "qualification_current", lambda _: True)
    monkeypatch.setattr(module, "fetch_authorized_run_source", lambda *_: source)
    calls = []

    def respond(request):
        calls.append(request)
        patch = json.dumps({
            "diagnosis": "Handler returns the wrong value", "files": [{
                "path": "app.py", "base_sha256": hashlib.sha256(source_file).hexdigest(),
                "content": f"def answer():\n    return {len(calls)}\n",
            }],
        })
        return httpx.Response(200, json={
            "model": "fixture-model", "status": "completed",
            "output": [{"type": "message", "content": [
                {"type": "output_text", "text": patch},
            ]}],
            "usage": {"input_tokens": 300, "output_tokens": 100},
        })

    provider = OpenAIResponses(
        "fixture-key", client=httpx.Client(transport=httpx.MockTransport(respond))
    )
    ec2, s3 = FakeEC2(), FakeS3(factory, tmp_path, retry=retry)
    worker = HostedWorker(
        SandboxSpec(
            "ami-12345678", "m6i.large", "subnet-12345678",
            "sg-12345678", "/dev/xvda",
        ), ec2, s3, "private-bucket", b"0" * 32,
        tmp_path, tmp_path, session_factory=factory, provider=provider,
        poll_seconds=0.01,
    )
    assert worker.process_event("dispatch-a") == "run-a"
    with factory() as db:
        run = db.get(Run, "run-a")
        assert (run.state, run.verdict) == ("REVIEW_READY", "PASSED")
        assert db.get(OutboxEvent, "dispatch-a").status == "delivered"
        media_jobs = db.scalars(select(OutboxEvent).where(
            OutboxEvent.topic == "media.transcode",
        )).all()
        assert {job.payload["label"] for job in media_jobs} == {"baseline", "candidate"}
        assert run.media_status == "PROCESSING"
        leases = db.scalars(select(SandboxLease).order_by(SandboxLease.generation)).all()
        assert [lease.phase for lease in leases] == (
            ["baseline", "candidate", "candidate"] if retry
            else ["baseline", "candidate"]
        )
        assert all(lease.state == "terminated" for lease in leases)
        assert len({lease.instance_id for lease in leases}) == (3 if retry else 2)
        event = db.scalar(select(RunEvent).where(
            RunEvent.run_id == run.id,
            RunEvent.event_type == "verification.completed",
        ))
        assert event.payload["status"] == "SUPPORTED"
        attempts = db.scalars(select(RunEvent).where(
            RunEvent.run_id == run.id, RunEvent.event_type == "verification.attempt",
        )).all()
        assert len(attempts) == (2 if retry else 1)
    assert len(calls) == (2 if retry else 1)
    assert b"HTTP 500" in calls[0].content
    if retry:
        assert b"candidate_declared_check_failed" in calls[1].content
    assert ec2.metadata_seals == (3 if retry else 2)
    engine.dispose()
