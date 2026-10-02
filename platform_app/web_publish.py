"""Publish a verified static build without exposing a partial release."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path

from botocore.exceptions import ClientError


class WebPublishError(Exception):
    pass


MAX_BUILD_BYTES = 50_000_000
MAX_FILE_BYTES = 10_000_000
_REVISION = re.compile(r"[0-9a-f]{40}\Z")
_ASSET = re.compile(r"assets/[A-Za-z0-9_./-]+\Z")
_REFERENCED = re.compile(r'''(?:src|href)=["']/(assets/[^"']+)["']''')


def _digest(value: bytes) -> str:
    return base64.b64encode(hashlib.sha256(value).digest()).decode("ascii")


def _head(client, bucket: str, key: str) -> dict | None:
    try:
        return client.head_object(Bucket=bucket, Key=key, ChecksumMode="ENABLED")
    except KeyError:  # Controlled in-memory store.
        return None
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
            return None
        raise


def _put_immutable(client, bucket: str, key: str, value: bytes, content_type: str) -> None:
    existing = _head(client, bucket, key)
    if existing is not None:
        if existing.get("ChecksumSHA256") == _digest(value) and (
            existing.get("ContentLength") == len(value)
        ):
            return
        raise WebPublishError(f"Immutable asset differs: {key}")
    try:
        client.put_object(
            Bucket=bucket, Key=key, Body=value, ContentType=content_type,
            CacheControl="public, max-age=31536000, immutable",
            ServerSideEncryption="AES256", ChecksumSHA256=_digest(value),
            IfNoneMatch="*",
        )
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") not in {"412", "PreconditionFailed"}:
            raise
    verified = _head(client, bucket, key)
    if verified is None or verified.get("ChecksumSHA256") != _digest(value) or (
        verified.get("ContentLength") != len(value)
    ):
        raise WebPublishError(f"Uploaded asset could not be verified: {key}")


def _build_files(dist: Path) -> tuple[bytes, list[tuple[str, bytes, str]]]:
    if dist.is_symlink() or dist.is_junction():
        raise WebPublishError("Build directory is linked")
    root = dist.resolve(strict=True)
    if not root.is_dir() or root.is_symlink() or root.is_junction():
        raise WebPublishError("Build directory is not regular")
    index_path = root / "index.html"
    if not index_path.is_file() or index_path.is_symlink():
        raise WebPublishError("Build index.html is missing")
    index = index_path.read_bytes()
    if not index or len(index) > MAX_FILE_BYTES:
        raise WebPublishError("Build index.html exceeds its limit")
    try:
        html = index.decode("utf-8")
    except UnicodeDecodeError as error:
        raise WebPublishError("Build index.html is not UTF-8") from error
    assets: list[tuple[str, bytes, str]] = []
    total = len(index)
    for selected in sorted(root.rglob("*")):
        if selected.is_symlink() or selected.is_junction():
            raise WebPublishError("Build contains a linked path")
        if selected.is_dir():
            continue
        if not selected.is_file() or not selected.resolve().is_relative_to(root):
            raise WebPublishError("Build contains a nonregular path")
        relative = selected.relative_to(root).as_posix()
        if relative == "index.html":
            continue
        if not _ASSET.fullmatch(relative) or ".." in Path(relative).parts:
            raise WebPublishError("Build contains an unexpected path")
        size = selected.stat().st_size
        if size <= 0 or size > MAX_FILE_BYTES or total + size > MAX_BUILD_BYTES:
            raise WebPublishError("Build exceeds deployment limits")
        value = selected.read_bytes()
        if len(value) != size or total + len(value) > MAX_BUILD_BYTES:
            raise WebPublishError("Build changed during validation")
        total += len(value)
        content_type = {
            ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml",
            ".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp",
            ".woff": "font/woff", ".woff2": "font/woff2", ".json": "application/json",
        }.get(selected.suffix)
        if content_type is None:
            raise WebPublishError(f"Unexpected build asset type: {relative}")
        assets.append((relative, value, content_type))
    references = set(_REFERENCED.findall(html))
    names = {name for name, _, _ in assets}
    if (
        not references or not references <= names
        or not any(name.endswith(".js") for name in references)
    ):
        raise WebPublishError("Index references missing compiled assets")
    return index, assets


def publish_web_build(client, bucket: str, dist: Path, revision: str) -> dict:
    """Upload checked assets and a release snapshot before a conditional index swap."""
    if not _REVISION.fullmatch(revision):
        raise WebPublishError("Release revision must be a full Git commit")
    index, assets = _build_files(dist)
    manifest = json.dumps({
        "revision": revision,
        "index_sha256": hashlib.sha256(index).hexdigest(),
        "assets": [
            {"path": name, "sha256": hashlib.sha256(value).hexdigest(), "bytes": len(value)}
            for name, value, _ in assets
        ],
    }, sort_keys=True, separators=(",", ":")).encode("utf-8")
    prior = _head(client, bucket, "index.html")
    for name, value, content_type in assets:
        _put_immutable(client, bucket, name, value, content_type)
    release_prefix = f"releases/{revision}/"
    _put_immutable(client, bucket, release_prefix + "index.html", index, "text/html")
    _put_immutable(client, bucket, release_prefix + "manifest.json", manifest,
                   "application/json")
    condition = {"IfMatch": prior["ETag"]} if prior is not None else {"IfNoneMatch": "*"}
    try:
        client.put_object(
            Bucket=bucket, Key="index.html", Body=index, ContentType="text/html",
            CacheControl="no-store", ServerSideEncryption="AES256",
            ChecksumSHA256=_digest(index), **condition,
        )
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") in {"412", "PreconditionFailed"}:
            raise WebPublishError("Another release changed index.html") from error
        raise
    verified = _head(client, bucket, "index.html")
    if verified is None or verified.get("ChecksumSHA256") != _digest(index):
        raise WebPublishError("Published index.html could not be verified")
    return {
        "revision": revision, "asset_count": len(assets),
        "index_sha256": hashlib.sha256(index).hexdigest(),
        "manifest_sha256": hashlib.sha256(manifest).hexdigest(),
    }
