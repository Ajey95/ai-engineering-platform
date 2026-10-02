import hashlib
import io
import json
import tarfile
from datetime import timedelta

import boto3
import pytest
from botocore.config import Config
from botocore.exceptions import ClientError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from platform_app.db import Base, utcnow
from platform_app.environment_manifest import EnvironmentManifest
from platform_app.hosted_baseline import seal_and_collect_baseline, stage_and_launch_baseline
from platform_app.models import OutboxEvent, Project, Run, RunEvent, SandboxLease, Task, Tenant
from platform_app.repository_archive import SourceArchive
from platform_app.run_ledger import claim_run, transition
from platform_app.sandbox_bootstrap_crypto import (
    SandboxBootstrapError,
    decrypt_user_data,
    encrypt_user_data,
)
from platform_app.sandbox_broker import (
    SandboxSpec,
    _launch_request,
    assert_sandbox_callback,
    attach_guest_bootstrap,
    due_sandbox_lease_ids,
    launch_reserved_sandbox,
    reserve_sandbox,
    revoke_sandbox,
    seal_bootstrapping_sandbox,
    terminate_revoked_sandbox,
)
from platform_app.sandbox_transport import SandboxObjectKeys
from platform_app.service import ServiceError, request_cancel


class FakeEC2:
    def __init__(self, *, public_ip: bool = False):
        self.instances = {}
        self.calls = []
        self.terminations = []
        self.public_ip = public_ip
        self.metadata_changes = []

    def run_instances(self, **args):
        self.calls.append(args)
        token = args["ClientToken"]
        if token not in self.instances:
            interface = args["NetworkInterfaces"][0]
            instance = {
                "InstanceId": "i-12345678", "ClientToken": token,
                "State": {"Name": "running"},
                "NetworkInterfaces": [{
                    "SubnetId": interface["SubnetId"],
                    "Groups": [{"GroupId": interface["Groups"][0]}],
                }],
                "MetadataOptions": {**args["MetadataOptions"], "State": "applied"},
            }
            if self.public_ip:
                instance["PublicIpAddress"] = "203.0.113.5"
                instance["NetworkInterfaces"][0]["Association"] = {
                    "PublicIp": "203.0.113.5"
                }
            self.instances[token] = instance
        return {"Instances": [self.instances[token]]}

    def describe_instances(self, **args):
        if "Filters" in args:
            token = args["Filters"][0]["Values"][0]
            found = self.instances.get(token)
        else:
            instance_id = args["InstanceIds"][0]
            found = next((item for item in self.instances.values()
                          if item["InstanceId"] == instance_id), None)
        return {"Reservations": [{"Instances": [found]}] if found else []}

    def terminate_instances(self, **args):
        instance_id = args["InstanceIds"][0]
        self.terminations.append(instance_id)
        for item in self.instances.values():
            if item["InstanceId"] == instance_id:
                item["State"] = {"Name": "terminated"}
        return {"TerminatingInstances": [{"InstanceId": instance_id}]}

    def modify_instance_metadata_options(self, **args):
        self.metadata_changes.append(args)
        for item in self.instances.values():
            if item["InstanceId"] == args["InstanceId"]:
                item["MetadataOptions"].update(args)
                item["MetadataOptions"].pop("InstanceId")
                item["MetadataOptions"]["State"] = "applied"
        return {"InstanceId": args["InstanceId"]}


class PendingSealEC2(FakeEC2):
    def modify_instance_metadata_options(self, **args):
        result = super().modify_instance_metadata_options(**args)
        for item in self.instances.values():
            item["MetadataOptions"]["State"] = "pending"
        return result


class ReadyS3:
    def __init__(self):
        self.objects = {}
        self.signer = boto3.client(
            "s3", region_name="us-east-1", aws_access_key_id="fixture-key",
            aws_secret_access_key="fixture-secret",
            config=Config(signature_version="s3v4"),
        )

    def generate_presigned_url(self, *args, **kwargs):
        return self.signer.generate_presigned_url(*args, **kwargs)

    def get_object(self, **kwargs):
        data = self.objects.get(kwargs["Key"])
        if data is None:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"ContentLength": len(data), "Body": io.BytesIO(data)}

    def put_object(self, **kwargs):
        if kwargs["Key"] in self.objects:
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        self.objects[kwargs["Key"]] = kwargs["Body"]
        return {}

    def head_object(self, **kwargs):
        import base64
        import hashlib
        data = self.objects[kwargs["Key"]]
        return {
            "ContentLength": len(data),
            "ChecksumSHA256": base64.b64encode(hashlib.sha256(data).digest()).decode(),
        }


@pytest.fixture
def scoped_db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Tenant(id="tenant-a", name="A"))
        db.add(Project(id="project-a", tenant_id="tenant-a", name="A"))
        db.add(Task(
            id="task-a", tenant_id="tenant-a", project_id="project-a",
            report="bug", expected_behavior="works", actual_behavior="broken",
            created_by="alice",
        ))
        db.add(Run(
            id="run-a", tenant_id="tenant-a", project_id="project-a", task_id="task-a",
            created_by="alice", idempotency_key="key-a", request_hash="h" * 64,
            base_commit="a" * 40, model_entry_id="model-a", state="QUEUED",
            config_snapshot={"policy_version": "1.0"},
        ))
        db.commit()
        run, fence = claim_run(db, "run-a", "worker-a")
        transition(db, run, "worker-a", fence, "PREPARING")
        db.commit()
        yield db, run, fence
    engine.dispose()


def _spec():
    return SandboxSpec(
        image_id="ami-12345678", instance_type="m6i.large",
        subnet_id="subnet-12345678", security_group_id="sg-12345678",
        root_device_name="/dev/xvda", disk_gib=40,
    )


_ENVELOPE_KEY = b"b" * 32
_SOURCE_SHA = "a" * 64


def _attach(db, lease, fence):
    return attach_guest_bootstrap(
        db, lease.id, "worker-a", fence,
        "#!/bin/bash\nset -e\n", _SOURCE_SHA, _ENVELOPE_KEY,
    )


def _ready(s3, lease, fence):
    keys = SandboxObjectKeys.scoped(
        lease.tenant_id, lease.project_id, lease.run_id, lease.id
    )
    s3.objects[keys.ready] = json.dumps({
        "version": 1, "lease_id": lease.id, "fence": fence,
        "source_sha256": _SOURCE_SHA,
    }).encode()
    return keys


def test_ec2_intent_is_fenced_private_encrypted_and_idempotent(scoped_db):
    db, run, fence = scoped_db
    lease = reserve_sandbox(db, run.id, "worker-a", fence, _spec())
    _attach(db, lease, fence)
    again = reserve_sandbox(db, run.id, "worker-a", fence, _spec())
    assert again.id == lease.id and lease.generation == 1
    ec2 = FakeEC2()
    # The first AWS request can succeed while its database receipt is lost.
    ec2.run_instances(**_launch_request(lease, "#!/bin/bash\nset -e\n"))
    launched = launch_reserved_sandbox(db, lease.id, "worker-a", fence, ec2, _ENVELOPE_KEY)
    assert launched.state == "bootstrapping" and launched.instance_id == "i-12345678"
    assert len(ec2.calls) == 2 and len(ec2.instances) == 1
    request = ec2.calls[0]
    assert request["ClientToken"] == lease.client_token
    assert request["NetworkInterfaces"][0]["AssociatePublicIpAddress"] is False
    assert request["MetadataOptions"]["HttpEndpoint"] == "enabled"
    assert request["MetadataOptions"]["HttpTokens"] == "required"
    assert request["UserData"].startswith("#!/bin/bash")
    assert request["BlockDeviceMappings"][0]["Ebs"] == {
        "VolumeSize": 40, "VolumeType": "gp3", "Encrypted": True,
        "DeleteOnTermination": True,
    }
    assert "IamInstanceProfile" not in request and "KeyName" not in request
    assert (
        launch_reserved_sandbox(db, lease.id, "worker-a", fence, ec2, _ENVELOPE_KEY).id
        == lease.id
    )
    assert len(ec2.calls) == 2


def test_cancel_revokes_vm_before_late_cleanup_and_verifies_termination(scoped_db):
    db, run, fence = scoped_db
    lease = reserve_sandbox(db, run.id, "worker-a", fence, _spec())
    _attach(db, lease, fence)
    ec2 = FakeEC2()
    launch_reserved_sandbox(db, lease.id, "worker-a", fence, ec2, _ENVELOPE_KEY)
    with pytest.raises(ServiceError):
        assert_sandbox_callback(db, lease.id, lease.instance_id, fence)
    s3 = ReadyS3()
    keys = _ready(s3, lease, fence)
    assert seal_bootstrapping_sandbox(
        db, lease.id, "worker-a", fence, ec2, s3, "private-bucket"
    )
    assert keys.go in s3.objects
    assert ec2.metadata_changes[0]["HttpEndpoint"] == "disabled"
    assert assert_sandbox_callback(db, lease.id, lease.instance_id, fence).id == lease.id
    request_cancel(db, run, "alice")
    db.commit()
    assert lease.state == "revoked" and run.cancel_requested
    assert db.scalar(select(OutboxEvent).where(
        OutboxEvent.topic == "sandbox.cleanup"
    )) is not None
    with pytest.raises(ServiceError):
        launch_reserved_sandbox(db, lease.id, "worker-a", fence, ec2, _ENVELOPE_KEY)
    with pytest.raises(ServiceError) as late:
        assert_sandbox_callback(db, lease.id, lease.instance_id, fence)
    assert late.value.code == "SANDBOX_REVOKED"
    assert terminate_revoked_sandbox(db, lease.id, ec2) is False
    assert ec2.terminations == ["i-12345678"]
    assert terminate_revoked_sandbox(db, lease.id, ec2) is True
    assert db.get(SandboxLease, lease.id).state == "terminated"


def test_unknown_launch_receipt_is_found_by_client_token_before_cleanup(scoped_db):
    db, run, fence = scoped_db
    lease = reserve_sandbox(db, run.id, "worker-a", fence, _spec())
    _attach(db, lease, fence)
    ec2 = FakeEC2()
    ec2.run_instances(**_launch_request(lease, "#!/bin/bash\nset -e\n"))
    revoke_sandbox(db, lease.id, "cancelled")
    assert lease.instance_id is None
    assert terminate_revoked_sandbox(db, lease.id, ec2) is False
    assert lease.instance_id == "i-12345678"
    assert ec2.terminations == ["i-12345678"]
    assert terminate_revoked_sandbox(db, lease.id, ec2) is True


def test_public_ip_response_is_revoked_and_late_fence_cannot_launch(scoped_db):
    db, run, fence = scoped_db
    lease = reserve_sandbox(db, run.id, "worker-a", fence, _spec())
    _attach(db, lease, fence)
    ec2 = FakeEC2(public_ip=True)
    with pytest.raises(ServiceError) as unsafe:
        launch_reserved_sandbox(db, lease.id, "worker-a", fence, ec2, _ENVELOPE_KEY)
    assert unsafe.value.code == "SANDBOX_UNSAFE"
    assert lease.state == "revoked"
    assert db.scalar(select(OutboxEvent).where(
        OutboxEvent.topic == "sandbox.cleanup"
    )) is not None
    run.lease_fence += 1
    db.commit()
    with pytest.raises(ServiceError) as stale:
        launch_reserved_sandbox(db, lease.id, "worker-a", fence, ec2, _ENVELOPE_KEY)
    assert stale.value.code == "LEASE_LOST"
    assert len(ec2.calls) == 1


def test_sandbox_spec_rejects_public_or_unbounded_inputs():
    with pytest.raises(ValueError):
        SandboxSpec("ami-12345678", "p5.48xlarge", "subnet-12345678",
                    "sg-12345678", "/dev/xvda")
    with pytest.raises(ValueError):
        SandboxSpec("ami-12345678", "m6i.large", "subnet-12345678",
                    "sg-12345678", "/dev/xvda", ttl_seconds=3600)
    with pytest.raises(ValueError):
        SandboxSpec("ami-12345678", "m6i.large", "subnet-12345678",
                    "sg-12345678", "/dev/xvda", ttl_seconds=60)
    with pytest.raises(ValueError):
        SandboxSpec("ami-12345678", "m6i.large", "subnet-12345678",
                    "sg-12345678", "/dev/xvda", disk_gib=101)


def test_expiry_sweep_finds_abandoned_intent(scoped_db):
    db, run, fence = scoped_db
    lease = reserve_sandbox(db, run.id, "worker-a", fence, _spec())
    assert due_sandbox_lease_ids(db) == []
    lease.expires_at = utcnow() - timedelta(minutes=1)
    db.commit()
    assert due_sandbox_lease_ids(db) == [lease.id]


def test_encrypted_bootstrap_is_scoped_and_authenticates_digest():
    user_data = "#!/bin/bash\nset -e\n"
    envelope, digest = encrypt_user_data(
        user_data, _ENVELOPE_KEY, "tenant-a", "run-a", "lease-a", 1
    )
    assert decrypt_user_data(
        envelope, digest, _ENVELOPE_KEY, "tenant-a", "run-a", "lease-a", 1
    ) == user_data
    with pytest.raises(SandboxBootstrapError):
        decrypt_user_data(
            envelope, digest, _ENVELOPE_KEY, "tenant-b", "run-a", "lease-a", 1
        )
    with pytest.raises(SandboxBootstrapError):
        decrypt_user_data(
            envelope, "0" * 64, _ENVELOPE_KEY, "tenant-a", "run-a", "lease-a", 1
        )
    with pytest.raises(SandboxBootstrapError):
        encrypt_user_data("not a script", _ENVELOPE_KEY, "tenant-a", "run-a", "lease-a", 1)


def test_guest_cannot_activate_until_ready_and_metadata_seal_applies(scoped_db):
    db, run, fence = scoped_db
    lease = reserve_sandbox(db, run.id, "worker-a", fence, _spec())
    ec2 = PendingSealEC2()
    with pytest.raises(ServiceError) as missing:
        launch_reserved_sandbox(db, lease.id, "worker-a", fence, ec2, _ENVELOPE_KEY)
    assert missing.value.code == "SANDBOX_BOOTSTRAP_MISSING"
    _attach(db, lease, fence)
    launch_reserved_sandbox(db, lease.id, "worker-a", fence, ec2, _ENVELOPE_KEY)
    s3 = ReadyS3()
    assert not seal_bootstrapping_sandbox(
        db, lease.id, "worker-a", fence, ec2, s3, "private-bucket"
    )
    keys = _ready(s3, lease, fence)
    assert not seal_bootstrapping_sandbox(
        db, lease.id, "worker-a", fence, ec2, s3, "private-bucket"
    )
    assert keys.go not in s3.objects
    with pytest.raises(ServiceError):
        assert_sandbox_callback(db, lease.id, lease.instance_id, fence)
    for instance in ec2.instances.values():
        instance["MetadataOptions"]["State"] = "applied"
    assert seal_bootstrapping_sandbox(
        db, lease.id, "worker-a", fence, ec2, s3, "private-bucket"
    )
    assert keys.go in s3.objects
    assert lease.sealed_at is not None


def test_cancel_during_bootstrap_revokes_before_guest_go(scoped_db):
    db, run, fence = scoped_db
    lease = reserve_sandbox(db, run.id, "worker-a", fence, _spec())
    _attach(db, lease, fence)
    ec2 = FakeEC2()
    launch_reserved_sandbox(db, lease.id, "worker-a", fence, ec2, _ENVELOPE_KEY)
    s3 = ReadyS3()
    keys = _ready(s3, lease, fence)
    request_cancel(db, run, "alice")
    db.commit()
    assert lease.state == "revoked"
    with pytest.raises(ServiceError):
        seal_bootstrapping_sandbox(db, lease.id, "worker-a", fence, ec2, s3, "private-bucket")
    assert keys.go not in s3.objects


def test_wrong_ready_marker_revokes_vm_and_queues_cleanup(scoped_db):
    db, run, fence = scoped_db
    lease = reserve_sandbox(db, run.id, "worker-a", fence, _spec())
    _attach(db, lease, fence)
    ec2 = FakeEC2()
    launch_reserved_sandbox(db, lease.id, "worker-a", fence, ec2, _ENVELOPE_KEY)
    s3 = ReadyS3()
    keys = _ready(s3, lease, fence)
    s3.objects[keys.ready] = b"wrong"
    with pytest.raises(ServiceError) as unsafe:
        seal_bootstrapping_sandbox(db, lease.id, "worker-a", fence, ec2, s3, "private-bucket")
    assert unsafe.value.code == "SANDBOX_UNSAFE"
    assert lease.state == "revoked"
    assert db.scalar(select(OutboxEvent).where(
        OutboxEvent.topic == "sandbox.cleanup"
    )) is not None


def test_hosted_baseline_handoff_is_replay_safe_and_records_guest_evidence(scoped_db):
    db, run, fence = scoped_db
    manifest = EnvironmentManifest.model_validate({
        "schema_version": "1.0", "language": "python", "python_version": "3.12",
        "services": [{
            "name": "app", "port": 8001, "health_path": "/health",
            "command": {"argv": ["python", "app.py"], "timeout_seconds": 30},
        }],
        "named_tests": {"baseline": {
            "argv": ["python", "-c", "print('ok')"], "timeout_seconds": 30,
        }},
        "browser_scenario": {"steps": [{"action": "goto", "path": "/"}]},
    })
    run.config_snapshot = {"environment_manifest": manifest.model_dump(mode="json")}
    db.commit()
    archive_file = io.BytesIO()
    with tarfile.open(fileobj=archive_file, mode="w") as archive:
        data = b"print('app')\n"
        entry = tarfile.TarInfo("app.py")
        entry.size = len(data)
        archive.addfile(entry, io.BytesIO(data))
    archive_bytes = archive_file.getvalue()
    source = SourceArchive(
        run.base_commit, hashlib.sha256(archive_bytes).hexdigest(), archive_bytes, 1
    )
    ec2, s3 = FakeEC2(), ReadyS3()
    lease = stage_and_launch_baseline(
        db, run.id, "worker-a", fence, source, _spec(), ec2, s3,
        "private-bucket", _ENVELOPE_KEY,
    )
    assert lease.state == "bootstrapping" and len(ec2.calls) == 1
    assert stage_and_launch_baseline(
        db, run.id, "worker-a", fence, source, _spec(), ec2, s3,
        "private-bucket", _ENVELOPE_KEY,
    ).id == lease.id
    assert len(ec2.calls) == 1
    keys = SandboxObjectKeys.scoped(
        lease.tenant_id, lease.project_id, lease.run_id, lease.id
    )
    s3.objects[keys.ready] = json.dumps({
        "version": 1, "lease_id": lease.id, "fence": fence,
        "source_sha256": lease.source_sha256,
    }).encode()
    assert seal_and_collect_baseline(
        db, lease.id, "worker-a", fence, ec2, s3, "private-bucket"
    ) is None
    assert keys.go in s3.objects and lease.state == "provisioned"
    evidence = b"guest output"
    guest_result = {
        "version": 1, "lease_id": lease.id, "fence": fence,
        "source_sha256": lease.source_sha256,
        "guest_exit_code": 0, "baseline": {"status": "BASELINE_RECORDED"},
        "evidence_sha256": hashlib.sha256(evidence).hexdigest(),
        "evidence_bytes": len(evidence),
    }
    s3.objects[keys.evidence] = evidence
    s3.objects[keys.result] = json.dumps(guest_result).encode()
    result = seal_and_collect_baseline(
        db, lease.id, "worker-a", fence, ec2, s3, "private-bucket"
    )
    assert result.evidence_archive == evidence
    assert lease.result_summary == guest_result
    assert lease.result_sha256 is not None
    assert seal_and_collect_baseline(
        db, lease.id, "worker-a", fence, ec2, s3, "private-bucket"
    ).result == guest_result
    assert len(db.scalars(select(RunEvent).where(
        RunEvent.run_id == run.id,
        RunEvent.event_type == "sandbox.baseline_recorded",
    )).all()) == 1
    events = db.scalars(select(OutboxEvent).where(
        OutboxEvent.topic == "sandbox.cleanup"
    )).all()
    assert events == []
