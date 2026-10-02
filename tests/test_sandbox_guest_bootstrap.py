import base64
import hashlib
import io
import json
import re
import tarfile
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import boto3
import pytest
from botocore.config import Config

import platform_app.sandbox_guest_bootstrap as guest_bootstrap
from platform_app.environment_manifest import EnvironmentManifest
from platform_app.repository_archive import SourceArchive
from platform_app.sandbox_bundle import build_guest_bundle
from platform_app.sandbox_evidence import package_guest_evidence
from platform_app.sandbox_guest_bootstrap import (
    GuestBootstrapConfig,
    GuestBootstrapError,
    prepare_guest_bundle,
    ready_marker,
    render_user_data,
    validate_go_marker,
)
from platform_app.sandbox_transport import SandboxObjectKeys, issue_guest_urls


def _bundle():
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as tar:
        data = b"print('guest')\n"
        member = tarfile.TarInfo("app.py")
        member.size = len(data)
        tar.addfile(member, io.BytesIO(data))
    raw = output.getvalue()
    source = SourceArchive("a" * 40, hashlib.sha256(raw).hexdigest(), raw, 1)
    manifest = EnvironmentManifest.model_validate({
        "schema_version": "1.0", "language": "python", "python_version": "3.12",
        "services": [{
            "name": "app", "port": 8001, "health_path": "/health",
            "command": {"argv": ["python", "app.py"], "timeout_seconds": 30},
        }],
        "named_tests": {"baseline": {
            "argv": ["python", "-c", "print('ok')"], "timeout_seconds": 30,
        }},
        "browser_scenario": {"steps": [{"action": "goto", "path": "/"}]},
    })
    return build_guest_bundle(source, manifest)


def _config(bundle):
    s3 = boto3.client(
        "s3", region_name="us-east-1", aws_access_key_id="fixture-key",
        aws_secret_access_key="fixture-secret",
        config=Config(signature_version="s3v4"),
    )
    urls = issue_guest_urls(s3, "example-bucket", SandboxObjectKeys.scoped(
        "tenant-a", "project-a", "run-a", "lease-a"
    ))
    return urls, GuestBootstrapConfig(
        "lease-a", 3, bundle.sha256, bundle.manifest_sha256,
        int(time.time()) + 1800,
        urls.source_get, urls.ready_put, urls.go_get, urls.result_put,
        urls.evidence_put,
    )


def test_userdata_hides_urls_in_base64_and_stages_approved_bundle(tmp_path):
    bundle = _bundle()
    urls, config = _config(bundle)
    script = render_user_data(
        urls, "lease-a", 3, bundle.sha256, bundle.manifest_sha256,
        config.expires_at_epoch,
    )
    assert script.startswith("#!/bin/bash\n")
    assert len(script.encode()) <= 16_384
    assert "X-Amz-Signature" not in script
    encoded = re.search(r"printf '%s' '([^']+)'", script).group(1)
    decoded = GuestBootstrapConfig.from_json(base64.b64decode(encoded))
    assert decoded == config
    workspace, manifest = prepare_guest_bundle(config, bundle.archive, tmp_path / "guest")
    assert (workspace / "app.py").read_bytes() == b"print('guest')\n"
    assert json.loads(manifest.read_text())["schema_version"] == "1.0"
    marker = ready_marker(config)
    validate_go_marker(config, marker)
    with pytest.raises(GuestBootstrapError):
        validate_go_marker(config, marker.replace(b"lease-a", b"lease-b"))


def test_guest_rejects_tampered_bundle_and_untrusted_url(tmp_path):
    bundle = _bundle()
    urls, config = _config(bundle)
    with pytest.raises(GuestBootstrapError):
        prepare_guest_bundle(config, bundle.archive + b"x", tmp_path / "bad")
    with pytest.raises(GuestBootstrapError):
        GuestBootstrapConfig(
            "lease-a", 3, bundle.sha256, bundle.manifest_sha256,
            int(time.time()) + 1800,
            "http://169.254.169.254/latest/meta-data", urls.ready_put,
            urls.go_get, urls.result_put, urls.evidence_put,
        )
    with pytest.raises(GuestBootstrapError):
        GuestBootstrapConfig.from_json(b'{"version":2}')


def test_root_supervisor_waits_for_go_and_seal_before_guest_process(tmp_path, monkeypatch):
    bundle = _bundle()
    _, config = _config(bundle)
    fetched = []
    uploaded = []

    def fake_get(url, **kwargs):
        fetched.append(url)
        return bundle.archive if url == config.source_get else ready_marker(config)

    def fake_put(url, body, **kwargs):
        uploaded.append((url, body))

    def fake_run(command, **kwargs):
        assert uploaded[0][0] == config.ready_put
        assert fetched[-1] == config.go_get
        assert command[:5] == ["runuser", "-u", "aipguest", "--", "env"]
        assert command[command.index("--case-id") + 1].startswith("sandbox-")
        assert 60 <= kwargs["timeout"] <= 1800
        artifacts = Path(command[command.index("--artifacts") + 1])
        (artifacts / "result.json").write_text('{"status":"BASELINE_RECORDED"}')
        package_guest_evidence(artifacts)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(guest_bootstrap, "os", SimpleNamespace(
        name="posix", geteuid=lambda: 0,
        chmod=lambda *_: None, chown=lambda *_: None,
    ))
    monkeypatch.setattr(guest_bootstrap, "_get", fake_get)
    monkeypatch.setattr(guest_bootstrap, "_put", fake_put)
    monkeypatch.setattr(guest_bootstrap, "_metadata_disabled", lambda: False)
    monkeypatch.setattr(guest_bootstrap.subprocess, "run", fake_run)
    with pytest.raises(GuestBootstrapError):
        guest_bootstrap.run_guest_bootstrap(config, tmp_path / "first" / "guest")
    assert all(url != config.result_put for url, _ in uploaded)
    uploaded.clear()
    monkeypatch.setattr(guest_bootstrap, "_metadata_disabled", lambda: True)
    output = guest_bootstrap.run_guest_bootstrap(config, tmp_path / "second" / "guest")
    assert output["baseline"]["status"] == "BASELINE_RECORDED"
    assert [url for url, _ in uploaded] == [
        config.ready_put, config.evidence_put, config.result_put,
    ]
    assert output["evidence_bytes"] > 0
    with tarfile.open(fileobj=io.BytesIO(uploaded[1][1]), mode="r:") as archive:
        assert archive.extractfile("result.json").read() == (
            b'{"status":"BASELINE_RECORDED"}'
        )


def test_root_supervisor_rejects_expired_lease_before_guest_process(tmp_path, monkeypatch):
    bundle = _bundle()
    _, config = _config(bundle)
    config = replace(config, expires_at_epoch=int(time.time()) + 20)
    fetched = []
    uploaded = []
    monkeypatch.setattr(guest_bootstrap, "os", SimpleNamespace(
        name="posix", geteuid=lambda: 0,
        chmod=lambda *_: None, chown=lambda *_: None,
    ))
    def fake_get(url, **kwargs):
        fetched.append(url)
        return bundle.archive if url == config.source_get else ready_marker(config)

    monkeypatch.setattr(guest_bootstrap, "_get", fake_get)
    monkeypatch.setattr(guest_bootstrap, "_put", lambda url, body: uploaded.append(url))
    monkeypatch.setattr(guest_bootstrap, "_metadata_disabled", lambda: True)
    monkeypatch.setattr(guest_bootstrap.subprocess, "run", lambda *_, **__: pytest.fail(
        "Expired guest was executed"
    ))
    with pytest.raises(GuestBootstrapError, match="execution window"):
        guest_bootstrap.run_guest_bootstrap(config, tmp_path / "guest")
    assert fetched[-1] == config.go_get
    assert uploaded == [config.ready_put]
