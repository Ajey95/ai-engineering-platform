import hashlib
import io
import json
from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from platform_app.db import Base, utcnow
from platform_app.hosted_evidence import verified_guest_evidence
from platform_app.models import Project, Run, SandboxLease, Task, Tenant
from platform_app.sandbox_transport import SandboxObjectKeys
from platform_app.service import ServiceError


class FakeS3:
    def __init__(self, objects):
        self.objects = objects

    def get_object(self, *, Bucket, Key):
        data = self.objects[Key]
        return {"ContentLength": len(data), "Body": io.BytesIO(data)}


def test_guest_evidence_download_checks_scope_and_database_receipt():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Tenant(id="tenant-a", name="A"))
        db.add(Project(id="project-a", tenant_id="tenant-a", name="A"))
        db.add(Task(
            id="task-a", tenant_id="tenant-a", project_id="project-a",
            report="Failure", expected_behavior="pass", actual_behavior="fail",
            created_by="alice",
        ))
        run = Run(
            id="run-a", tenant_id="tenant-a", project_id="project-a",
            task_id="task-a", created_by="alice", idempotency_key="k",
            request_hash="a" * 64, base_commit="b" * 40,
            model_entry_id="model-a", config_snapshot={},
        )
        db.add(run)
        db.flush()
        lease = SandboxLease(
            id="lease-a", tenant_id="tenant-a", project_id="project-a",
            run_id="run-a", generation=1, phase="baseline", lease_fence=2,
            client_token="a" * 64, state="terminated", image_id="ami-12345678",
            instance_type="m6i.large", subnet_id="subnet-12345678",
            security_group_id="sg-12345678", root_device_name="/dev/xvda",
            disk_gib=40, expires_at=utcnow() + timedelta(minutes=5),
            source_sha256="c" * 64,
        )
        evidence = b"recorded guest evidence"
        result = {
            "version": 1, "lease_id": lease.id, "fence": lease.lease_fence,
            "source_sha256": lease.source_sha256, "guest_exit_code": 0,
            "phase": "baseline", "baseline": {"status": "BASELINE_RECORDED"},
            "evidence_sha256": hashlib.sha256(evidence).hexdigest(),
            "evidence_bytes": len(evidence),
        }
        lease.result_summary = result
        lease.result_sha256 = hashlib.sha256(json.dumps(
            result, sort_keys=True, separators=(",", ":")
        ).encode()).hexdigest()
        lease.result_received_at = utcnow()
        db.add(lease)
        db.commit()
        keys = SandboxObjectKeys.scoped("tenant-a", "project-a", "run-a", "lease-a")
        objects = {keys.result: json.dumps(result).encode(), keys.evidence: evidence}
        assert verified_guest_evidence(db, run, "baseline", FakeS3(objects), "bucket") == evidence
        with pytest.raises(ServiceError, match="not recorded"):
            verified_guest_evidence(db, run, "candidate", FakeS3(objects), "bucket")
        objects[keys.evidence] = b"tampered guest evidence"
        with pytest.raises(ServiceError, match="invalid"):
            verified_guest_evidence(db, run, "baseline", FakeS3(objects), "bucket")
    engine.dispose()
