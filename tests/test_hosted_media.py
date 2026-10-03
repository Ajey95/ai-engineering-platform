"""A verified guest video becomes a separate, replayable private HLS job."""

import base64
import hashlib
import io
import tarfile
from datetime import timedelta
from types import SimpleNamespace

from botocore.exceptions import ClientError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from platform_app.config import Settings
from platform_app.db import Base, utcnow
from platform_app.hosted_media import media_event_id, stage_hosted_recording
from platform_app.hosted_media_worker import dispatch_one_hosted_media
from platform_app.media import expected_hls_master
from platform_app.media_queue import consume_one_media_dispatch, publish_media_dispatches
from platform_app.models import (
    MediaMinuteCharge,
    OutboxEvent,
    PrivateMediaPublication,
    Project,
    Run,
    SandboxLease,
    Task,
    Tenant,
    ToolAction,
)
from platform_app.sandbox_transport import GuestOutput


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.puts = 0

    def head_object(self, **request):
        data = self.objects.get(request["Key"])
        if data is None:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "HeadObject")
        return {
            "ChecksumSHA256": data["ChecksumSHA256"],
            "ContentLength": len(data["Body"]),
        }

    def put_object(self, **request):
        if request["Key"] in self.objects:
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        self.objects[request["Key"]] = request
        self.puts += 1
        return {}


class FakeSQS:
    def __init__(self):
        self.messages = []
        self.deleted = []

    def send_message(self, **request):
        self.messages.append(request)
        return {"MessageId": f"message-{len(self.messages)}"}

    def receive_message(self, **request):
        if not self.messages:
            return {"Messages": []}
        return {"Messages": [{
            "Body": self.messages[0]["MessageBody"],
            "ReceiptHandle": "receipt-a",
        }]}

    def change_message_visibility(self, **request):
        return {}

    def delete_message(self, **request):
        self.deleted.append(request["ReceiptHandle"])
        self.messages.pop(0)


def _guest_output():
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w") as tar:
        raw = b"a controlled browser WebM source"
        entry = tarfile.TarInfo("browser/video.webm")
        entry.size = len(raw)
        tar.addfile(entry, io.BytesIO(raw))
    evidence = archive.getvalue()
    return GuestOutput({
        "lease_id": "lease-a", "evidence_sha256": hashlib.sha256(evidence).hexdigest(),
        "baseline": {"browser": {"recording": "video.webm"}},
    }, evidence)


def test_hosted_video_stages_then_publishes_private_hls(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'media.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    output = _guest_output()
    with factory() as db:
        db.add(Tenant(id="tenant-a", name="A"))
        db.add(Project(id="project-a", tenant_id="tenant-a", name="A"))
        db.add(Task(
            id="task-a", tenant_id="tenant-a", project_id="project-a",
            report="Video evidence", expected_behavior="Pass",
            actual_behavior="Fail", created_by="alice",
        ))
        db.add(Run(
            id="run-a", tenant_id="tenant-a", project_id="project-a",
            task_id="task-a", created_by="alice", idempotency_key="key-a",
            request_hash="a" * 64, base_commit="b" * 40,
            model_entry_id="model-a", state="REPRODUCING",
            lease_owner="worker-a", lease_fence=1,
            lease_until=utcnow() + timedelta(minutes=5),
            config_snapshot={"execution_profile": "hosted_vm_v1"},
        ))
        db.add(SandboxLease(
            id="lease-a", tenant_id="tenant-a", project_id="project-a",
            run_id="run-a", generation=1, phase="baseline", lease_fence=1,
            client_token="token-a", state="terminated", image_id="ami-12345678",
            instance_type="m6i.large", subnet_id="subnet-12345678",
            security_group_id="sg-12345678", root_device_name="/dev/xvda",
            disk_gib=8, expires_at=utcnow() + timedelta(minutes=5),
            result_received_at=utcnow(), result_summary=output.result,
        ))
        db.commit()
        run = db.get(Run, "run-a")
        assert stage_hosted_recording(
            db, run, "worker-a", 1, output, "baseline", tmp_path
        )
        db.commit()
        assert run.media_status == "PROCESSING"
        assert db.get(OutboxEvent, media_event_id("run-a", "baseline")).status == "pending"

    encode_calls = []

    def fake_encode(source, artifact_root, tenant_id, run_id):
        assert source.read_bytes() == b"a controlled browser WebM source"
        target = expected_hls_master(
            hashlib.sha256(source.read_bytes()).hexdigest(), artifact_root,
            tenant_id, run_id,
        ).parent
        if (target / "master.m3u8").is_file():
            return target / "master.m3u8"
        encode_calls.append(run_id)
        variant = target / "low"
        variant.mkdir(parents=True, exist_ok=True)
        (target / "master.m3u8").write_text("#EXTM3U\nlow/index.m3u8\n")
        (variant / "index.m3u8").write_text("#EXTM3U\ninit.mp4\nsegment_0000.m4s\n")
        (variant / "init.mp4").write_bytes(b"init")
        (variant / "segment_0000.m4s").write_bytes(b"segment")
        return target / "master.m3u8"

    import platform_app.hosted_media_worker as module

    monkeypatch.setattr(module, "encode_hls", fake_encode)
    monkeypatch.setattr(module, "probe", lambda _source: SimpleNamespace(duration_seconds=10.0))
    client = FakeS3()
    config = Settings(private_media_bucket="private-bucket")
    sqs = FakeSQS()
    queue_url = "https://sqs.example.test/123/media"
    with factory() as db:
        assert publish_media_dispatches(db, sqs, queue_url) == 1
        assert publish_media_dispatches(db, sqs, queue_url) == 0
        event = db.get(OutboxEvent, media_event_id("run-a", "baseline"))
        event.status = "processing"
        event.processing_token = "stale-worker"
        event.processing_lease_until = utcnow() - timedelta(seconds=1)
        db.commit()
    assert consume_one_media_dispatch(
        factory, sqs, queue_url,
        lambda event_id: dispatch_one_hosted_media(
            factory, config, tmp_path, client, event_id=event_id,
        ), wait_seconds=0,
    )
    assert sqs.deleted == ["receipt-a"]
    assert client.puts == 5
    for item in client.objects.values():
        assert item["ChecksumSHA256"] == base64.b64encode(
            hashlib.sha256(item["Body"]).digest()
        ).decode()
    with factory() as db:
        run = db.get(Run, "run-a")
        assert run.media_status == "READY"
        assert db.get(OutboxEvent, media_event_id("run-a", "baseline")).status == "delivered"
        assert db.query(PrivateMediaPublication).one().status == "ready"
        charge = db.query(MediaMinuteCharge).one()
        assert (charge.attempt, charge.reserved_seconds, charge.status) == (
            1, 10, "completed",
        )
        assert db.query(ToolAction).filter_by(step_id="media_baseline").one().status == "COMPLETED"
        assert stage_hosted_recording(
            db, run, "worker-a", 1, output, "baseline", tmp_path
        )
        assert run.media_status == "READY"
    assert dispatch_one_hosted_media(factory, config, tmp_path, client) is None
    with factory() as db:
        event = db.get(OutboxEvent, media_event_id("run-a", "baseline"))
        event.status = "pending"
        db.commit()
    assert dispatch_one_hosted_media(
        factory, config, tmp_path, client, event_id=media_event_id("run-a", "baseline"),
    ) == media_event_id("run-a", "baseline")
    with factory() as db:
        assert db.query(MediaMinuteCharge).count() == 1
    assert client.puts == 5
    assert encode_calls == ["run-a_baseline"]
    engine.dispose()
