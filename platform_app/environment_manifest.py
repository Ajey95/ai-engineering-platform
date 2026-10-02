"""Versioned, bounded environment contract for one authorized web repository."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_NAME = re.compile(r"[a-z][a-z0-9_-]{0,39}\Z")
_VERSION = re.compile(r"[0-9]+(?:\.[0-9]+){0,2}\Z")
_ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")


class EnvironmentManifestError(ValueError):
    pass


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    argv: tuple[str, ...] = Field(min_length=1, max_length=32)
    timeout_seconds: int = Field(ge=1, le=300)

    @field_validator("argv")
    @classmethod
    def bounded_argv(cls, argv: tuple[str, ...]) -> tuple[str, ...]:
        if any(
            not part or "\x00" in part or len(part.encode("utf-8")) > 2048
            for part in argv
        ) or sum(len(part.encode("utf-8")) for part in argv) > 8192:
            raise ValueError("Command arguments exceed policy")
        return argv


class LocalService(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    command: Command
    port: int = Field(ge=1024, le=65535)
    health_path: str = Field(min_length=1, max_length=200)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        if not _NAME.fullmatch(value):
            raise ValueError("Service name is invalid")
        return value

    @field_validator("health_path")
    @classmethod
    def valid_health_path(cls, value: str) -> str:
        if not value.startswith("/") or value.startswith("//") or "\\" in value:
            raise ValueError("Health path must be local")
        parsed = urlsplit(value)
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError("Health path cannot change origin")
        return value


class BrowserStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action: Literal["goto", "fill", "click", "expect_text"]
    path: str | None = None
    role: str | None = None
    name: str | None = None
    value: str | None = None
    text: str | None = None

    @model_validator(mode="after")
    def valid_step(self) -> "BrowserStep":
        provided = {key for key in ("path", "role", "name", "value", "text")
                    if getattr(self, key) is not None}
        required = {
            "goto": {"path"}, "fill": {"role", "name", "value"},
            "click": {"role", "name"}, "expect_text": {"text"},
        }[self.action]
        if provided != required:
            raise ValueError("Browser step fields do not match the action")
        if self.path is not None and (
            not self.path.startswith("/") or self.path.startswith("//")
            or "\\" in self.path or len(self.path) > 300
        ):
            raise ValueError("Browser path must stay on the local origin")
        if any(len(value) > 500 for value in (self.role, self.name, self.value, self.text)
               if value is not None):
            raise ValueError("Browser step text exceeds policy")
        return self


class BrowserScenario(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    steps: tuple[BrowserStep, ...] = Field(min_length=1, max_length=100)
    mask_selectors: tuple[str, ...] = Field(default=(), max_length=20)

    @field_validator("mask_selectors")
    @classmethod
    def bounded_masks(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value or len(value) > 300 for value in values):
            raise ValueError("Mask selector exceeds policy")
        return values


class EnvironmentManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str
    language: str
    python_version: str | None = None
    node_version: str | None = None
    install: tuple[Command, ...] = Field(default=(), max_length=6)
    build: tuple[Command, ...] = Field(default=(), max_length=6)
    services: tuple[LocalService, ...] = Field(min_length=1, max_length=4)
    named_tests: dict[str, Command] = Field(min_length=1, max_length=20)
    browser_scenario: BrowserScenario
    fixture_setup: tuple[Command, ...] = Field(default=(), max_length=4)
    postgres_fixture: bool = False
    external_destinations: tuple[str, ...] = Field(default=(), max_length=20)
    environment_keys: tuple[str, ...] = Field(default=(), max_length=40)
    max_runtime_seconds: int = Field(default=1800, ge=60, le=1800)

    @model_validator(mode="after")
    def validate_contract(self) -> "EnvironmentManifest":
        if self.schema_version != "1.0" or self.language not in {
            "python", "node", "python-node"
        }:
            raise ValueError("Environment schema or language is unsupported")
        needs_python = "python" in self.language
        needs_node = "node" in self.language
        if (self.python_version is not None) != needs_python:
            raise ValueError("Python runtime declaration is inconsistent")
        if (self.node_version is not None) != needs_node:
            raise ValueError("Node runtime declaration is inconsistent")
        if any(
            version is not None and not _VERSION.fullmatch(version)
            for version in (self.python_version, self.node_version)
        ):
            raise ValueError("Runtime version must be pinned")
        names = [service.name for service in self.services]
        ports = [service.port for service in self.services]
        if len(names) != len(set(names)) or len(ports) != len(set(ports)):
            raise ValueError("Service names and ports must be unique")
        if "app" not in names:
            raise ValueError("An app service is required")
        if any(not _NAME.fullmatch(name) for name in self.named_tests):
            raise ValueError("Named test identifier is invalid")
        if any(not _ENV_NAME.fullmatch(name) or name.startswith("AIP_")
               for name in self.environment_keys):
            raise ValueError("Environment key is invalid or reserved")
        if len(self.environment_keys) != len(set(self.environment_keys)):
            raise ValueError("Environment keys must be unique")
        destinations = [canonical_destination(value) for value in self.external_destinations]
        if len(destinations) != len(set(destinations)):
            raise ValueError("External destinations must be unique")
        return self

    def digest(self) -> str:
        payload = self.model_dump(mode="json")
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(raw).hexdigest()

    def runner_manifest(self, case_id: str) -> dict:
        """Translate approved fields to the existing sandbox browser/test runners."""
        if not _NAME.fullmatch(case_id):
            raise ValueError("Case identifier is invalid")
        app = next(service for service in self.services if service.name == "app")
        origin = f"http://127.0.0.1:{app.port}"
        return {
            "schema_version": "1.0", "case_id": case_id,
            "fixture_revision": self.digest(), "oracle_revision": "none",
            "start_command": list(app.command.argv),
            "startup_timeout_seconds": app.command.timeout_seconds,
            "health_url": origin + app.health_path,
            "allowed_origin": origin,
            "require_instance_header": False,
            "named_tests": {
                name: list(command.argv) for name, command in self.named_tests.items()
            },
            "named_test_timeouts": {
                name: command.timeout_seconds for name, command in self.named_tests.items()
            },
            "scenario": self.browser_scenario.model_dump(mode="json"),
        }


def canonical_destination(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https" or not parsed.hostname
        or parsed.username or parsed.password or parsed.path not in {"", "/"}
        or parsed.query or parsed.fragment
        or parsed.port not in {None, 443}
    ):
        raise ValueError("External destination must be an HTTPS origin")
    hostname = parsed.hostname.rstrip(".").lower()
    if (
        hostname != parsed.hostname.lower()
        or not re.fullmatch(r"[a-z0-9.-]+", hostname)
        or "." not in hostname
        or any(not label or label.startswith("-") or label.endswith("-")
               for label in hostname.split("."))
        or hostname.endswith((".local", ".internal", ".localhost"))
    ):
        raise ValueError("External destination host is invalid")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise ValueError("IP destinations are not allowed")
    return f"https://{hostname}"


def authorize_environment_destinations(
    manifest: EnvironmentManifest, approved_origins: set[str]
) -> None:
    canonical_approved = {canonical_destination(value) for value in approved_origins}
    missing = {canonical_destination(value) for value in manifest.external_destinations} - (
        canonical_approved
    )
    if missing:
        raise EnvironmentManifestError("Manifest requests an unapproved network destination")
