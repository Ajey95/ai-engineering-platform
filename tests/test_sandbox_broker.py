from datetime import timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from platform_app.db import Base, utcnow
from platform_app.models import OutboxEvent, Project, Run, SandboxLease, Task, Tenant
from platform_app.run_ledger import claim_run, transition
from platform_app.sandbox_broker import (
    SandboxSpec,
    _launch_request,
    assert_sandbox_callback,
    due_sandbox_lease_ids,
    launch_reserved_sandbox,
    reserve_sandbox,
    revoke_sandbox,
    terminate_revoked_sandbox,
)
from platform_app.service import ServiceError, request_cancel


class FakeEC2:
    def __init__(self, *, public_ip: bool = False):
        self.instances = {}
        self.calls = []
        self.terminations = []
        self.public_ip = public_ip

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
                "MetadataOptions": args["MetadataOptions"],
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


def test_ec2_intent_is_fenced_private_encrypted_and_idempotent(scoped_db):
    db, run, fence = scoped_db
    lease = reserve_sandbox(db, run.id, "worker-a", fence, _spec())
    again = reserve_sandbox(db, run.id, "worker-a", fence, _spec())
    assert again.id == lease.id and lease.generation == 1
    ec2 = FakeEC2()
    # The first AWS request can succeed while its database receipt is lost.
    ec2.run_instances(**_launch_request(lease))
    launched = launch_reserved_sandbox(db, lease.id, "worker-a", fence, ec2)
    assert launched.state == "provisioned" and launched.instance_id == "i-12345678"
    assert len(ec2.calls) == 2 and len(ec2.instances) == 1
    request = ec2.calls[0]
    assert request["ClientToken"] == lease.client_token
    assert request["NetworkInterfaces"][0]["AssociatePublicIpAddress"] is False
    assert request["MetadataOptions"]["HttpEndpoint"] == "disabled"
    assert request["BlockDeviceMappings"][0]["Ebs"] == {
        "VolumeSize": 40, "VolumeType": "gp3", "Encrypted": True,
        "DeleteOnTermination": True,
    }
    assert "IamInstanceProfile" not in request and "KeyName" not in request
    assert launch_reserved_sandbox(db, lease.id, "worker-a", fence, ec2).id == lease.id
    assert len(ec2.calls) == 2


def test_cancel_revokes_vm_before_late_cleanup_and_verifies_termination(scoped_db):
    db, run, fence = scoped_db
    lease = reserve_sandbox(db, run.id, "worker-a", fence, _spec())
    ec2 = FakeEC2()
    launch_reserved_sandbox(db, lease.id, "worker-a", fence, ec2)
    assert assert_sandbox_callback(db, lease.id, lease.instance_id, fence).id == lease.id
    request_cancel(db, run, "alice")
    db.commit()
    assert lease.state == "revoked" and run.cancel_requested
    assert db.scalar(select(OutboxEvent).where(
        OutboxEvent.topic == "sandbox.cleanup"
    )) is not None
    with pytest.raises(ServiceError):
        launch_reserved_sandbox(db, lease.id, "worker-a", fence, ec2)
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
    ec2 = FakeEC2()
    ec2.run_instances(**_launch_request(lease))
    revoke_sandbox(db, lease.id, "cancelled")
    assert lease.instance_id is None
    assert terminate_revoked_sandbox(db, lease.id, ec2) is False
    assert lease.instance_id == "i-12345678"
    assert ec2.terminations == ["i-12345678"]
    assert terminate_revoked_sandbox(db, lease.id, ec2) is True


def test_public_ip_response_is_revoked_and_late_fence_cannot_launch(scoped_db):
    db, run, fence = scoped_db
    lease = reserve_sandbox(db, run.id, "worker-a", fence, _spec())
    ec2 = FakeEC2(public_ip=True)
    with pytest.raises(ServiceError) as unsafe:
        launch_reserved_sandbox(db, lease.id, "worker-a", fence, ec2)
    assert unsafe.value.code == "SANDBOX_UNSAFE"
    assert lease.state == "revoked"
    assert db.scalar(select(OutboxEvent).where(
        OutboxEvent.topic == "sandbox.cleanup"
    )) is not None
    run.lease_fence += 1
    db.commit()
    with pytest.raises(ServiceError) as stale:
        launch_reserved_sandbox(db, lease.id, "worker-a", fence, ec2)
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
                    "sg-12345678", "/dev/xvda", disk_gib=101)


def test_expiry_sweep_finds_abandoned_intent(scoped_db):
    db, run, fence = scoped_db
    lease = reserve_sandbox(db, run.id, "worker-a", fence, _spec())
    assert due_sandbox_lease_ids(db) == []
    lease.expires_at = utcnow() - timedelta(minutes=1)
    db.commit()
    assert due_sandbox_lease_ids(db) == [lease.id]
