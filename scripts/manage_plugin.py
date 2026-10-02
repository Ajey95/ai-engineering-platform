"""Register, validate and allowlist pinned plugin versions from an operator shell."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from platform_app.models import AuditEvent, PluginEntry, Tenant
from platform_app.plugin_registry import (
    PluginError,
    PluginManifest,
    disable_plugin,
    enable_plugin,
    register_plugin,
    validate_plugin_artifact,
)
from platform_app.service import canonical_hash


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operator", required=True)
    sub = parser.add_subparsers(dest="action", required=True)
    registration = sub.add_parser("register")
    registration.add_argument("--manifest", required=True, type=Path)
    for action in ("validate", "enable", "disable"):
        item = sub.add_parser(action)
        item.add_argument("--plugin-id", required=True)
        item.add_argument("--version", required=True)
        if action == "validate":
            item.add_argument("--artifact", required=True, type=Path)
    allowlist = sub.add_parser("allowlist")
    allowlist.add_argument("--tenant-id", required=True)
    allowlist.add_argument("--entry", action="append", default=[])
    args = parser.parse_args()
    if not 1 <= len(args.operator) <= 200:
        parser.error("Operator identity must be 1 to 200 characters")
    url = os.environ.get("AIP_DATABASE_URL", "")
    if not url.startswith("postgresql+psycopg://"):
        parser.error("AIP_DATABASE_URL must point to migrated PostgreSQL")
    engine = create_engine(url, pool_pre_ping=True)
    try:
        with Session(engine) as db, db.begin():
            if args.action == "register":
                try:
                    manifest = PluginManifest.model_validate_json(
                        args.manifest.read_text(encoding="utf-8")
                    )
                except (OSError, ValidationError) as error:
                    parser.error(f"Manifest is invalid: {type(error).__name__}")
                register_plugin(db, manifest, args.operator)
            elif args.action == "allowlist":
                tenant = db.scalar(
                    select(Tenant).where(Tenant.id == args.tenant_id).with_for_update()
                )
                if tenant is None:
                    parser.error("Tenant not found")
                requested = sorted(set(args.entry))
                if len(requested) != len(args.entry):
                    parser.error("Duplicate plugin version in allowlist")
                for item in requested:
                    if item.count("@") != 1:
                        parser.error("Allowlist entries must be plugin_id@version")
                    plugin_id, version = item.split("@", 1)
                    found = db.scalar(select(PluginEntry.id).where(
                        PluginEntry.plugin_id == plugin_id,
                        PluginEntry.version == version,
                    ))
                    if found is None:
                        parser.error("Allowlist references an unregistered plugin version")
                before = tenant.plugin_allowlist or []
                if before != requested:
                    tenant.plugin_allowlist = requested
                    db.add(AuditEvent(
                        tenant_id=tenant.id, actor=args.operator,
                        action="tenant.plugins.update", target_ref=tenant.id,
                        arguments_hash=canonical_hash({"before": before, "after": requested}),
                        policy_revision=tenant.policy_revision, outcome="allowed",
                    ))
            else:
                entry = db.scalar(
                    select(PluginEntry).where(
                        PluginEntry.plugin_id == args.plugin_id,
                        PluginEntry.version == args.version,
                    ).with_for_update()
                )
                if entry is None:
                    parser.error("Plugin version not found")
                if args.action == "validate":
                    validate_plugin_artifact(db, entry, args.artifact, args.operator)
                elif args.action == "enable":
                    enable_plugin(db, entry, args.operator)
                else:
                    disable_plugin(db, entry, args.operator)
        print(f"Plugin {args.action} recorded")
        return 0
    except PluginError as error:
        parser.error(f"Plugin operation denied: {error.code}")
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
