"""Guest bootstrap contract: stage source, wait for metadata seal, then execute."""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import re
import subprocess
import tarfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from platform_app.environment_manifest import EnvironmentManifest
from platform_app.safe_archive import UnsafeArchive, extract_regular_tar
from platform_app.sandbox_transport import GuestUrls


class GuestBootstrapError(ValueError):
    pass


_ID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")


def _s3_url(value: str) -> bool:
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    return (
        parsed.scheme == "https"
        and parsed.username is None and parsed.password is None
        and parsed.fragment == ""
        and host.endswith(".amazonaws.com")
        and (".s3." in host or host.startswith("s3."))
        and "X-Amz-Signature" in parse_qs(parsed.query)
    )


@dataclass(frozen=True)
class GuestBootstrapConfig:
    lease_id: str
    fence: int
    bundle_sha256: str
    manifest_sha256: str
    expires_at_epoch: int
    source_get: str
    ready_put: str
    go_get: str
    result_put: str
    evidence_put: str

    def __post_init__(self):
        if (
            not isinstance(self.lease_id, str) or not _ID.fullmatch(self.lease_id)
            or type(self.fence) is not int or self.fence < 1
            or not isinstance(self.bundle_sha256, str)
            or not _SHA.fullmatch(self.bundle_sha256)
            or not isinstance(self.manifest_sha256, str)
            or not _SHA.fullmatch(self.manifest_sha256)
            or type(self.expires_at_epoch) is not int or self.expires_at_epoch <= 0
            or not all(isinstance(value, str) and _s3_url(value) for value in (
                self.source_get, self.ready_put, self.go_get,
                self.result_put, self.evidence_put,
            ))
        ):
            raise GuestBootstrapError("Guest bootstrap scope or capability is invalid")

    def to_json(self) -> bytes:
        return json.dumps({"version": 1, **self.__dict__}, sort_keys=True,
                          separators=(",", ":")).encode()

    @classmethod
    def from_json(cls, raw: bytes) -> "GuestBootstrapConfig":
        if len(raw) > 12_000:
            raise GuestBootstrapError("Guest bootstrap exceeds policy")
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, ValueError) as error:
            raise GuestBootstrapError("Guest bootstrap is malformed") from error
        required = {
            "version", "lease_id", "fence", "bundle_sha256", "manifest_sha256",
            "expires_at_epoch",
            "source_get", "ready_put", "go_get", "result_put", "evidence_put",
        }
        if not isinstance(payload, dict) or set(payload) != required or payload["version"] != 1:
            raise GuestBootstrapError("Guest bootstrap schema is unsupported")
        return cls(**{key: value for key, value in payload.items() if key != "version"})


def render_user_data(
    urls: GuestUrls, lease_id: str, fence: int,
    bundle_sha256: str, manifest_sha256: str, expires_at_epoch: int,
) -> str:
    config = GuestBootstrapConfig(
        lease_id, fence, bundle_sha256, manifest_sha256, expires_at_epoch,
        urls.source_get, urls.ready_put, urls.go_get, urls.result_put,
        urls.evidence_put,
    )
    encoded = base64.b64encode(config.to_json()).decode("ascii")
    script = (
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        "umask 077\n"
        "install -d -m 0700 /run/aip\n"
        f"printf '%s' '{encoded}' | base64 -d > /run/aip/bootstrap.json\n"
        "exec /usr/bin/env PYTHONPATH=/opt/aip /opt/aip/.venv/bin/python "
        "-m platform_app.sandbox_guest_bootstrap "
        "--config /run/aip/bootstrap.json\n"
    )
    if len(script.encode()) > 16_384:
        raise GuestBootstrapError("EC2 user data exceeds 16 KiB")
    return script


def prepare_guest_bundle(
    config: GuestBootstrapConfig, raw: bytes, destination: Path
) -> tuple[Path, Path]:
    if not raw or len(raw) > 50_000_000 or hashlib.sha256(raw).hexdigest() != (
        config.bundle_sha256
    ):
        raise GuestBootstrapError("Guest bundle digest or size differs")
    try:
        extract_regular_tar(raw, destination)
    except UnsafeArchive as error:
        raise GuestBootstrapError("Guest bundle contains unsafe entries") from error
    manifest_path = destination / "control" / "manifest.json"
    workspace = destination / "workspace"
    if not manifest_path.is_file() or not workspace.is_dir():
        raise GuestBootstrapError("Guest bundle lacks source or approved manifest")
    plan = manifest_path.read_bytes()
    if hashlib.sha256(plan).hexdigest() != config.manifest_sha256:
        raise GuestBootstrapError("Guest manifest digest differs")
    try:
        EnvironmentManifest.model_validate_json(plan)
    except ValueError as error:
        raise GuestBootstrapError("Guest manifest is invalid") from error
    return workspace, manifest_path


def ready_marker(config: GuestBootstrapConfig) -> bytes:
    return json.dumps({
        "version": 1, "lease_id": config.lease_id, "fence": config.fence,
        "source_sha256": config.bundle_sha256,
    }, sort_keys=True, separators=(",", ":")).encode()


def validate_go_marker(config: GuestBootstrapConfig, raw: bytes) -> None:
    if len(raw) > 1024:
        raise GuestBootstrapError("Guest activation marker exceeds policy")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, ValueError) as error:
        raise GuestBootstrapError("Guest activation marker is malformed") from error
    if payload != {
        "version": 1, "lease_id": config.lease_id, "fence": config.fence,
        "source_sha256": config.bundle_sha256,
    }:
        raise GuestBootstrapError("Guest activation marker mismatches the lease")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        return None


_HTTP = build_opener(_NoRedirect())


def _get(url: str, *, max_bytes: int, missing_ok: bool = False) -> bytes | None:
    if not _s3_url(url):
        raise GuestBootstrapError("Guest requested an untrusted URL")
    try:
        with _HTTP.open(Request(url, method="GET"), timeout=10) as response:
            body = response.read(max_bytes + 1)
    except HTTPError as error:
        if missing_ok and error.code == 404:
            return None
        raise GuestBootstrapError("Guest S3 read failed") from error
    except URLError as error:
        raise GuestBootstrapError("Guest S3 read failed") from error
    if len(body) > max_bytes:
        raise GuestBootstrapError("Guest S3 object exceeds policy")
    return body


def _put(
    url: str, body: bytes, *, content_type: str = "application/json",
    max_bytes: int = 1_000_000,
) -> None:
    if not _s3_url(url) or len(body) > max_bytes:
        raise GuestBootstrapError("Guest S3 upload exceeds policy")
    request = Request(
        url, data=body, method="PUT", headers={
            "Content-Type": content_type,
            "If-None-Match": "*",
            "x-amz-server-side-encryption": "AES256",
        },
    )
    try:
        with _HTTP.open(request, timeout=20) as response:
            if response.status not in {200, 201}:
                raise GuestBootstrapError("Guest S3 upload was not accepted")
    except HTTPError as error:
        if error.code == 412:
            # Replayed cloud-init cannot replace an earlier object. The broker
            # validates the marker body before it publishes a go signal.
            return
        raise GuestBootstrapError("Guest S3 upload failed") from error
    except URLError as error:
        raise GuestBootstrapError("Guest S3 upload failed") from error


def _metadata_disabled() -> bool:
    # Use a direct connection; proxy variables must not turn a live IMDS
    # response into a false "disabled" result.
    from http.client import HTTPConnection

    connection = HTTPConnection("169.254.169.254", timeout=1)
    try:
        connection.request(
            "PUT", "/latest/api/token", headers={
                "X-aws-ec2-metadata-token-ttl-seconds": "60",
            },
        )
        connection.getresponse()
    except OSError:
        return True
    finally:
        connection.close()
    return False


def _capability_expiry(url: str) -> int:
    query = parse_qs(urlsplit(url).query)
    try:
        signed_at = datetime.strptime(query["X-Amz-Date"][0], "%Y%m%dT%H%M%SZ")
        ttl = int(query["X-Amz-Expires"][0])
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise GuestBootstrapError("Guest capability expiry is invalid") from error
    if not 60 <= ttl <= 3600:
        raise GuestBootstrapError("Guest capability lifetime exceeds policy")
    return int(signed_at.replace(tzinfo=timezone.utc).timestamp()) + ttl


def _evidence_archive(artifacts: Path) -> bytes:
    """Package only bounded regular guest outputs for the broker to verify."""
    output = io.BytesIO()
    total = 0
    count = 0
    with tarfile.open(fileobj=output, mode="w") as archive:
        for path in sorted(artifacts.rglob("*")):
            if path.is_symlink():
                raise GuestBootstrapError("Guest evidence contains a link")
            if path.is_dir():
                continue
            if not path.is_file():
                raise GuestBootstrapError("Guest evidence contains a special file")
            count += 1
            size = path.stat().st_size
            total += size
            if count > 1000 or size > 20_000_000 or total > 45_000_000:
                raise GuestBootstrapError("Guest evidence exceeds policy")
            entry = tarfile.TarInfo(path.relative_to(artifacts).as_posix())
            entry.size = size
            entry.mode = 0o644
            entry.mtime = 0
            with path.open("rb") as stream:
                archive.addfile(entry, stream)
    raw = output.getvalue()
    if len(raw) > 50_000_000:
        raise GuestBootstrapError("Guest evidence archive exceeds policy")
    return raw


def run_guest_bootstrap(config: GuestBootstrapConfig, root: Path) -> dict:
    """Run only on a fresh hardened AMI as root, before tenant code starts."""
    if os.name != "posix" or os.geteuid() != 0:
        raise GuestBootstrapError("Bootstrap must run as root inside the guest VM")
    raw = _get(config.source_get, max_bytes=50_000_000)
    assert raw is not None
    workspace, manifest_path = prepare_guest_bundle(config, raw, root)
    os.chmod(root, 0o755)
    os.chmod(manifest_path.parent, 0o755)
    os.chmod(manifest_path, 0o444)
    for path in [workspace, *workspace.rglob("*")]:
        os.chown(path, 10001, 10001)
    artifacts = root.parent / "aip-artifacts"
    artifacts.mkdir(mode=0o700, exist_ok=False)
    os.chown(artifacts, 10001, 10001)
    _put(config.ready_put, ready_marker(config))
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        go = _get(config.go_get, max_bytes=1024, missing_ok=True)
        if go is not None:
            validate_go_marker(config, go)
            break
        time.sleep(2)
    else:
        raise GuestBootstrapError("Broker did not authorize guest execution")
    if not _metadata_disabled():
        raise GuestBootstrapError("Instance metadata is still reachable")
    plan = EnvironmentManifest.model_validate_json(manifest_path.read_bytes())
    # Broker lease and SigV4 expiry bound the guest, including launch delay.
    deadline = min(
        config.expires_at_epoch, _capability_expiry(config.result_put),
        _capability_expiry(config.evidence_put),
    )
    remaining = deadline - int(time.time()) - 30
    if remaining < 60:
        raise GuestBootstrapError("Sandbox execution window has expired")
    command = [
        "runuser", "-u", "aipguest", "--", "env", "-i",
        "HOME=/home/aipguest", "PATH=/opt/aip/.venv/bin:/opt/node/bin:/usr/bin:/bin",
        "PYTHONPATH=/opt/aip", "PLAYWRIGHT_BROWSERS_PATH=/opt/aip/browsers",
        "/opt/aip/.venv/bin/python", "-m", "platform_app.guest_runner",
        "--manifest", str(manifest_path), "--workspace", str(workspace),
        "--artifacts", str(artifacts), "--case-id",
        "sandbox-" + hashlib.sha256(config.lease_id.encode()).hexdigest()[:32],
    ]
    try:
        completed = subprocess.run(command, timeout=min(plan.max_runtime_seconds, remaining),
                                   check=False, capture_output=True)
        exit_code = completed.returncode
    except subprocess.TimeoutExpired:
        exit_code = None
    result_file = artifacts / "result.json"
    if result_file.is_file() and result_file.stat().st_size <= 1_000_000:
        try:
            guest_result = json.loads(result_file.read_bytes())
        except (UnicodeDecodeError, ValueError):
            guest_result = None
    else:
        guest_result = None
    output = {
        "version": 1, "lease_id": config.lease_id, "fence": config.fence,
        "source_sha256": config.bundle_sha256,
        "guest_exit_code": exit_code, "baseline": guest_result,
    }
    evidence = _evidence_archive(artifacts)
    output["evidence_sha256"] = hashlib.sha256(evidence).hexdigest()
    output["evidence_bytes"] = len(evidence)
    _put(
        config.evidence_put, evidence, content_type="application/x-tar",
        max_bytes=50_000_000,
    )
    body = json.dumps(output, sort_keys=True, separators=(",", ":")).encode()
    _put(config.result_put, body)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path("/var/lib/aip/guest"))
    args = parser.parse_args()
    try:
        config = GuestBootstrapConfig.from_json(args.config.read_bytes())
        result = run_guest_bootstrap(config, args.root)
    except (GuestBootstrapError, OSError, ValueError):
        # No capability URL, source text or credentials enter console logs.
        print("Guest bootstrap failed", flush=True)
        return 2
    return 0 if result["guest_exit_code"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
