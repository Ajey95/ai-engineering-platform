"""Scoped object paths and CloudFront custom-policy grants for private HLS."""

from __future__ import annotations

import base64
import binascii
import ipaddress
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from platform_app.config import Settings

GRANT_MINUTES = 5
_ID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
COOKIE_NAMES = (
    "CloudFront-Policy", "CloudFront-Signature",
    "CloudFront-Key-Pair-Id", "CloudFront-Hash-Algorithm",
)


def media_prefix(
    tenant_id: str, project_id: str, run_id: str, label: str, effect_hash: str,
) -> str:
    if (
        not all(_ID.fullmatch(value) for value in (tenant_id, project_id, run_id))
        or label not in {"baseline", "candidate"}
        or not _DIGEST.fullmatch(effect_hash)
    ):
        raise ValueError("Private media scope is invalid")
    return f"private-media/{tenant_id}/{project_id}/{run_id}/{label}/{effect_hash}/"


def _cookie_base64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii").translate(str.maketrans("+=/", "-_~"))


@dataclass(frozen=True)
class CdnGrant:
    manifest_url: str
    prefix: str
    expires_at: datetime
    cookies: dict[str, str]


def sign_recording_grant(
    config: Settings, prefix: str, *, now: datetime | None = None,
) -> CdnGrant:
    """Grant exactly one immutable recording path for five minutes."""
    parsed = urlsplit(config.public_base_url)
    if (
        parsed.scheme != "https" or not parsed.hostname or parsed.username
        or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}
        or parsed.port not in {None, 443}
    ):
        raise ValueError("Private media requires a reviewed HTTPS public origin")
    hostname = parsed.hostname.lower()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise ValueError("Private media requires a public DNS hostname")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise ValueError("Private media requires a public DNS hostname")
    if not re.fullmatch(r"[A-Za-z0-9]{1,128}", config.cloudfront_key_pair_id):
        raise ValueError("CloudFront key pair ID is missing")
    if not re.fullmatch(
        r"private-media/[A-Za-z0-9_-]{1,64}/[A-Za-z0-9_-]{1,64}/"
        r"[A-Za-z0-9_-]{1,64}/(baseline|candidate)/[0-9a-f]{64}/", prefix,
    ):
        raise ValueError("Private media prefix is invalid")
    try:
        pem = base64.b64decode(config.cloudfront_private_key_b64, validate=True)
        key = serialization.load_pem_private_key(pem, password=None)
    except (ValueError, TypeError, binascii.Error, UnsupportedAlgorithm) as error:
        raise ValueError("CloudFront signing key is unavailable") from error
    if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048:
        raise ValueError("CloudFront signing key must be RSA 2048 or stronger")
    origin = f"https://{hostname}"
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("Grant time must include a timezone")
    expires_at = now + timedelta(minutes=GRANT_MINUTES)
    resource = f"{origin}/{prefix}*"
    policy = json.dumps({
        "Statement": [{
            "Resource": resource,
            "Condition": {"DateLessThan": {"AWS:EpochTime": int(expires_at.timestamp())}},
        }],
    }, separators=(",", ":")).encode("utf-8")
    signature = key.sign(policy, padding.PKCS1v15(), hashes.SHA256())
    return CdnGrant(
        manifest_url=f"/{prefix}master.m3u8", prefix=f"/{prefix}", expires_at=expires_at,
        cookies={
            "CloudFront-Policy": _cookie_base64(policy),
            "CloudFront-Signature": _cookie_base64(signature),
            "CloudFront-Key-Pair-Id": config.cloudfront_key_pair_id,
            "CloudFront-Hash-Algorithm": "SHA256",
        },
    )
