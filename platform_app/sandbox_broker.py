"""Durable, fenced EC2 sandbox lifecycle; guest execution remains disabled."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from platform_app.db import utcnow
from platform_app.models import OutboxEvent, Run, SandboxLease
from platform_app.run_ledger import ACTIVE_STATES, TERMINAL_STATES, assert_fence, aware
from platform_app.sandbox_bootstrap_crypto import decrypt_user_data, encrypt_user_data
from platform_app.sandbox_transport import (
    SandboxObjectKeys,
    SandboxTransportError,
    guest_ready,
    publish_guest_go,
)
from platform_app.service import ServiceError, append_event

_AWS_ID = re.compile(r"(?:ami|subnet|sg)-[0-9a-f]{8,17}\Z")
_INSTANCE_ID = re.compile(r"i-[0-9a-f]{8,17}\Z")
_INSTANCE_TYPES = frozenset({"m6i.large", "m7i.large"})


@dataclass(frozen=True)
class SandboxSpec:
    image_id: str
    instance_type: str
    subnet_id: str
    security_group_id: str
    root_device_name: str
    disk_gib: int = 40
    ttl_seconds: int = 1800

    def __post_init__(self):
        if (
            not all(_AWS_ID.fullmatch(value) for value in (
                self.image_id, self.subnet_id, self.security_group_id
            ))
            or not self.image_id.startswith("ami-")
            or not self.subnet_id.startswith("subnet-")
            or not self.security_group_id.startswith("sg-")
            or self.instance_type not in _INSTANCE_TYPES
            or self.root_device_name not in {"/dev/xvda", "/dev/sda1"}
            or not 8 <= self.disk_gib <= 100
            or not 60 <= self.ttl_seconds <= 1800
        ):
            raise ValueError("Sandbox launch spec is outside the reviewed bounds")


def _matches(lease: SandboxLease, spec: SandboxSpec) -> bool:
    return (
        lease.image_id == spec.image_id
        and lease.instance_type == spec.instance_type
        and lease.subnet_id == spec.subnet_id
        and lease.security_group_id == spec.security_group_id
        and lease.root_device_name == spec.root_device_name
        and lease.disk_gib == spec.disk_gib
    )


def reserve_sandbox(
    db: Session, run_id: str, worker_id: str, fence: int, spec: SandboxSpec
) -> SandboxLease:
    """Commit an EC2 idempotency key before any external launch call."""
    run = db.scalar(
        select(Run).where(Run.id == run_id).with_for_update()
        .execution_options(populate_existing=True)
    )
    if run is None:
        raise ServiceError("NOT_FOUND", "Run not found", 404)
    assert_fence(run, worker_id, fence)
    if run.cancel_requested or run.state not in ACTIVE_STATES:
        raise ServiceError("SANDBOX_NOT_ALLOWED", "Run is not executable", 409)
    existing = db.scalar(select(SandboxLease).where(
        SandboxLease.tenant_id == run.tenant_id,
        SandboxLease.run_id == run.id,
        SandboxLease.state.in_([
            "intended", "bootstrapping", "provisioned", "revoked", "terminating"
        ]),
    ).with_for_update())
    if existing is not None:
        if (
            existing.state in {"intended", "bootstrapping", "provisioned"}
            and existing.lease_fence == fence and _matches(existing, spec)
        ):
            return existing
        raise ServiceError("SANDBOX_STILL_ACTIVE", "Prior sandbox needs cleanup", 409)
    generation = (db.scalar(select(func.max(SandboxLease.generation)).where(
        SandboxLease.run_id == run.id
    )) or 0) + 1
    token = hashlib.sha256(
        f"aip-sandbox-v1:{run.tenant_id}:{run.id}:{generation}".encode()
    ).hexdigest()
    lease = SandboxLease(
        tenant_id=run.tenant_id, project_id=run.project_id, run_id=run.id,
        generation=generation, lease_fence=fence, client_token=token,
        state="intended", image_id=spec.image_id, instance_type=spec.instance_type,
        subnet_id=spec.subnet_id, security_group_id=spec.security_group_id,
        root_device_name=spec.root_device_name, disk_gib=spec.disk_gib,
        expires_at=utcnow() + timedelta(seconds=spec.ttl_seconds),
    )
    db.add(lease)
    db.flush()
    append_event(db, run, "sandbox.intended", {
        "sandbox_lease_id": lease.id, "generation": generation, "image_id": spec.image_id,
    })
    db.commit()
    db.refresh(lease)
    return lease


def attach_guest_bootstrap(
    db: Session, lease_id: str, worker_id: str, fence: int,
    user_data: str, source_sha256: str, envelope_key: bytes,
) -> SandboxLease:
    """Commit the exact encrypted user data before the idempotent EC2 launch."""
    if not re.fullmatch(r"[0-9a-f]{64}", source_sha256):
        raise ValueError("Pinned source digest is invalid")
    scoped = db.get(SandboxLease, lease_id)
    if scoped is None:
        raise ServiceError("NOT_FOUND", "Sandbox lease not found", 404)
    run = db.scalar(
        select(Run).where(Run.id == scoped.run_id).with_for_update()
        .execution_options(populate_existing=True)
    )
    lease = db.scalar(
        select(SandboxLease).where(SandboxLease.id == lease_id).with_for_update()
        .execution_options(populate_existing=True)
    )
    if run is None or lease is None or run.tenant_id != lease.tenant_id:
        raise ServiceError("SANDBOX_SCOPE", "Sandbox run scope changed", 409)
    assert_fence(run, worker_id, fence)
    if (
        run.cancel_requested or run.state not in ACTIVE_STATES
        or lease.lease_fence != fence or lease.state != "intended"
        or aware(lease.expires_at) <= utcnow()
    ):
        raise ServiceError("SANDBOX_REVOKED", "Sandbox bootstrap is no longer allowed", 409)
    envelope, digest = encrypt_user_data(
        user_data, envelope_key, lease.tenant_id, lease.run_id,
        lease.id, lease.generation,
    )
    if lease.bootstrap_envelope is not None:
        if lease.bootstrap_sha256 == digest and lease.source_sha256 == source_sha256:
            decrypt_user_data(
                lease.bootstrap_envelope, lease.bootstrap_sha256, envelope_key,
                lease.tenant_id, lease.run_id, lease.id, lease.generation,
            )
            return lease
        raise ServiceError("SANDBOX_BOOTSTRAP_CONFLICT", "Bootstrap differs from intent", 409)
    lease.bootstrap_envelope = envelope
    lease.bootstrap_sha256 = digest
    lease.source_sha256 = source_sha256
    lease.updated_at = utcnow()
    append_event(db, run, "sandbox.bootstrap_attached", {
        "sandbox_lease_id": lease.id, "bootstrap_sha256": digest,
        "source_sha256": source_sha256,
    })
    db.commit()
    return lease


def _launch_request(lease: SandboxLease, user_data: str) -> dict:
    tags = [
        {"Key": "aip:managed", "Value": "sandbox"},
        {"Key": "aip:tenant", "Value": lease.tenant_id},
        {"Key": "aip:run", "Value": lease.run_id},
        {"Key": "aip:lease", "Value": lease.id},
        {"Key": "aip:expires", "Value": str(int(aware(lease.expires_at).timestamp()))},
    ]
    return {
        "ImageId": lease.image_id,
        "InstanceType": lease.instance_type,
        "MinCount": 1, "MaxCount": 1,
        "ClientToken": lease.client_token,
        "NetworkInterfaces": [{
            "DeviceIndex": 0, "SubnetId": lease.subnet_id,
            "Groups": [lease.security_group_id],
            "AssociatePublicIpAddress": False,
            "DeleteOnTermination": True,
        }],
        # Cloud-init needs IMDSv2 to read user data. The broker disables the
        # endpoint before publishing the go marker for untrusted code.
        "MetadataOptions": {
            "HttpEndpoint": "enabled", "HttpTokens": "required",
            "HttpPutResponseHopLimit": 1, "HttpProtocolIpv6": "disabled",
            "InstanceMetadataTags": "disabled",
        },
        "UserData": user_data,
        "BlockDeviceMappings": [{
            "DeviceName": lease.root_device_name,
            "Ebs": {
                "VolumeSize": lease.disk_gib, "VolumeType": "gp3", "Encrypted": True,
                "DeleteOnTermination": True,
            },
        }],
        "InstanceInitiatedShutdownBehavior": "terminate",
        "TagSpecifications": [
            {"ResourceType": "instance", "Tags": tags},
            {"ResourceType": "volume", "Tags": tags},
        ],
    }


def _instance_safe(
    instance: dict, lease: SandboxLease, *, metadata_endpoint: str
) -> bool:
    interfaces = instance.get("NetworkInterfaces") or []
    metadata = instance.get("MetadataOptions") or {}
    if len(interfaces) != 1:
        return False
    interface = interfaces[0]
    groups = {group.get("GroupId") for group in interface.get("Groups", [])}
    return (
        "IamInstanceProfile" not in instance
        and not instance.get("KeyName")
        and not instance.get("PublicIpAddress")
        and not interface.get("Association")
        and interface.get("SubnetId") == lease.subnet_id
        and groups == {lease.security_group_id}
        and metadata.get("HttpEndpoint") == metadata_endpoint
        and metadata.get("HttpTokens") == "required"
        and metadata.get("HttpPutResponseHopLimit") == 1
        and metadata.get("HttpProtocolIpv6", "disabled") == "disabled"
        and metadata.get("InstanceMetadataTags") == "disabled"
    )


def launch_reserved_sandbox(
    db: Session, lease_id: str, worker_id: str, fence: int, ec2,
    envelope_key: bytes,
) -> SandboxLease:
    """Replay uses the same ClientToken and cannot silently create a second VM."""
    scoped = db.get(SandboxLease, lease_id)
    if scoped is None:
        raise ServiceError("NOT_FOUND", "Sandbox lease not found", 404)
    run = db.scalar(
        select(Run).where(Run.id == scoped.run_id).with_for_update()
        .execution_options(populate_existing=True)
    )
    lease = db.scalar(
        select(SandboxLease).where(SandboxLease.id == lease_id).with_for_update()
        .execution_options(populate_existing=True)
    )
    if lease is None:
        raise ServiceError("NOT_FOUND", "Sandbox lease not found", 404)
    if run is None or run.tenant_id != lease.tenant_id:
        raise ServiceError("SANDBOX_SCOPE", "Sandbox run scope changed", 409)
    assert_fence(run, worker_id, fence)
    if (
        run.cancel_requested or run.state not in ACTIVE_STATES
        or lease.lease_fence != fence or aware(lease.expires_at) <= utcnow()
    ):
        raise ServiceError("SANDBOX_REVOKED", "Sandbox launch is no longer allowed", 409)
    if lease.state in {"bootstrapping", "provisioned"} and lease.instance_id:
        return lease
    if lease.state != "intended":
        raise ServiceError("SANDBOX_REVOKED", "Sandbox intent is revoked", 409)
    if not lease.bootstrap_envelope or not lease.bootstrap_sha256 or not lease.source_sha256:
        raise ServiceError("SANDBOX_BOOTSTRAP_MISSING", "Guest bootstrap is not committed", 409)
    user_data = decrypt_user_data(
        lease.bootstrap_envelope, lease.bootstrap_sha256, envelope_key,
        lease.tenant_id, lease.run_id, lease.id, lease.generation,
    )
    response = ec2.run_instances(**_launch_request(lease, user_data))
    instances = response.get("Instances", [])
    if len(instances) != 1 or not _INSTANCE_ID.fullmatch(instances[0].get("InstanceId", "")):
        raise ServiceError("SANDBOX_OUTCOME_UNKNOWN", "EC2 launch needs reconciliation", 503)
    instance = instances[0]
    lease.instance_id = instance["InstanceId"]
    try:
        assert_fence(run, worker_id, fence)
        fence_lost = False
    except ServiceError:
        fence_lost = True
    unsafe = (
        not _instance_safe(instance, lease, metadata_endpoint="enabled")
        or fence_lost
        or run.cancel_requested
        or aware(lease.expires_at) <= utcnow()
    )
    lease.state = "revoked" if unsafe else "bootstrapping"
    lease.updated_at = utcnow()
    append_event(db, run, "sandbox.revoked" if unsafe else "sandbox.bootstrapping", {
        "sandbox_lease_id": lease.id, "instance_id": lease.instance_id,
    })
    if unsafe:
        db.add(OutboxEvent(
            tenant_id=run.tenant_id, topic="sandbox.cleanup",
            payload={"run_id": run.id, "sandbox_lease_id": lease.id},
        ))
    db.commit()
    if unsafe:
        raise ServiceError("SANDBOX_UNSAFE", "EC2 launch failed isolation checks", 503)
    return lease


def _describe_exact_instance(ec2, instance_id: str) -> dict:
    described = ec2.describe_instances(InstanceIds=[instance_id])
    instances = [
        instance for reservation in described.get("Reservations", [])
        for instance in reservation.get("Instances", [])
    ]
    if len(instances) != 1 or instances[0].get("InstanceId") != instance_id:
        raise ServiceError("SANDBOX_OUTCOME_UNKNOWN", "EC2 state is ambiguous", 503)
    return instances[0]


def _revoke_unsafe_bootstrap(db: Session, run: Run, lease: SandboxLease, reason: str) -> None:
    lease.state = "revoked"
    lease.updated_at = utcnow()
    db.add(OutboxEvent(
        tenant_id=run.tenant_id, topic="sandbox.cleanup",
        payload={"run_id": run.id, "sandbox_lease_id": lease.id},
    ))
    append_event(db, run, "sandbox.revoked", {
        "sandbox_lease_id": lease.id, "reason": reason,
    })
    db.commit()


def seal_bootstrapping_sandbox(
    db: Session, lease_id: str, worker_id: str, fence: int,
    ec2, s3, bucket: str,
) -> bool:
    """Publish go only after a ready marker and applied IMDS shutdown."""
    scoped = db.get(SandboxLease, lease_id)
    if scoped is None:
        raise ServiceError("NOT_FOUND", "Sandbox lease not found", 404)
    run = db.scalar(
        select(Run).where(Run.id == scoped.run_id).with_for_update()
        .execution_options(populate_existing=True)
    )
    lease = db.scalar(
        select(SandboxLease).where(SandboxLease.id == lease_id).with_for_update()
        .execution_options(populate_existing=True)
    )
    if run is None or lease is None or run.tenant_id != lease.tenant_id:
        raise ServiceError("SANDBOX_SCOPE", "Sandbox run scope changed", 409)
    assert_fence(run, worker_id, fence)
    if (
        run.cancel_requested or run.state not in ACTIVE_STATES
        or lease.lease_fence != fence or aware(lease.expires_at) <= utcnow()
    ):
        raise ServiceError("SANDBOX_REVOKED", "Sandbox activation is no longer allowed", 409)
    if lease.state == "provisioned" and lease.sealed_at is not None:
        return True
    if lease.state != "bootstrapping" or not lease.instance_id or not lease.source_sha256:
        raise ServiceError("SANDBOX_NOT_READY", "Sandbox has not reached bootstrap", 409)
    keys = SandboxObjectKeys.scoped(
        lease.tenant_id, lease.project_id, lease.run_id, lease.id
    )
    try:
        ready = guest_ready(
            s3, bucket, keys, lease.id, fence, lease.source_sha256
        )
    except SandboxTransportError as error:
        _revoke_unsafe_bootstrap(db, run, lease, "invalid_ready_marker")
        raise ServiceError("SANDBOX_UNSAFE", "Guest ready marker is invalid", 409) from error
    if not ready:
        db.rollback()
        return False
    instance = _describe_exact_instance(ec2, lease.instance_id)
    if not _instance_safe(instance, lease, metadata_endpoint=instance.get(
        "MetadataOptions", {}
    ).get("HttpEndpoint", "")):
        _revoke_unsafe_bootstrap(db, run, lease, "isolation_changed")
        raise ServiceError("SANDBOX_UNSAFE", "EC2 isolation changed during bootstrap", 503)
    metadata = instance.get("MetadataOptions", {})
    if metadata.get("HttpEndpoint") != "disabled":
        ec2.modify_instance_metadata_options(
            InstanceId=lease.instance_id, HttpEndpoint="disabled",
            HttpTokens="required", HttpProtocolIpv6="disabled",
            InstanceMetadataTags="disabled",
        )
        instance = _describe_exact_instance(ec2, lease.instance_id)
        metadata = instance.get("MetadataOptions", {})
    if metadata.get("State") != "applied":
        db.rollback()
        return False
    if not _instance_safe(instance, lease, metadata_endpoint="disabled"):
        _revoke_unsafe_bootstrap(db, run, lease, "metadata_seal_failed")
        raise ServiceError("SANDBOX_UNSAFE", "EC2 metadata seal failed", 503)
    publish_guest_go(s3, bucket, keys, lease.id, fence, lease.source_sha256)
    lease.state = "provisioned"
    lease.sealed_at = utcnow()
    lease.updated_at = utcnow()
    append_event(db, run, "sandbox.provisioned", {
        "sandbox_lease_id": lease.id, "instance_id": lease.instance_id,
        "metadata_endpoint": "disabled",
    })
    db.commit()
    return True


def revoke_sandbox(db: Session, lease_id: str, reason: str) -> SandboxLease:
    if reason not in {"cancelled", "expired", "closed", "unsafe"}:
        raise ValueError("Sandbox revocation reason is invalid")
    scoped = db.get(SandboxLease, lease_id)
    if scoped is None:
        raise ServiceError("NOT_FOUND", "Sandbox lease not found", 404)
    run = db.scalar(
        select(Run).where(Run.id == scoped.run_id).with_for_update()
        .execution_options(populate_existing=True)
    )
    lease = db.scalar(
        select(SandboxLease).where(SandboxLease.id == lease_id).with_for_update()
        .execution_options(populate_existing=True)
    )
    if lease is None:
        raise ServiceError("NOT_FOUND", "Sandbox lease not found", 404)
    if lease.state in {"revoked", "terminating", "terminated"}:
        return lease
    if run is None or run.tenant_id != lease.tenant_id:
        raise ServiceError("SANDBOX_SCOPE", "Sandbox run scope changed", 409)
    lease.state = "revoked"
    lease.updated_at = utcnow()
    db.add(OutboxEvent(
        tenant_id=run.tenant_id, topic="sandbox.cleanup",
        payload={"run_id": run.id, "sandbox_lease_id": lease.id},
    ))
    append_event(db, run, "sandbox.revoked", {
        "sandbox_lease_id": lease.id, "reason": reason,
    })
    db.commit()
    return lease


def _instance_for_token(ec2, token: str) -> dict | None:
    response = ec2.describe_instances(Filters=[{"Name": "client-token", "Values": [token]}])
    instances = [
        instance for reservation in response.get("Reservations", [])
        for instance in reservation.get("Instances", [])
    ]
    if len(instances) > 1:
        raise ServiceError("SANDBOX_OUTCOME_UNKNOWN", "Client token matched multiple VMs", 503)
    return instances[0] if instances else None


def terminate_revoked_sandbox(db: Session, lease_id: str, ec2) -> bool:
    """Return true only when EC2 reports terminated; unknown intent stays pending."""
    lease = db.scalar(select(SandboxLease).where(SandboxLease.id == lease_id).with_for_update())
    if lease is None:
        raise ServiceError("NOT_FOUND", "Sandbox lease not found", 404)
    if lease.state == "terminated":
        return True
    if lease.state not in {"revoked", "terminating"}:
        raise ServiceError("SANDBOX_ACTIVE", "Sandbox must be revoked before cleanup", 409)
    if lease.instance_id is None:
        found = _instance_for_token(ec2, lease.client_token)
        if found is None:
            return False
        if not _INSTANCE_ID.fullmatch(found.get("InstanceId", "")):
            raise ServiceError("SANDBOX_OUTCOME_UNKNOWN", "EC2 instance ID is invalid", 503)
        lease.instance_id = found["InstanceId"]
    described = ec2.describe_instances(InstanceIds=[lease.instance_id])
    instances = [
        instance for reservation in described.get("Reservations", [])
        for instance in reservation.get("Instances", [])
    ]
    if len(instances) != 1 or instances[0].get("InstanceId") != lease.instance_id:
        raise ServiceError("SANDBOX_OUTCOME_UNKNOWN", "EC2 state is ambiguous", 503)
    if instances[0].get("State", {}).get("Name") == "terminated":
        lease.state = "terminated"
        lease.updated_at = utcnow()
        db.commit()
        return True
    lease.state = "terminating"
    lease.updated_at = utcnow()
    db.commit()
    ec2.terminate_instances(InstanceIds=[lease.instance_id])
    return False


def due_sandbox_lease_ids(db: Session, limit: int = 100) -> list[str]:
    """Find expired or closed-run VMs independently of dispatch delivery."""
    return list(db.scalars(
        select(SandboxLease.id).join(Run, SandboxLease.run_id == Run.id).where(
            SandboxLease.tenant_id == Run.tenant_id,
            SandboxLease.state.in_(["intended", "bootstrapping", "provisioned"]),
            or_(
                SandboxLease.expires_at <= utcnow(),
                Run.cancel_requested.is_(True),
                Run.state.in_(TERMINAL_STATES),
            ),
        ).order_by(SandboxLease.expires_at, SandboxLease.id).limit(limit)
    ))


def assert_sandbox_callback(
    db: Session, lease_id: str, instance_id: str, fence: int
) -> SandboxLease:
    """A stale VM may never report a result after revocation or lease replacement."""
    scoped = db.get(SandboxLease, lease_id)
    run = (
        db.scalar(
            select(Run).where(Run.id == scoped.run_id).with_for_update()
            .execution_options(populate_existing=True)
        ) if scoped else None
    )
    lease = (
        db.scalar(
            select(SandboxLease).where(SandboxLease.id == lease_id).with_for_update()
            .execution_options(populate_existing=True)
        ) if run else None
    )
    if (
        lease is None or run is None or run.tenant_id != lease.tenant_id
        or lease.state != "provisioned" or lease.instance_id != instance_id
        or lease.lease_fence != fence or run.lease_fence != fence
        or run.cancel_requested or run.state not in ACTIVE_STATES
        or aware(lease.expires_at) <= utcnow()
    ):
        raise ServiceError("SANDBOX_REVOKED", "Sandbox callback is no longer authorized", 409)
    return lease
