import hashlib
import io
import json
import tarfile

import pytest

from platform_app.environment_manifest import EnvironmentManifest
from platform_app.repository_archive import SourceArchive
from platform_app.safe_archive import extract_regular_tar
from platform_app.sandbox_bundle import SandboxBundleError, build_guest_bundle


def _source():
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as tar:
        data = b"print('hello')\n"
        member = tarfile.TarInfo("app.py")
        member.size = len(data)
        tar.addfile(member, io.BytesIO(data))
    raw = output.getvalue()
    return SourceArchive("a" * 40, hashlib.sha256(raw).hexdigest(), raw, 1)


def _manifest():
    return EnvironmentManifest.model_validate({
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


def test_guest_bundle_pins_source_and_approved_manifest(tmp_path):
    source = _source()
    plan = _manifest()
    bundle = build_guest_bundle(source, plan)
    assert bundle == build_guest_bundle(source, plan)
    assert bundle.source_commit == "a" * 40
    assert bundle.manifest_sha256 == hashlib.sha256(json.dumps(
        plan.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()
    extract_regular_tar(bundle.archive, tmp_path / "unpacked")
    assert (tmp_path / "unpacked/workspace/app.py").read_bytes() == b"print('hello')\n"
    saved = json.loads((tmp_path / "unpacked/control/manifest.json").read_text())
    assert EnvironmentManifest.model_validate(saved).digest() == plan.digest()
    assert not (tmp_path / "unpacked/workspace/control/manifest.json").exists()


def test_bundle_rejects_changed_source_and_size_limit():
    source = _source()
    changed = SourceArchive(source.commit, source.sha256, source.archive + b"x", 1)
    with pytest.raises(SandboxBundleError):
        build_guest_bundle(changed, _manifest())
    with pytest.raises(SandboxBundleError):
        build_guest_bundle(source, _manifest(), max_bytes=512)
