import asyncio
import socket
import sys

import pytest
from pydantic import ValidationError

from platform_app.browser_runner import run_scenario
from platform_app.environment_manifest import (
    EnvironmentManifest,
    EnvironmentManifestError,
    authorize_environment_destinations,
)
from platform_app.verifier import run_named_test


def _manifest():
    return {
        "schema_version": "1.0",
        "language": "python-node",
        "python_version": "3.12",
        "node_version": "22",
        "install": [{"argv": ["python", "-m", "pip", "install", "--no-index", "."],
                     "timeout_seconds": 180}],
        "build": [],
        "services": [{
            "name": "app", "port": 8001, "health_path": "/health",
            "command": {"argv": ["python", "server.py"], "timeout_seconds": 300},
        }],
        "named_tests": {
            "baseline": {"argv": ["python", "-m", "pytest", "tests"],
                         "timeout_seconds": 120}
        },
        "browser_scenario": {
            "steps": [
                {"action": "goto", "path": "/"},
                {"action": "click", "role": "button", "name": "Create"},
                {"action": "expect_text", "text": "Created"},
            ],
            "mask_selectors": [],
        },
        "fixture_setup": [],
        "postgres_fixture": False,
        "external_destinations": ["https://registry.npmjs.org"],
        "environment_keys": ["TEST_MODE"],
        "max_runtime_seconds": 1800,
    }


def test_versioned_environment_hash_and_explicit_egress_approval():
    manifest = EnvironmentManifest.model_validate(_manifest())
    assert len(manifest.digest()) == 64
    same = EnvironmentManifest.model_validate(_manifest())
    assert same.digest() == manifest.digest()
    runner = manifest.runner_manifest("case-001")
    assert runner["allowed_origin"] == "http://127.0.0.1:8001"
    assert runner["require_instance_header"] is False
    assert runner["startup_timeout_seconds"] == 300
    assert runner["named_test_timeouts"]["baseline"] == 120
    assert runner["named_tests"]["baseline"] == ["python", "-m", "pytest", "tests"]
    with pytest.raises(EnvironmentManifestError):
        authorize_environment_destinations(manifest, set())
    authorize_environment_destinations(manifest, {"https://REGISTRY.NPMJS.ORG/"})


@pytest.mark.parametrize("change", [
    {"schema_version": "2.0"},
    {"language": "ruby"},
    {"node_version": None},
    {"python_version": "latest"},
    {"external_destinations": ["http://example.com"]},
    {"external_destinations": ["https://127.0.0.1"]},
    {"external_destinations": ["https://metadata.internal"]},
    {"environment_keys": ["AIP_SECRET"]},
    {"browser_scenario": {"steps": [{"action": "goto", "path": "//evil.example"}]}},
    {"browser_scenario": {"steps": [{"action": "click", "role": "button"}]}},
    {"services": [{
        "name": "app", "port": 8001, "health_path": "//evil.example/path",
        "command": {"argv": ["python", "server.py"], "timeout_seconds": 300},
    }]},
])
def test_rejects_unsupported_runtime_network_and_process_scope(change):
    payload = _manifest()
    payload.update(change)
    with pytest.raises((ValidationError, ValueError)):
        EnvironmentManifest.model_validate(payload)


def test_rejects_duplicate_service_ports_and_unbounded_argv():
    payload = _manifest()
    payload["services"].append({
        "name": "worker", "port": 8001, "health_path": "/health",
        "command": {"argv": ["python", "worker.py"], "timeout_seconds": 120},
    })
    with pytest.raises(ValidationError):
        EnvironmentManifest.model_validate(payload)
    payload = _manifest()
    payload["named_tests"]["baseline"]["argv"] = ["x" * 2049]
    with pytest.raises(ValidationError):
        EnvironmentManifest.model_validate(payload)


def test_rejects_unreviewed_manifest_fields():
    payload = _manifest()
    payload["credential_ref"] = "secret://not-approved"
    with pytest.raises(ValidationError):
        EnvironmentManifest.model_validate(payload)


def test_general_manifest_runs_browser_scenario_without_fixture_header(tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    (tmp_path / "server.py").write_text(
        "from http.server import BaseHTTPRequestHandler, HTTPServer\n"
        "import sys\n"
        "class Handler(BaseHTTPRequestHandler):\n"
        "    def do_GET(self):\n"
        "        body = b'<h1>Ready</h1>' if self.path == '/' else b'ok'\n"
        "        self.send_response(200)\n"
        "        self.end_headers()\n"
        "        self.wfile.write(body)\n"
        "HTTPServer(('127.0.0.1', int(sys.argv[1])), Handler).serve_forever()\n",
        encoding="utf-8",
    )
    payload = _manifest()
    payload["services"][0]["port"] = port
    payload["services"][0]["command"]["argv"] = [sys.executable, "server.py", str(port)]
    payload["browser_scenario"]["steps"] = [
        {"action": "goto", "path": "/"},
        {"action": "expect_text", "text": "Ready"},
    ]
    manifest = EnvironmentManifest.model_validate(payload)
    artifacts = tmp_path / "evidence"
    result = asyncio.run(run_scenario(
        manifest.runner_manifest("case-001"), tmp_path, artifacts
    ))
    assert result["status"] == "PASSED"
    assert (artifacts / result["final_screenshot"]).is_file()


def test_general_manifest_runs_a_named_test_at_the_exact_tree(tmp_path):
    payload = _manifest()
    payload["named_tests"]["baseline"]["argv"] = [
        sys.executable, "-c", "print('named test passed')"
    ]
    manifest = EnvironmentManifest.model_validate(payload)
    workspace = tmp_path / "source"
    workspace.mkdir()
    (workspace / "app.py").write_text("x = 1\n", encoding="utf-8")
    receipt = run_named_test(
        manifest.runner_manifest("case-001"), "baseline", workspace,
        tmp_path / "evidence",
    )
    assert receipt["status"] == "PASSED"
    assert receipt["tested_tree_sha256"] == receipt["post_test_tree_sha256"]
