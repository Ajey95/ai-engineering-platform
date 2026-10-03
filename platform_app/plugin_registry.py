"""Pinned tool plugin manifests and a deny-by-default resolution boundary."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator, SchemaError
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from platform_app.db import utcnow
from platform_app.models import AuditEvent, PluginEntry, PluginRegistryEvent, Tenant
from platform_app.service import canonical_hash

PLUGIN_ID = re.compile(r"^[a-z][a-z0-9._-]{2,99}$")
VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[a-zA-Z0-9.-]+)?$")
DIGEST = re.compile(r"^[0-9a-f]{64}$")
NAME = re.compile(r"^[a-z][a-z0-9_]{1,79}$")


class PluginError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _reject_schema_refs(value: object) -> None:
    if isinstance(value, dict):
        if any(key in value for key in ("$ref", "$dynamicRef", "$recursiveRef")):
            raise ValueError("Referenced schemas are not supported in reviewed tools")
        for child in value.values():
            _reject_schema_refs(child)
    elif isinstance(value, list):
        for child in value:
            _reject_schema_refs(child)


class PluginTool(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str
    input_schema: dict
    output_schema: dict
    permission_scopes: list[str] = Field(min_length=1, max_length=20)
    side_effect_class: str

    @model_validator(mode="after")
    def validate_tool(self):
        if not NAME.fullmatch(self.name):
            raise ValueError("Tool name is invalid")
        if self.side_effect_class not in {
            "read", "workspace_write", "isolated_execution", "external_write", "publication"
        }:
            raise ValueError("Side effect class is invalid")
        if len(set(self.permission_scopes)) != len(self.permission_scopes) or any(
            not NAME.fullmatch(scope.replace(".", "_")) for scope in self.permission_scopes
        ):
            raise ValueError("Permission scope is invalid")
        _reject_schema_refs(self.input_schema)
        _reject_schema_refs(self.output_schema)
        try:
            Draft202012Validator.check_schema(self.input_schema)
            Draft202012Validator.check_schema(self.output_schema)
        except SchemaError as error:
            raise ValueError("Tool JSON schema is invalid") from error
        return self


def _https_origin(value: str) -> str:
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    if (
        parsed.scheme != "https" or not host or not parsed.netloc
        or parsed.username or parsed.password or parsed.path not in {"", "/"}
        or parsed.query or parsed.fragment
        or host == "localhost" or host.endswith((".local", ".internal", ".test"))
        or "." not in host
    ):
        raise ValueError("Network destination must be a public HTTPS origin")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("Network destination cannot be an IP address")
    if parsed.port not in {None, 443}:
        raise ValueError("Network destination must use HTTPS port 443")
    return f"https://{host.lower()}"


class PluginManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    plugin_id: str
    version: str
    contract_version: str
    publisher: str = Field(min_length=1, max_length=200)
    artifact_sha256: str
    category: str
    transport: str
    endpoint_origin: str | None = None
    tools: list[PluginTool] = Field(min_length=1, max_length=50)
    allowed_network_destinations: list[str] = Field(default_factory=list, max_length=20)
    credential_types: list[str] = Field(default_factory=list, max_length=10)
    max_runtime_seconds: int = Field(ge=1, le=300)
    max_output_bytes: int = Field(ge=1, le=10_000_000)
    compatibility_constraints: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_manifest(self):
        if not PLUGIN_ID.fullmatch(self.plugin_id) or not VERSION.fullmatch(self.version):
            raise ValueError("Plugin identity or version is invalid")
        if self.contract_version != "1.0" or not DIGEST.fullmatch(self.artifact_sha256):
            raise ValueError("Plugin contract or artifact digest is invalid")
        if self.category not in {
            "repository", "browser", "execution", "retrieval", "media", "external_knowledge"
        }:
            raise ValueError("Plugin category is invalid")
        if self.transport not in {"internal", "mcp_http", "isolated_container"}:
            raise ValueError("Plugin transport is invalid")
        names = [tool.name for tool in self.tools]
        if len(names) != len(set(names)):
            raise ValueError("Plugin tool names must be unique")
        if len(self.credential_types) != len(set(self.credential_types)) or any(
            item not in {"oauth2", "secret_ref", "workload_identity"}
            for item in self.credential_types
        ):
            raise ValueError("Credential type is invalid")
        origins = [_https_origin(item) for item in self.allowed_network_destinations]
        if len(origins) != len(set(origins)):
            raise ValueError("Network destination is duplicated")
        if self.transport == "mcp_http":
            if not self.endpoint_origin or _https_origin(self.endpoint_origin) not in origins:
                raise ValueError("MCP endpoint must be in the exact destination allowlist")
        elif self.endpoint_origin is not None:
            raise ValueError("Only remote MCP transport may declare an endpoint")
        if any(
            len(key) > 80 or len(value) > 200
            for key, value in self.compatibility_constraints.items()
        ):
            raise ValueError("Compatibility constraint is too long")
        return self


def manifest_digest(manifest: PluginManifest | dict) -> str:
    value = manifest.model_dump(mode="json") if isinstance(manifest, PluginManifest) else manifest
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _event(db: Session, entry: PluginEntry, actor: str, action: str, outcome: str) -> None:
    db.add(PluginRegistryEvent(
        plugin_entry_id=entry.id, actor=actor, action=action,
        outcome=outcome, manifest_sha256=entry.manifest_sha256,
    ))


def register_plugin(db: Session, manifest: PluginManifest, actor: str) -> PluginEntry:
    if not 1 <= len(actor) <= 200:
        raise PluginError("INVALID_OPERATOR", "Operator identity is required")
    existing = db.scalar(select(PluginEntry).where(
        PluginEntry.plugin_id == manifest.plugin_id,
        PluginEntry.version == manifest.version,
    ))
    if existing is not None:
        raise PluginError("PLUGIN_EXISTS", "Plugin version already exists")
    entry = PluginEntry(
        plugin_id=manifest.plugin_id,
        version=manifest.version,
        manifest=manifest.model_dump(mode="json"),
        manifest_sha256=manifest_digest(manifest),
        artifact_sha256=manifest.artifact_sha256,
        state="registered",
    )
    db.add(entry)
    db.flush()
    _event(db, entry, actor, "register", "registered")
    return entry


def validate_plugin_artifact(
    db: Session, entry: PluginEntry, artifact_path: Path, actor: str
) -> None:
    if entry.state != "registered":
        raise PluginError("PLUGIN_STATE", "Only registered plugin versions can be validated")
    try:
        manifest = PluginManifest.model_validate(entry.manifest)
        if (
            manifest_digest(manifest) != entry.manifest_sha256
            or manifest.artifact_sha256 != entry.artifact_sha256
        ):
            raise ValueError("Manifest changed")
        with artifact_path.open("rb") as artifact:
            digest = hashlib.file_digest(artifact, "sha256").hexdigest()
    except (OSError, ValueError) as error:
        raise PluginError("PLUGIN_ARTIFACT_INVALID", "Reviewed artifact is unavailable") from error
    if digest != entry.artifact_sha256:
        raise PluginError("PLUGIN_ARTIFACT_INVALID", "Reviewed artifact digest differs")
    entry.state = "validated"
    entry.validated_at = utcnow()
    _event(db, entry, actor, "validate", "validated")


def enable_plugin(db: Session, entry: PluginEntry, actor: str) -> None:
    if entry.state != "validated" or entry.validated_at is None:
        raise PluginError("PLUGIN_STATE", "Plugin version is not validated")
    manifest = PluginManifest.model_validate(entry.manifest)
    if manifest.transport == "mcp_http":
        raise PluginError("PLUGIN_TRANSPORT_UNAVAILABLE", "Remote MCP execution is not qualified")
    if (
        manifest_digest(manifest) != entry.manifest_sha256
        or manifest.artifact_sha256 != entry.artifact_sha256
    ):
        raise PluginError("PLUGIN_ARTIFACT_INVALID", "Manifest changed after validation")
    entry.state = "enabled"
    _event(db, entry, actor, "enable", "enabled")


def disable_plugin(db: Session, entry: PluginEntry, actor: str) -> None:
    if entry.state == "disabled":
        return
    entry.state = "disabled"
    _event(db, entry, actor, "disable", "disabled")


def tenant_plugin_catalog(db: Session, tenant: Tenant) -> list[dict]:
    allowed = set(tenant.plugin_allowlist or [])
    entries = db.scalars(select(PluginEntry).order_by(
        PluginEntry.plugin_id, PluginEntry.version,
    ).limit(500)).all()
    result = []
    for entry in entries:
        try:
            manifest = PluginManifest.model_validate(entry.manifest)
            intact = (
                manifest_digest(manifest) == entry.manifest_sha256
                and manifest.artifact_sha256 == entry.artifact_sha256
            )
        except ValueError:
            intact = False
            manifest = None
        result.append({
            "plugin_id": entry.plugin_id, "version": entry.version,
            "state": entry.state if intact else "invalid",
            "transport": manifest.transport if manifest else "unknown",
            "tools": [tool.name for tool in manifest.tools] if manifest else [],
            "allowed": f"{entry.plugin_id}@{entry.version}" in allowed,
            "ready": bool(
                intact and entry.state == "enabled" and entry.validated_at
                and manifest is not None and manifest.transport != "mcp_http"
            ),
        })
    return result


def set_tenant_plugin_access(
    db: Session, tenant_id: str, actor: str,
    plugin_id: str, version: str, allowed: bool, reason: str,
) -> dict:
    reason = reason.strip()
    if not 8 <= len(reason) <= 2000:
        raise PluginError("PLUGIN_REASON_REQUIRED", "A change reason is required")
    if not PLUGIN_ID.fullmatch(plugin_id) or not VERSION.fullmatch(version):
        raise PluginError("PLUGIN_ID_INVALID", "Plugin identity is invalid")
    tenant = db.scalar(select(Tenant).where(Tenant.id == tenant_id).with_for_update())
    if tenant is None or tenant.status != "active":
        raise PluginError("TENANT_DISABLED", "Tenant is unavailable")
    entry = db.scalar(select(PluginEntry).where(
        PluginEntry.plugin_id == plugin_id, PluginEntry.version == version,
    ).with_for_update())
    if entry is None:
        raise PluginError("PLUGIN_UNAVAILABLE", "Plugin version is unavailable")
    if allowed:
        try:
            manifest = PluginManifest.model_validate(entry.manifest)
        except ValueError as error:
            raise PluginError("PLUGIN_ARTIFACT_INVALID", "Plugin manifest is invalid") from error
        if (
            manifest_digest(manifest) != entry.manifest_sha256
            or manifest.artifact_sha256 != entry.artifact_sha256
        ):
            raise PluginError("PLUGIN_ARTIFACT_INVALID", "Plugin manifest changed")
        if manifest.transport == "mcp_http":
            raise PluginError("PLUGIN_TRANSPORT_UNAVAILABLE", "Remote MCP is not qualified")
        if entry.state != "enabled" or entry.validated_at is None:
            raise PluginError("PLUGIN_UNAVAILABLE", "Plugin version is not ready")
    key = f"{plugin_id}@{version}"
    before = sorted(set(tenant.plugin_allowlist or []))
    after = sorted((set(before) | {key}) if allowed else (set(before) - {key}))
    if before != after:
        tenant.plugin_allowlist = after
        db.add(AuditEvent(
            tenant_id=tenant_id, actor=actor, action="tenant.plugins.update",
            target_ref=key,
            arguments_hash=canonical_hash({
                "before": before, "after": after, "reason": reason,
            }),
            policy_revision=tenant.policy_revision, outcome="allowed",
        ))
    return {
        "plugin_id": plugin_id, "version": version,
        "allowed": allowed, "ready": entry.state == "enabled" and entry.validated_at is not None,
    }


@dataclass(frozen=True)
class ResolvedPluginTool:
    plugin_id: str
    version: str
    artifact_sha256: str
    tool: PluginTool
    transport: str
    max_runtime_seconds: int
    max_output_bytes: int


def resolve_plugin_tool(
    db: Session,
    tenant: Tenant,
    plugin_id: str,
    version: str,
    tool_name: str,
    arguments: dict,
    granted_scopes: set[str],
) -> ResolvedPluginTool:
    """Model output can select a reviewed tool but cannot install or enlarge it."""
    if f"{plugin_id}@{version}" not in (tenant.plugin_allowlist or []):
        raise PluginError("PLUGIN_DENIED", "Plugin version is not tenant allowlisted")
    entry = db.scalar(select(PluginEntry).where(
        PluginEntry.plugin_id == plugin_id, PluginEntry.version == version,
    ))
    if entry is None or entry.state != "enabled" or entry.validated_at is None:
        raise PluginError("PLUGIN_UNAVAILABLE", "Plugin version is not enabled")
    try:
        manifest = PluginManifest.model_validate(entry.manifest)
    except ValueError as error:
        raise PluginError("PLUGIN_ARTIFACT_INVALID", "Stored manifest is invalid") from error
    if (
        manifest_digest(manifest) != entry.manifest_sha256
        or manifest.artifact_sha256 != entry.artifact_sha256
    ):
        raise PluginError("PLUGIN_ARTIFACT_INVALID", "Pinned manifest or artifact changed")
    tool = next((item for item in manifest.tools if item.name == tool_name), None)
    if tool is None:
        raise PluginError("PLUGIN_TOOL_UNKNOWN", "Tool is not in the reviewed manifest")
    if not set(tool.permission_scopes).issubset(granted_scopes):
        raise PluginError("PLUGIN_DENIED", "Tool permission is not granted")
    if not isinstance(arguments, dict) or list(
        Draft202012Validator(tool.input_schema).iter_errors(arguments)
    ):
        raise PluginError("PLUGIN_ARGUMENTS_INVALID", "Tool arguments violate the schema")
    return ResolvedPluginTool(
        plugin_id, version, entry.artifact_sha256, tool, manifest.transport,
        manifest.max_runtime_seconds, manifest.max_output_bytes,
    )
