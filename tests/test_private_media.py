import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from botocore.exceptions import ClientError
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from platform_app import api
from platform_app.artifact_quota import artifact_usage_bytes, reserve_artifact
from platform_app.browser_auth import SESSION_COOKIE, csrf_for
from platform_app.config import Settings
from platform_app.db import Base
from platform_app.models import (
    ArtifactCharge,
    BrowserSession,
    OutboxEvent,
    PrivateMediaPublication,
    Project,
    ProjectMembership,
    RecordingDeletion,
    Run,
    Task,
    Tenant,
    TenantMembership,
    ToolAction,
)
from platform_app.private_media import media_prefix, sign_recording_grant
from platform_app.private_media_deletion import process_private_media_deletion
from platform_app.private_media_store import publication_inventory, publish_recording
from platform_app.recording_deletion import reconcile_local_recording_deletions
from platform_app.service import ServiceError
from scripts.dispatch_private_media_deletions import pending_event_ids


def _config() -> tuple[Settings, rsa.RSAPrivateKey]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return Settings(
        environment="production",
        database_url="postgresql+psycopg://unused:unused@localhost/unused",
        oidc_issuer="https://issuer.example.test/", oidc_audience="aip-api",
        oidc_jwks_url="https://issuer.example.test/keys",
        public_base_url="https://app.example.test",
        browser_session_secret="s" * 48,
        private_media_bucket="private-media.example.test",
        cloudfront_key_pair_id="KTEST123",
        cloudfront_private_key_b64=base64.b64encode(pem).decode(),
    ), key


def _decode_cookie(value: str) -> bytes:
    return base64.b64decode(value.translate(str.maketrans("-_~", "+=/")))


def _scope(db: Session, *, effect: str = "a" * 64) -> Run:
    db.add(Tenant(id="tenant-a", name="A"))
    db.add(Project(id="project-a", tenant_id="tenant-a", name="A"))
    db.add(Task(
        id="task-a", tenant_id="tenant-a", project_id="project-a",
        report="bug", expected_behavior="works", actual_behavior="broken", created_by="alice",
    ))
    run = Run(
        id="run-a", tenant_id="tenant-a", project_id="project-a", task_id="task-a",
        created_by="alice", idempotency_key="key-a", request_hash="h" * 64,
        base_commit="a" * 40, model_entry_id="model-a", state="REVIEW_READY",
        config_snapshot={"policy_version": "1.0"}, media_status="READY",
    )
    db.add(run)
    db.flush()
    return run


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.uploads = 0
        self.fail_after = None

    def put_object(self, **args):
        if self.fail_after is not None and self.uploads >= self.fail_after:
            raise ClientError({"Error": {"Code": "ServiceUnavailable"}}, "PutObject")
        self.uploads += 1
        self.objects[args["Key"]] = args

    def head_object(self, **args):
        value = self.objects[args["Key"]]
        return {"ChecksumSHA256": value["ChecksumSHA256"], "ContentLength": len(value["Body"])}

    def list_objects_v2(self, **args):
        return {"Contents": [
            {"Key": key} for key in self.objects if key.startswith(args["Prefix"])
        ]}

    def delete_objects(self, **args):
        for item in args["Delete"]["Objects"]:
            self.objects.pop(item["Key"], None)
        return {"Errors": []}


class FakeCloudFront:
    def __init__(self):
        self.status = "InProgress"
        self.calls = []

    def create_invalidation(self, **args):
        self.calls.append(args)
        return {"Invalidation": {"Id": "INV123"}}

    def get_invalidation(self, **args):
        return {"Invalidation": {"Status": self.status}}


def test_cloudfront_grant_is_path_scoped_signed_and_five_minutes():
    config, key = _config()
    prefix = media_prefix("tenant-a", "project-a", "run-a", "baseline", "a" * 64)
    now = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
    grant = sign_recording_grant(config, prefix, now=now)
    assert grant.manifest_url == f"/{prefix}master.m3u8"
    assert grant.prefix == f"/{prefix}"
    assert grant.expires_at == now + timedelta(minutes=5)
    policy = _decode_cookie(grant.cookies["CloudFront-Policy"])
    signature = _decode_cookie(grant.cookies["CloudFront-Signature"])
    key.public_key().verify(signature, policy, padding.PKCS1v15(), hashes.SHA256())
    statement = json.loads(policy)["Statement"][0]
    assert statement["Resource"] == f"https://app.example.test/{prefix}*"
    assert statement["Condition"]["DateLessThan"]["AWS:EpochTime"] == int(
        grant.expires_at.timestamp()
    )
    assert grant.cookies["CloudFront-Hash-Algorithm"] == "SHA256"
    with pytest.raises(ValueError):
        sign_recording_grant(config, "private-media/*", now=now)


def test_private_publication_uploads_verifies_and_replays(tmp_path):
    config, _ = _config()
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        run = _scope(db)
        effect = "a" * 64
        relative = Path("private-media") / "tenant-a" / "run-a_baseline" / "media" / effect
        root = tmp_path / relative
        variant = root / "low"
        variant.mkdir(parents=True)
        (root / "master.m3u8").write_text(
            "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=350000\nlow/index.m3u8\n"
        )
        (variant / "index.m3u8").write_text(
            '#EXTM3U\n#EXT-X-MAP:URI="init.mp4"\n#EXTINF:2.0,\n'
            'segment_0000.m4s\n#EXT-X-ENDLIST\n'
        )
        (variant / "init.mp4").write_bytes(b"init")
        (variant / "segment_0000.m4s").write_bytes(b"segment")
        db.add(ToolAction(
            tenant_id="tenant-a", run_id="run-a", step_id="media_baseline",
            logical_action="media.encode", effect_key="b" * 64,
            arguments_hash="c" * 64, policy_result="allowed", status="COMPLETED",
            receipt={"status": "READY", "master": str(relative / "master.m3u8")},
        ))
        db.commit()
        client = FakeS3()
        master = root / "master.m3u8"
        valid_master = master.read_text()
        master.write_text(valid_master.replace("low/index.m3u8", "https://elsewhere.test/a"))
        with pytest.raises(ServiceError) as escaped:
            publication_inventory(db, run, "baseline", tmp_path)
        assert escaped.value.code == "MEDIA_NOT_READY"
        master.write_text(valid_master)
        playlist = variant / "index.m3u8"
        valid_playlist = playlist.read_text()
        playlist.write_text(valid_playlist + '#EXT-X-KEY:METHOD=AES-128,URI="https://elsewhere.test/key"\n')
        with pytest.raises(ServiceError) as injected:
            publication_inventory(db, run, "baseline", tmp_path)
        assert injected.value.code == "MEDIA_NOT_READY"
        playlist.write_text(valid_playlist)
        digest, byte_count = publication_inventory(db, run, "baseline", tmp_path)
        reserve_artifact(db, run.id, "private_media", "baseline", digest, byte_count)
        db.commit()
        client.fail_after = 2
        with pytest.raises(ClientError):
            publish_recording(db, config, run, "baseline", tmp_path, client)
        db.rollback()
        assert db.query(PrivateMediaPublication).count() == 0
        assert db.query(ArtifactCharge).one().status == "reserved"
        assert client.uploads == 2
        assert not any(key.endswith("manifest.json") for key in client.objects)
        client.fail_after = None
        publish_recording(db, config, run, "baseline", tmp_path, client)
        assert client.uploads == 5
        db.rollback()  # Simulate a crash after S3 completed but before the DB commit.
        assert db.query(PrivateMediaPublication).count() == 0
        assert db.query(ArtifactCharge).one().status == "reserved"
        publication = publish_recording(db, config, run, "baseline", tmp_path, client)
        db.commit()
        assert publication.status == "ready"
        assert artifact_usage_bytes(db, run.tenant_id) == byte_count
        assert publication.object_count == 5
        assert client.uploads == 5
        prefix = media_prefix("tenant-a", "project-a", "run-a", "baseline", effect)
        assert set(client.objects) == {
            prefix + name for name in (
                "master.m3u8", "low/index.m3u8", "low/init.mp4",
                "low/segment_0000.m4s", "manifest.json",
            )
        }
        for item in client.objects.values():
            assert item["ServerSideEncryption"] == "AES256"
            assert item["IfNoneMatch"] == "*"
            assert item["ChecksumSHA256"] == base64.b64encode(
                hashlib.sha256(item["Body"]).digest()
            ).decode()
        again = publish_recording(db, config, run, "baseline", tmp_path / "gone", client)
        assert again.id == publication.id and client.uploads == 5
        client.objects[prefix + "manifest.json"]["ChecksumSHA256"] = "tampered"
        with pytest.raises(ServiceError) as changed:
            publish_recording(db, config, run, "baseline", tmp_path, client)
        assert changed.value.code == "MEDIA_UPLOAD_UNVERIFIED"
        del client.objects[prefix + "manifest.json"]
        with pytest.raises(ServiceError) as missing:
            publish_recording(db, config, run, "baseline", tmp_path, client)
        assert missing.value.code == "MEDIA_UPLOAD_UNVERIFIED"
    engine.dispose()


def test_recording_grant_requires_session_scope_and_denies_deleted(tmp_path, monkeypatch):
    config, _ = _config()
    monkeypatch.setattr(api, "settings", lambda: config)
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        _scope(db)
        db.add_all([
            TenantMembership(tenant_id="tenant-a", subject="alice", role="owner"),
            Tenant(id="tenant-b", name="B"),
            TenantMembership(tenant_id="tenant-b", subject="bob", role="owner"),
            ProjectMembership(
                tenant_id="tenant-a", project_id="project-a", subject="alice", role="reviewer"
            ),
            PrivateMediaPublication(
                tenant_id="tenant-a", project_id="project-a", run_id="run-a",
                label="baseline", effect_hash="a" * 64,
                manifest_sha256="b" * 64, object_count=5, byte_count=100,
                status="ready",
            ),
        ])
        token = "test-session-token"
        db.add(BrowserSession(
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            tenant_id="tenant-a", subject="alice", created_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ))
        other_token = "other-session-token"
        db.add(BrowserSession(
            token_hash=hashlib.sha256(other_token.encode()).hexdigest(),
            tenant_id="tenant-b", subject="bob", created_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ))
        db.commit()

        def session():
            yield db

        api.app.dependency_overrides[api.db_session] = session
        try:
            client = TestClient(api.app, base_url="https://app.example.test")
            client.cookies.set(SESSION_COOKIE, token)
            url = "/v1/runs/run-a/recordings/baseline/grant"
            assert client.post(url).status_code == 403
            response = client.post(url, headers={
                "Origin": config.public_base_url, "X-AIP-CSRF": csrf_for(token, config),
            })
            assert response.status_code == 200, response.text
            assert response.json()["manifest_url"].endswith("/master.m3u8")
            headers = response.headers.get_list("set-cookie")
            assert len(headers) == 4
            assert all("Secure" in item and "HttpOnly" in item for item in headers)
            assert all("Max-Age" not in item for item in headers)
            assert client.post(
                "/v1/runs/run-a/recordings/candidate/grant",
                headers={"X-AIP-CSRF": csrf_for(token, config)},
            ).status_code == 404
            other = TestClient(api.app, base_url="https://app.example.test")
            other.cookies.set(SESSION_COOKIE, other_token)
            assert other.post(
                url, headers={"X-AIP-CSRF": csrf_for(other_token, config)},
            ).status_code == 404
            deleted = client.delete(
                "/v1/runs/run-a/recordings/baseline",
                headers={"Origin": config.public_base_url,
                         "X-AIP-CSRF": csrf_for(token, config)},
            )
            assert deleted.status_code == 202
            assert deleted.json()["status"] == "pending"
            assert db.query(OutboxEvent).filter_by(topic="private_media.delete").count() == 1
            assert client.post(
                url, headers={"X-AIP-CSRF": csrf_for(token, config)},
            ).status_code == 404
        finally:
            api.app.dependency_overrides.clear()
    engine.dispose()


def test_remote_deletion_waits_for_edge_invalidation_and_survives_local_reconcile(tmp_path):
    config, _ = _config()
    config = config.model_copy(update={
        "artifact_dir": str(tmp_path), "cloudfront_distribution_id": "EDIST12345",
    })
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        run = _scope(db)
        publication = PrivateMediaPublication(
            tenant_id=run.tenant_id, project_id=run.project_id, run_id=run.id,
            label="baseline", effect_hash="a" * 64, manifest_sha256="b" * 64,
            object_count=2, byte_count=100, status="ready",
        )
        deletion = RecordingDeletion(
            tenant_id=run.tenant_id, run_id=run.id, label="baseline",
            actor="alice", status="pending",
        )
        event = OutboxEvent(
            tenant_id=run.tenant_id, topic="private_media.delete",
            payload={"run_id": run.id, "label": "baseline"}, status="pending",
        )
        db.add_all([publication, deletion, event, ArtifactCharge(
            tenant_id=run.tenant_id, run_id=run.id,
            kind="private_media", logical_key="baseline", sha256="b" * 64,
            byte_count=100, status="active",
        )])
        db.commit()
        assert artifact_usage_bytes(db, run.tenant_id) == 100
        result = reconcile_local_recording_deletions(lambda: Session(engine), str(tmp_path))
        assert result["cleaned"] == 0 and deletion.status == "pending"
        prefix = media_prefix(run.tenant_id, run.project_id, run.id, "baseline", "a" * 64)
        s3 = FakeS3()
        for name in ("master.m3u8", "low/index.m3u8"):
            s3.objects[prefix + name] = {"Key": prefix + name, "Body": b"a"}
        cloudfront = FakeCloudFront()
        assert process_private_media_deletion(db, config, event, s3, cloudfront) is False
        db.commit()
        assert not s3.objects
        assert event.status == "pending" and deletion.status == "pending"
        assert publication.status == "ready"
        assert artifact_usage_bytes(db, run.tenant_id) == 100
        cloudfront.status = "Completed"
        assert process_private_media_deletion(db, config, event, s3, cloudfront) is True
        db.commit()
        assert event.status == "delivered" and deletion.status == "complete"
        assert publication.status == "deleted"
        assert artifact_usage_bytes(db, run.tenant_id) == 0
        assert run.media_status == "PARTIALLY_DELETED"
        assert cloudfront.calls[0]["InvalidationBatch"]["Paths"]["Items"] == [
            f"/{prefix}*"
        ]
        assert all(
            call["InvalidationBatch"]["CallerReference"] == deletion.id
            for call in cloudfront.calls
        )
    engine.dispose()


def test_private_deletion_dispatch_prioritizes_untried_events():
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all([
            OutboxEvent(
                id="old-failing", tenant_id="tenant-a", topic="private_media.delete",
                payload={}, status="pending", attempts=3,
            ),
            OutboxEvent(
                id="new-pending", tenant_id="tenant-a", topic="private_media.delete",
                payload={}, status="pending", attempts=0,
            ),
            OutboxEvent(
                id="other-topic", tenant_id="tenant-a", topic="run.dispatch",
                payload={}, status="pending", attempts=0,
            ),
        ])
        db.commit()
        assert pending_event_ids(db) == ["new-pending", "old-failing"]
    engine.dispose()
