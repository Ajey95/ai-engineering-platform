"""Hosted identity and project membership must gate tenant records at the API boundary."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from platform_app import api, auth, run_ledger, service
from platform_app.config import Settings
from platform_app.db import Base
from platform_app.models import (
    AuditEvent,
    PluginEntry,
    Project,
    ProjectMembership,
    Run,
    Task,
    Tenant,
    TenantMembership,
)
from platform_app.plugin_registry import PluginManifest, manifest_digest
from platform_app.service import ServiceError


@pytest.fixture
def hosted(monkeypatch):
    config = Settings(
        environment="production",
        database_url="postgresql+psycopg://unused:unused@localhost/unused",
        oidc_issuer="https://issuer.example.test/",
        oidc_audience="aip-api",
        oidc_jwks_url="https://issuer.example.test/keys",
    )
    for module in (api, auth, run_ledger, service):
        monkeypatch.setattr(module, "settings", lambda: config)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(
        auth,
        "_jwks_client",
        lambda _: SimpleNamespace(
            get_signing_key_from_jwt=lambda _token: SimpleNamespace(key=key.public_key())
        ),
    )
    return config, key


def token(config, key, subject="alice", **overrides):
    now = datetime.now(UTC)
    claims = {
        "iss": config.oidc_issuer,
        "aud": config.oidc_audience,
        "sub": subject,
        "iat": now,
        "exp": now + timedelta(minutes=10),
    }
    claims.update(overrides)
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test-key"})


def test_oidc_rejects_wrong_claims_and_algorithm(hosted):
    config, key = hosted
    assert auth.verify_bearer(token(config, key), config) == "alice"
    for claims in (
        {"aud": "another-api"},
        {"iss": "https://wrong.test/"},
        {"exp": datetime.now(UTC) - timedelta(minutes=2)},
    ):
        with pytest.raises(ServiceError) as rejected:
            auth.verify_bearer(token(config, key, **claims), config)
        assert rejected.value.status == 401
    with pytest.raises(ServiceError) as rejected:
        auth.verify_bearer(jwt.encode({"sub": "alice"}, "s" * 32, algorithm="HS256"), config)
    assert rejected.value.status == 401


def test_hosted_project_roles_and_tenant_selection(hosted, monkeypatch):
    config, key = hosted
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(api, "SessionLocal", sessionmaker(bind=engine))
    with Session(engine) as db:
        plugin_manifest = PluginManifest.model_validate({
            "plugin_id": "example.read-tool", "version": "1.0.0",
            "contract_version": "1.0", "publisher": "Reviewed team",
            "artifact_sha256": "a" * 64, "category": "retrieval",
            "transport": "internal", "tools": [{
                "name": "read_fact", "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
                "permission_scopes": ["memory.read"], "side_effect_class": "read",
            }], "allowed_network_destinations": [], "credential_types": [],
            "max_runtime_seconds": 10, "max_output_bytes": 4096,
            "compatibility_constraints": {},
        })
        db.add_all(
            [
                Tenant(id="tenant-a", name="A"),
                Tenant(id="tenant-b", name="B"),
                TenantMembership(tenant_id="tenant-a", subject="alice", role="member"),
                TenantMembership(tenant_id="tenant-a", subject="bob", role="member"),
                TenantMembership(tenant_id="tenant-a", subject="owner", role="owner"),
                PluginEntry(
                    plugin_id=plugin_manifest.plugin_id, version=plugin_manifest.version,
                    manifest=plugin_manifest.model_dump(mode="json"),
                    manifest_sha256=manifest_digest(plugin_manifest),
                    artifact_sha256=plugin_manifest.artifact_sha256,
                    state="enabled", validated_at=datetime.now(UTC),
                ),
                Project(id="project-a", tenant_id="tenant-a", name="Allowed"),
                Project(id="project-b", tenant_id="tenant-a", name="Hidden"),
                Project(id="project-c", tenant_id="tenant-b", name="Other tenant"),
                ProjectMembership(
                    tenant_id="tenant-a", project_id="project-a", subject="alice", role="viewer"
                ),
                ProjectMembership(
                    tenant_id="tenant-a", project_id="project-a", subject="bob", role="contributor"
                ),
                Task(
                    id="task-a",
                    tenant_id="tenant-a",
                    project_id="project-a",
                    report="A real report",
                    expected_behavior="pass",
                    actual_behavior="fail",
                    created_by="bob",
                ),
                Run(
                    id="paused-run", tenant_id="tenant-a", project_id="project-a",
                    task_id="task-a", created_by="bob", idempotency_key="initial-run-key",
                    request_hash="r" * 64, base_commit="a" * 40, model_entry_id="model",
                    state="PAUSED_INPUT", resume_target="PREPARING",
                    config_snapshot={"policy_version": "1.0"},
                ),
            ]
        )
        db.commit()

        def test_db():
            yield db

        api.app.dependency_overrides[api.db_session] = test_db
        try:
            client = TestClient(api.app)
            alice = {"Authorization": f"Bearer {token(config, key)}", "X-Tenant-ID": "tenant-a"}
            bob = {
                "Authorization": f"Bearer {token(config, key, 'bob')}",
                "X-Tenant-ID": "tenant-a",
            }
            owner = {
                "Authorization": f"Bearer {token(config, key, 'owner')}",
                "X-Tenant-ID": "tenant-a",
            }
            assert client.get("/v1/projects", headers=alice).json()[0]["id"] == "project-a"
            assert len(client.get("/v1/projects", headers=alice).json()) == 1
            assert client.get("/v1/tasks?project_id=project-b", headers=alice).status_code == 404
            assert (
                client.get("/v1/projects", headers={**alice, "X-Tenant-ID": "tenant-b"}).status_code
                == 404
            )
            assert client.get("/v1/projects").status_code == 401
            assert client.get("/v1/runs/paused-run/events").status_code == 401
            assert client.get(
                "/v1/runs/paused-run/events",
                headers={**alice, "X-Tenant-ID": "tenant-b"},
            ).status_code == 404
            body = {
                "project_id": "project-a",
                "report": "Another valid report",
                "expected_behavior": "pass",
                "actual_behavior": "fail",
            }
            assert client.post("/v1/tasks", headers=alice, json=body).status_code == 404
            assert client.post("/v1/tasks", headers=bob, json=body).status_code == 201
            assert (
                client.post("/v1/projects", headers=bob, json={"name": "No permission"}).status_code
                == 403
            )
            assert client.put(
                "/v1/memberships/owner", headers=owner,
                json={"role": "member", "status": "disabled"},
            ).status_code == 409
            assert client.put(
                "/v1/projects/project-a/members/alice", headers=owner,
                json={"role": "contributor"},
            ).status_code == 200
            assert client.post("/v1/tasks", headers=alice, json=body).status_code == 201
            assert client.put(
                "/v1/projects/project-a/members/intruder", headers=owner,
                json={"role": "viewer"},
            ).status_code == 404
            assert client.get("/v1/tenant/quotas", headers=bob).status_code == 403
            assert client.get("/v1/tenant/plugins", headers=bob).status_code == 403
            assert client.get("/v1/tenant/plugins", headers=owner).json()[0]["ready"] is True
            assert client.put(
                "/v1/tenant/plugins/example.tool/1.0.0", headers=bob,
                json={"allowed": True, "reason": "Approve reviewed plugin"},
            ).status_code == 403
            plugin_path = "/v1/tenant/plugins/example.read-tool/1.0.0"
            assert client.put(
                plugin_path, headers=owner,
                json={"allowed": True, "reason": "Approve read-only plugin"},
            ).status_code == 200
            assert client.get("/v1/tenant/plugins", headers=owner).json()[0]["allowed"] is True
            assert client.put(
                plugin_path, headers=owner,
                json={"allowed": False, "reason": "Revoke plugin access"},
            ).status_code == 200
            assert client.get("/v1/tenant/plugins", headers=owner).json()[0]["allowed"] is False
            assert client.put("/v1/tenant/quotas", headers=bob, json={
                "daily_inference_cap_usd": "5", "monthly_inference_cap_usd": "50",
                "max_concurrent_runs": 2, "daily_export_cap_bytes": 100_000,
                "daily_sandbox_minutes": 60,
                "reason": "Approved pilot ceiling",
            }).status_code == 403
            quotas = {
                "daily_inference_cap_usd": "5.000000",
                "monthly_inference_cap_usd": "50.000000",
                "max_concurrent_runs": 2, "daily_export_cap_bytes": 100_000,
                "daily_sandbox_minutes": 60,
                "reason": "Approved pilot ceiling",
            }
            assert client.put("/v1/tenant/quotas", headers=owner, json={
                **quotas, "monthly_inference_cap_usd": "4",
            }).status_code == 422
            saved = client.put("/v1/tenant/quotas", headers=owner, json=quotas)
            assert saved.status_code == 200
            assert saved.json()["daily_inference_cap_usd"] == "5.000000"
            assert client.get("/v1/tenant/quotas", headers=owner).json() == saved.json()
            assert client.put("/v1/tenant/quotas", headers=owner, json=quotas).status_code == 200
            assert len(db.scalars(select(AuditEvent).where(
                AuditEvent.action == "tenant.quotas.update",
            )).all()) == 1
            response = client.post(
                "/v1/tasks/task-a/runs",
                headers={**bob, "Idempotency-Key": "run-key-001"},
                json={"base_commit": "a" * 40, "selected_model_entry": "none"},
            )
            assert response.status_code == 503
            assert response.json()["code"] == "EXECUTION_UNAVAILABLE"
            resume = client.post(
                "/v1/runs/paused-run/resume",
                headers={**bob, "Idempotency-Key": "resume-key-001"},
                json={"input_text": "Use the valid form"},
            )
            assert resume.status_code == 503
            assert resume.json()["code"] == "EXECUTION_UNAVAILABLE"
        finally:
            api.app.dependency_overrides.clear()
    engine.dispose()
