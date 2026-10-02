"""Private, single-object S3 capabilities for an isolated sandbox guest."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

from botocore.exceptions import ClientError


class SandboxTransportError(ValueError):
    pass


_ID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")


def _sha256_base64(data: bytes) -> str:
    return base64.b64encode(hashlib.sha256(data).digest()).decode("ascii")


def _precondition_failed(error: ClientError) -> bool:
    return error.response.get("Error", {}).get("Code") in {
        "PreconditionFailed", "ConditionalRequestConflict", "412", "409"
    }


@dataclass(frozen=True)
class SandboxObjectKeys:
    source: str
    ready: str
    go: str
    result: str

    @classmethod
    def scoped(
        cls, tenant_id: str, project_id: str, run_id: str, lease_id: str
    ) -> "SandboxObjectKeys":
        if not all(_ID.fullmatch(value) for value in (
            tenant_id, project_id, run_id, lease_id
        )):
            raise SandboxTransportError("Sandbox object scope is invalid")
        suffix = f"{tenant_id}/{project_id}/{run_id}/{lease_id}"
        return cls(
            source=f"sandbox/in/{suffix}/source.tar",
            ready=f"sandbox/out/{suffix}/ready.json",
            go=f"sandbox/in/{suffix}/go.json",
            result=f"sandbox/out/{suffix}/result.json",
        )


@dataclass(frozen=True)
class GuestUrls:
    source_get: str
    ready_put: str
    go_get: str
    result_put: str


def stage_source_archive(
    s3, bucket: str, keys: SandboxObjectKeys, archive: bytes,
    *, max_bytes: int = 50_000_000,
) -> str:
    """Commit immutable source before handing the guest a read capability."""
    if not archive or len(archive) > max_bytes:
        raise SandboxTransportError("Sandbox source archive exceeds policy")
    digest = hashlib.sha256(archive).hexdigest()
    checksum = _sha256_base64(archive)
    try:
        s3.put_object(
            Bucket=bucket, Key=keys.source, Body=archive,
            IfNoneMatch="*", ServerSideEncryption="AES256",
            ChecksumSHA256=checksum, ContentType="application/x-tar",
        )
    except ClientError as error:
        if not _precondition_failed(error):
            raise
    head = s3.head_object(Bucket=bucket, Key=keys.source, ChecksumMode="ENABLED")
    if head.get("ContentLength") != len(archive) or head.get("ChecksumSHA256") != checksum:
        raise SandboxTransportError("Staged sandbox source differs from the pinned archive")
    return digest


def _presign(s3, method: str, bucket: str, key: str, ttl: int, *, put: bool) -> str:
    params = {"Bucket": bucket, "Key": key}
    if put:
        params.update({
            "IfNoneMatch": "*", "ServerSideEncryption": "AES256",
            "ContentType": "application/json",
        })
    url = s3.generate_presigned_url(
        method, Params=params, ExpiresIn=ttl,
        HttpMethod="PUT" if put else "GET",
    )
    parsed = urlsplit(url)
    signed_headers = parse_qs(parsed.query).get("X-Amz-SignedHeaders", [""])[0]
    if (
        parsed.scheme != "https" or not parsed.hostname
        or parsed.username or parsed.password or parsed.fragment
        or (put and not {
            "content-type", "host", "if-none-match", "x-amz-server-side-encryption"
        }
            <= set(signed_headers.split(";")))
        or "X-Amz-Signature" not in parse_qs(parsed.query)
    ):
        raise SandboxTransportError("S3 did not issue a constrained SigV4 HTTPS URL")
    return url


def issue_guest_urls(
    s3, bucket: str, keys: SandboxObjectKeys, *, ttl_seconds: int = 900
) -> GuestUrls:
    """URLs are bearer capabilities; callers must encrypt their bootstrap copy."""
    if not 60 <= ttl_seconds <= 1800:
        raise SandboxTransportError("Guest URL lifetime is outside policy")
    return GuestUrls(
        source_get=_presign(s3, "get_object", bucket, keys.source, ttl_seconds, put=False),
        ready_put=_presign(s3, "put_object", bucket, keys.ready, ttl_seconds, put=True),
        go_get=_presign(s3, "get_object", bucket, keys.go, ttl_seconds, put=False),
        result_put=_presign(s3, "put_object", bucket, keys.result, ttl_seconds, put=True),
    )


def publish_guest_go(
    s3, bucket: str, keys: SandboxObjectKeys, lease_id: str,
    fence: int, source_sha256: str,
) -> str:
    """Idempotent go marker is written only after the EC2 metadata endpoint is sealed."""
    if not _ID.fullmatch(lease_id) or fence < 1 or not re.fullmatch(
        r"[0-9a-f]{64}", source_sha256
    ):
        raise SandboxTransportError("Guest activation identity is invalid")
    body = json.dumps({
        "version": 1, "lease_id": lease_id, "fence": fence,
        "source_sha256": source_sha256,
    }, sort_keys=True, separators=(",", ":")).encode()
    checksum = _sha256_base64(body)
    try:
        s3.put_object(
            Bucket=bucket, Key=keys.go, Body=body, IfNoneMatch="*",
            ServerSideEncryption="AES256", ChecksumSHA256=checksum,
            ContentType="application/json",
        )
    except ClientError as error:
        if not _precondition_failed(error):
            raise
    head = s3.head_object(Bucket=bucket, Key=keys.go, ChecksumMode="ENABLED")
    if head.get("ContentLength") != len(body) or head.get("ChecksumSHA256") != checksum:
        raise SandboxTransportError("Guest activation marker conflicts with this lease")
    return hashlib.sha256(body).hexdigest()


def fence_guest_outputs(s3, bucket: str, keys: SandboxObjectKeys) -> None:
    """Occupy unused conditional-PUT slots, making their signed URLs unusable."""
    for key in (keys.ready, keys.result):
        try:
            s3.put_object(
                Bucket=bucket, Key=key, Body=b"", IfNoneMatch="*",
                ServerSideEncryption="AES256", ContentType="application/json",
            )
        except ClientError as error:
            if not _precondition_failed(error):
                raise
