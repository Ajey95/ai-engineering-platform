"""Publish a complete HLS recording to a private, checksummed object prefix."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path

from botocore.exceptions import ClientError
from sqlalchemy import select
from sqlalchemy.orm import Session

from platform_app.artifact_quota import activate_artifact, artifact_charge
from platform_app.config import Settings
from platform_app.models import PrivateMediaPublication, Run, ToolAction
from platform_app.private_media import media_prefix
from platform_app.recording_deletion import deletion_for
from platform_app.service import ServiceError, append_event

_BUCKET = re.compile(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]\Z")
_VARIANT = re.compile(r"(low|medium|high|source)/(index\.m3u8|init\.mp4|segment_[0-9]{4}\.m4s)\Z")
_CONTENT_TYPES = {
    ".m3u8": "application/vnd.apple.mpegurl",
    ".mp4": "video/mp4",
    ".m4s": "video/iso.segment",
    ".json": "application/json",
}
MAX_OBJECTS = 999
MAX_BYTES = 500_000_000
MAX_OBJECT_BYTES = 50_000_000


def _collect_objects(
    run: Run, label: str, artifact_root: Path, master_ref: str, effect_hash: str,
) -> tuple[list[tuple[str, bytes, str]], bytes]:
    root = artifact_root.resolve(strict=True)
    expected = root / "private-media" / run.tenant_id / f"{run.id}_{label}" / "media"
    expected_master = expected / effect_hash / "master.m3u8"
    referenced = root / master_ref
    if (
        referenced.resolve() != expected_master.resolve()
        or not expected_master.resolve().is_relative_to(root)
        or any(
            part.is_symlink() or part.is_junction()
            for part in (expected, expected / effect_hash, expected_master)
        )
    ):
        raise ServiceError("MEDIA_NOT_READY", "Recording manifest escaped its scope", 409)
    directory = expected_master.parent
    if directory.is_symlink() or not directory.is_dir():
        raise ServiceError("MEDIA_NOT_READY", "Recording directory is unavailable", 409)
    files: list[tuple[str, bytes, str]] = []
    total_bytes = 0
    for file in sorted(directory.rglob("*")):
        if file.is_symlink() or file.is_junction():
            raise ServiceError("MEDIA_NOT_READY", "Recording contains a linked path", 409)
        if not file.is_file():
            continue
        relative = file.relative_to(directory).as_posix()
        if relative != "master.m3u8" and not _VARIANT.fullmatch(relative):
            raise ServiceError("MEDIA_NOT_READY", "Recording contains an unexpected object", 409)
        size = file.stat().st_size
        if size <= 0 or size > MAX_OBJECT_BYTES or total_bytes + size > MAX_BYTES:
            raise ServiceError("MEDIA_LIMIT", "Recording exceeds object store limits", 413)
        value = file.read_bytes()
        total_bytes += len(value)
        files.append((relative, value, _CONTENT_TYPES[file.suffix]))
        if len(files) > MAX_OBJECTS or total_bytes > MAX_BYTES:
            raise ServiceError("MEDIA_LIMIT", "Recording exceeds object store limits", 413)
    if len(files) < 4 or not any(name == "master.m3u8" for name, _, _ in files):
        raise ServiceError("MEDIA_NOT_READY", "Recording files are incomplete", 409)
    manifest = json.dumps({
        "schema_version": "1.0", "effect_hash": effect_hash,
        "objects": [
            {"name": name, "sha256": _checksum(value)[0], "bytes": len(value)}
            for name, value, _ in files
        ],
    }, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(manifest) > MAX_OBJECT_BYTES or total_bytes + len(manifest) > MAX_BYTES:
        raise ServiceError("MEDIA_LIMIT", "Recording exceeds object store limits", 413)
    return files, manifest


def publication_inventory(
    db: Session, run: Run, label: str, artifact_root: Path,
) -> tuple[str, int]:
    """Return the exact manifest digest and bytes used by publication."""
    if label not in {"baseline", "candidate"}:
        raise ServiceError("INVALID_MEDIA_LABEL", "Unknown recording side", 400)
    if deletion_for(db, run, label) is not None:
        raise ServiceError("RECORDING_DELETED", "Recording access was revoked", 409)
    action = db.scalar(select(ToolAction).where(
        ToolAction.tenant_id == run.tenant_id,
        ToolAction.run_id == run.id,
        ToolAction.step_id == f"media_{label}",
        ToolAction.status == "COMPLETED",
    ))
    receipt = (action.receipt or {}) if action else {}
    master_ref = receipt.get("master")
    if receipt.get("status") != "READY" or not isinstance(master_ref, str):
        raise ServiceError("MEDIA_NOT_READY", "Recording has no complete encode", 409)
    parts = Path(master_ref).parts
    if len(parts) < 2 or parts[-1] != "master.m3u8":
        raise ServiceError("MEDIA_NOT_READY", "Recording manifest reference is invalid", 409)
    files, manifest = _collect_objects(run, label, artifact_root, master_ref, parts[-2])
    return _checksum(manifest)[0], sum(len(value) for _, value, _ in files) + len(manifest)


def _checksum(value: bytes) -> tuple[str, str]:
    digest = hashlib.sha256(value).digest()
    return digest.hex(), base64.b64encode(digest).decode("ascii")


def _upload(client, config: Settings, key: str, value: bytes, content_type: str) -> None:
    _, checksum = _checksum(value)
    try:
        head = client.head_object(
            Bucket=config.private_media_bucket, Key=key, ChecksumMode="ENABLED"
        )
    except KeyError:  # Controlled in-memory store.
        head = None
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code", "")
        if code not in {"404", "NoSuchKey", "NotFound"}:
            raise
        head = None
    if head is not None:
        if head.get("ChecksumSHA256") == checksum and head.get("ContentLength") == len(value):
            return
        raise ServiceError("MEDIA_UPLOAD_CONFLICT", "Private object already differs", 409)
    args = {
        "Bucket": config.private_media_bucket,
        "Key": key,
        "Body": value,
        "ContentType": content_type,
        "CacheControl": "max-age=31536000, immutable",
        "ChecksumSHA256": checksum,
        "IfNoneMatch": "*",
        "ServerSideEncryption": "aws:kms" if config.private_media_kms_key_id else "AES256",
    }
    if config.private_media_kms_key_id:
        args["SSEKMSKeyId"] = config.private_media_kms_key_id
    try:
        client.put_object(**args)
    except ClientError as error:
        # Another publisher may have won the conditional create. Verify its bytes.
        code = error.response.get("Error", {}).get("Code", "")
        if code not in {"412", "PreconditionFailed"}:
            raise
    head = client.head_object(
        Bucket=config.private_media_bucket, Key=key, ChecksumMode="ENABLED"
    )
    if head.get("ChecksumSHA256") != checksum or head.get("ContentLength") != len(value):
        raise ServiceError("MEDIA_UPLOAD_UNVERIFIED", "Private object checksum differs", 503)


def publish_recording(
    db: Session, config: Settings, run: Run, label: str, artifact_root: Path, client,
) -> PrivateMediaPublication:
    """Upload immutable files, then make the scoped recording visible in PostgreSQL."""
    if (
        not _BUCKET.fullmatch(config.private_media_bucket)
        or ".." in config.private_media_bucket
        or ".-" in config.private_media_bucket
        or "-." in config.private_media_bucket
    ):
        raise ServiceError("MEDIA_STORE_UNAVAILABLE", "Private media bucket is invalid", 503)
    if label not in {"baseline", "candidate"}:
        raise ServiceError("INVALID_MEDIA_LABEL", "Unknown recording side", 400)
    # Keep deletion serialized with the entire upload. A revocation cannot
    # complete while this publisher can still write objects to its prefix.
    locked = db.scalar(
        select(Run).where(Run.id == run.id).with_for_update()
        .execution_options(populate_existing=True)
    )
    if locked is None or locked.tenant_id != run.tenant_id:
        raise ServiceError("MEDIA_NOT_READY", "Recording run is unavailable", 409)
    run = locked
    if deletion_for(db, run, label) is not None:
        raise ServiceError("RECORDING_DELETED", "Recording access was revoked", 409)
    action = db.scalar(select(ToolAction).where(
        ToolAction.tenant_id == run.tenant_id,
        ToolAction.run_id == run.id,
        ToolAction.step_id == f"media_{label}",
        ToolAction.status == "COMPLETED",
    ))
    receipt = (action.receipt or {}) if action else {}
    master_ref = receipt.get("master")
    if receipt.get("status") != "READY" or not isinstance(master_ref, str):
        raise ServiceError("MEDIA_NOT_READY", "Recording has no complete encode", 409)
    parts = Path(master_ref).parts
    if len(parts) < 2 or parts[-1] != "master.m3u8":
        raise ServiceError("MEDIA_NOT_READY", "Recording manifest reference is invalid", 409)
    effect_hash = parts[-2]
    try:
        prefix = media_prefix(run.tenant_id, run.project_id, run.id, label, effect_hash)
    except ValueError as error:
        raise ServiceError("MEDIA_NOT_READY", "Recording effect is invalid", 409) from error
    existing = db.scalar(select(PrivateMediaPublication).where(
        PrivateMediaPublication.tenant_id == run.tenant_id,
        PrivateMediaPublication.run_id == run.id,
        PrivateMediaPublication.label == label,
    ))
    if existing is not None:
        if existing.status != "ready" or existing.effect_hash != effect_hash:
            raise ServiceError("MEDIA_PUBLICATION_CONFLICT", "Recording publication changed", 409)
        try:
            head = client.head_object(
                Bucket=config.private_media_bucket, Key=prefix + "manifest.json",
                ChecksumMode="ENABLED",
            )
        except (ClientError, KeyError) as error:
            raise ServiceError(
                "MEDIA_UPLOAD_UNVERIFIED", "Published manifest is unavailable", 503
            ) from error
        try:
            expected = base64.b64encode(bytes.fromhex(existing.manifest_sha256)).decode("ascii")
        except ValueError as error:
            raise ServiceError(
                "MEDIA_UPLOAD_UNVERIFIED", "Manifest digest is invalid", 503
            ) from error
        if head.get("ChecksumSHA256") != expected:
            raise ServiceError("MEDIA_UPLOAD_UNVERIFIED", "Published manifest is unavailable", 503)
        return existing
    files, manifest = _collect_objects(run, label, artifact_root, master_ref, effect_hash)
    manifest_sha256 = _checksum(manifest)[0]
    byte_count = sum(len(value) for _, value, _ in files) + len(manifest)
    charge = artifact_charge(db, run, "private_media", label, manifest_sha256, byte_count)
    for name, value, content_type in files:
        _upload(client, config, prefix + name, value, content_type)
    _upload(client, config, prefix + "manifest.json", manifest, _CONTENT_TYPES[".json"])
    if deletion_for(db, locked, label):
        raise ServiceError("RECORDING_DELETED", "Recording was revoked during upload", 409)
    concurrent = db.scalar(select(PrivateMediaPublication).where(
        PrivateMediaPublication.tenant_id == run.tenant_id,
        PrivateMediaPublication.run_id == run.id,
        PrivateMediaPublication.label == label,
    ).execution_options(populate_existing=True))
    if concurrent is not None:
        if concurrent.status != "ready" or concurrent.effect_hash != effect_hash:
            raise ServiceError("MEDIA_PUBLICATION_CONFLICT", "Recording publication changed", 409)
        activate_artifact(db, charge)
        return concurrent
    publication = PrivateMediaPublication(
        tenant_id=run.tenant_id, project_id=run.project_id, run_id=run.id,
        label=label, effect_hash=effect_hash,
        manifest_sha256=manifest_sha256,
        object_count=len(files) + 1, byte_count=byte_count, status="ready",
    )
    db.add(publication)
    activate_artifact(db, charge)
    append_event(db, locked, "media.published", {
        "label": label, "effect_hash": effect_hash,
        "manifest_sha256": publication.manifest_sha256,
        "object_count": publication.object_count,
    })
    return publication
