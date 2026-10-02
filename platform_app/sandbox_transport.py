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
    evidence: str

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
            evidence=f"sandbox/out/{suffix}/evidence.tar",
        )


@dataclass(frozen=True)
class GuestUrls:
    source_get: str
    ready_put: str
    go_get: str
    result_put: str
    evidence_put: str


@dataclass(frozen=True)
class GuestOutput:
    result: dict
    evidence_archive: bytes


def _read_bounded_object(s3, bucket: str, key: str, limit: int) -> bytes | None:
    try:
        response = s3.get_object(Bucket=bucket, Key=key)
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") in {"NoSuchKey", "404"}:
            return None
        raise
    size = response.get("ContentLength")
    if type(size) is not int or not 1 <= size <= limit:
        raise SandboxTransportError("Guest output size is outside policy")
    data = response["Body"].read(limit + 1)
    if len(data) != size:
        raise SandboxTransportError("Guest output length changed")
    return data


def fetch_guest_output(
    s3, bucket: str, keys: SandboxObjectKeys, lease_id: str,
    fence: int, source_sha256: str, *, phase: str = "baseline",
) -> GuestOutput | None:
    """Verify guest transport bytes; callers must also check the DB lease fence."""
    raw = _read_bounded_object(s3, bucket, keys.result, 1_000_000)
    if raw is None:
        return None
    try:
        result = json.loads(raw)
    except (UnicodeDecodeError, ValueError) as error:
        raise SandboxTransportError("Guest result is malformed") from error
    if (
        not isinstance(result, dict)
        or result.get("version") != 1
        or result.get("lease_id") != lease_id
        or result.get("fence") != fence
        or result.get("source_sha256") != source_sha256
        or phase not in {"baseline", "candidate"}
        or result.get("phase") != phase
        or type(result.get("evidence_bytes")) is not int
        or not 1 <= result["evidence_bytes"] <= 50_000_000
        or not isinstance(result.get("evidence_sha256"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", result["evidence_sha256"])
        or (result.get("guest_exit_code") is not None
            and type(result["guest_exit_code"]) is not int)
        or (result.get(phase) is not None
            and not isinstance(result[phase], dict))
    ):
        raise SandboxTransportError("Guest result does not match its lease")
    evidence = _read_bounded_object(s3, bucket, keys.evidence, 50_000_000)
    if evidence is None:
        return None
    if len(evidence) != result["evidence_bytes"] or hashlib.sha256(
        evidence
    ).hexdigest() != result["evidence_sha256"]:
        raise SandboxTransportError("Guest evidence checksum differs")
    return GuestOutput(result=result, evidence_archive=evidence)


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


def _presign(
    s3, method: str, bucket: str, key: str, ttl: int, *, put: bool,
    content_type: str = "application/json",
) -> str:
    params = {"Bucket": bucket, "Key": key}
    if put:
        params.update({
            "IfNoneMatch": "*", "ServerSideEncryption": "AES256",
            "ContentType": content_type,
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
    s3, bucket: str, keys: SandboxObjectKeys, *, ttl_seconds: int = 1800
) -> GuestUrls:
    """URLs are bearer capabilities; callers must encrypt their bootstrap copy."""
    if not 60 <= ttl_seconds <= 1800:
        raise SandboxTransportError("Guest URL lifetime is outside policy")
    return GuestUrls(
        source_get=_presign(s3, "get_object", bucket, keys.source, ttl_seconds, put=False),
        ready_put=_presign(s3, "put_object", bucket, keys.ready, ttl_seconds, put=True),
        go_get=_presign(s3, "get_object", bucket, keys.go, ttl_seconds, put=False),
        result_put=_presign(s3, "put_object", bucket, keys.result, ttl_seconds, put=True),
        evidence_put=_presign(
            s3, "put_object", bucket, keys.evidence, ttl_seconds,
            put=True, content_type="application/x-tar",
        ),
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


def guest_ready(
    s3, bucket: str, keys: SandboxObjectKeys, lease_id: str,
    fence: int, source_sha256: str,
) -> bool:
    """Read only the bounded ready marker from this lease's private S3 key."""
    try:
        response = s3.get_object(Bucket=bucket, Key=keys.ready)
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") in {"NoSuchKey", "404"}:
            return False
        raise
    if not 1 <= response.get("ContentLength", 0) <= 1024:
        raise SandboxTransportError("Guest ready marker is empty or oversized")
    body = response["Body"].read(1025)
    if len(body) != response["ContentLength"]:
        raise SandboxTransportError("Guest ready marker length changed")
    try:
        marker = json.loads(body)
    except (UnicodeDecodeError, ValueError) as error:
        raise SandboxTransportError("Guest ready marker is malformed") from error
    if marker != {
        "version": 1, "lease_id": lease_id, "fence": fence,
        "source_sha256": source_sha256,
    }:
        raise SandboxTransportError("Guest ready marker does not match its lease")
    return True


def fence_guest_outputs(s3, bucket: str, keys: SandboxObjectKeys) -> None:
    """Occupy unused conditional-PUT slots, making their signed URLs unusable."""
    s3.delete_object(Bucket=bucket, Key=keys.go)
    for key in (keys.ready, keys.result, keys.evidence):
        try:
            s3.put_object(
                Bucket=bucket, Key=key, Body=b"", IfNoneMatch="*",
                ServerSideEncryption="AES256",
                ContentType=("application/x-tar" if key == keys.evidence else "application/json"),
            )
        except ClientError as error:
            if not _precondition_failed(error):
                raise
