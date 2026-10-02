import hashlib

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from platform_app.db import Base
from platform_app.models import PluginRegistryEvent, Tenant
from platform_app.plugin_registry import (
    PluginError,
    PluginManifest,
    enable_plugin,
    register_plugin,
    resolve_plugin_tool,
    validate_plugin_artifact,
)


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Tenant(id="tenant-a", name="A", plugin_allowlist=[]))
        session.commit()
        yield session
    engine.dispose()


def manifest(artifact: bytes, **changes) -> PluginManifest:
    raw = {
        "plugin_id": "example.scoped-tool", "version": "1.2.0",
        "contract_version": "1.0", "publisher": "Reviewed team",
        "artifact_sha256": hashlib.sha256(artifact).hexdigest(),
        "category": "retrieval", "transport": "isolated_container",
        "endpoint_origin": None,
        "tools": [{
            "name": "read_fact", "input_schema": {
                "type": "object", "properties": {"fact_id": {"type": "string"}},
                "required": ["fact_id"], "additionalProperties": False,
            },
            "output_schema": {"type": "object"},
            "permission_scopes": ["memory.read"], "side_effect_class": "read",
        }],
        "allowed_network_destinations": [], "credential_types": [],
        "max_runtime_seconds": 10, "max_output_bytes": 4096,
        "compatibility_constraints": {"platform_contract": "1.0"},
    }
    raw.update(changes)
    return PluginManifest.model_validate(raw)


def test_manifest_digest_lifecycle_and_tenant_tool_resolution(db, tmp_path):
    artifact = b"reviewed isolated tool bundle"
    path = tmp_path / "plugin.tar"
    path.write_bytes(artifact)
    entry = register_plugin(db, manifest(artifact), "operator")
    with pytest.raises(PluginError) as early:
        enable_plugin(db, entry, "operator")
    assert early.value.code == "PLUGIN_STATE"
    with pytest.raises(PluginError) as duplicate:
        register_plugin(db, manifest(artifact), "operator")
    assert duplicate.value.code == "PLUGIN_EXISTS"
    validate_plugin_artifact(db, entry, path, "operator")
    enable_plugin(db, entry, "operator")
    tenant = db.get(Tenant, "tenant-a")
    with pytest.raises(PluginError) as denied:
        resolve_plugin_tool(db, tenant, entry.plugin_id, entry.version, "read_fact", {}, set())
    assert denied.value.code == "PLUGIN_DENIED"
    tenant.plugin_allowlist = ["example.scoped-tool@1.2.0"]
    with pytest.raises(PluginError) as scope:
        resolve_plugin_tool(
            db, tenant, entry.plugin_id, entry.version, "read_fact", {}, set()
        )
    assert scope.value.code == "PLUGIN_DENIED"
    with pytest.raises(PluginError) as arguments:
        resolve_plugin_tool(
            db, tenant, entry.plugin_id, entry.version, "read_fact", {}, {"memory.read"}
        )
    assert arguments.value.code == "PLUGIN_ARGUMENTS_INVALID"
    resolved = resolve_plugin_tool(
        db, tenant, entry.plugin_id, entry.version, "read_fact",
        {"fact_id": "fact-a"}, {"memory.read"},
    )
    assert resolved.artifact_sha256 == hashlib.sha256(artifact).hexdigest()
    assert resolved.max_output_bytes == 4096
    assert len(db.scalars(select(PluginRegistryEvent)).all()) == 3


def test_tampered_artifact_and_manifest_fail_closed(db, tmp_path):
    artifact = b"reviewed"
    path = tmp_path / "plugin.tar"
    path.write_bytes(b"tampered")
    entry = register_plugin(db, manifest(artifact), "operator")
    with pytest.raises(PluginError) as wrong_file:
        validate_plugin_artifact(db, entry, path, "operator")
    assert wrong_file.value.code == "PLUGIN_ARTIFACT_INVALID"
    path.write_bytes(artifact)
    validate_plugin_artifact(db, entry, path, "operator")
    enable_plugin(db, entry, "operator")
    db.get(Tenant, "tenant-a").plugin_allowlist = ["example.scoped-tool@1.2.0"]
    entry.manifest = {**entry.manifest, "max_output_bytes": 10_000_000}
    with pytest.raises(PluginError) as changed:
        resolve_plugin_tool(
            db, db.get(Tenant, "tenant-a"), entry.plugin_id, entry.version,
            "read_fact", {"fact_id": "fact-a"}, {"memory.read"},
        )
    assert changed.value.code == "PLUGIN_ARTIFACT_INVALID"


def test_remote_endpoint_must_be_allowlisted_and_cannot_be_enabled(db, tmp_path):
    artifact = b"remote manifest review"
    with pytest.raises(ValidationError):
        manifest(
            artifact, transport="mcp_http", endpoint_origin="http://127.0.0.1:9000",
            allowed_network_destinations=["http://127.0.0.1:9000"],
        )
    with pytest.raises(ValidationError):
        manifest(
            artifact, transport="mcp_http", endpoint_origin="https://tools.example.com",
            allowed_network_destinations=["https://other.example.com"],
        )
    unsafe = manifest(artifact).model_dump()
    unsafe["tools"][0]["input_schema"] = {"$ref": "https://metadata.example/schema.json"}
    with pytest.raises(ValidationError):
        PluginManifest.model_validate(unsafe)
    reviewed = manifest(
        artifact, transport="mcp_http", endpoint_origin="https://tools.example.com",
        allowed_network_destinations=["https://tools.example.com"],
    )
    path = tmp_path / "remote-review.json"
    path.write_bytes(artifact)
    entry = register_plugin(db, reviewed, "operator")
    validate_plugin_artifact(db, entry, path, "operator")
    with pytest.raises(PluginError) as unavailable:
        enable_plugin(db, entry, "operator")
    assert unavailable.value.code == "PLUGIN_TRANSPORT_UNAVAILABLE"
