import socket
import sys

import pytest

from platform_app.environment_manifest import EnvironmentManifest
from platform_app.guest_runner import run_environment


def _manifest(port, *, external=(), python_version=None):
    return EnvironmentManifest.model_validate({
        "schema_version": "1.0", "language": "python",
        "python_version": python_version or f"{sys.version_info.major}.{sys.version_info.minor}",
        "services": [{
            "name": "app", "port": port, "health_path": "/health",
            "command": {"argv": [sys.executable, "server.py", str(port)],
                        "timeout_seconds": 30},
        }],
        "named_tests": {"baseline": {
            "argv": [sys.executable, "-c", "print('baseline checked')"],
            "timeout_seconds": 20,
        }},
        "browser_scenario": {"steps": [
            {"action": "goto", "path": "/"},
            {"action": "expect_text", "text": "Ready"},
        ]},
        "external_destinations": list(external),
    })


@pytest.mark.parametrize("phase,status", [
    ("baseline", "BASELINE_RECORDED"),
    ("candidate", "CANDIDATE_RECORDED"),
])
def test_guest_records_named_and_browser_evidence(tmp_path, phase, status):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    source = tmp_path / "source"
    source.mkdir()
    (source / "server.py").write_text(
        "from http.server import BaseHTTPRequestHandler, HTTPServer\n"
        "import sys\n"
        "class Handler(BaseHTTPRequestHandler):\n"
        "    def do_GET(self):\n"
        "        body = b'<h1>Ready</h1>' if self.path == '/' else b'ok'\n"
        "        self.send_response(200)\n"
        "        self.end_headers()\n"
        "        self.wfile.write(body)\n"
        "HTTPServer(('127.0.0.1', int(sys.argv[1])), Handler).serve_forever()\n",
        encoding="utf-8", newline="\n",
    )
    evidence = tmp_path / "evidence"
    result = run_environment(
        _manifest(port), source, evidence, case_id="case-001", phase=phase
    )
    assert result["status"] == status and result["phase"] == phase
    assert result["named_tests"]["baseline"]["status"] == "PASSED"
    assert result["browser"]["status"] == "PASSED"
    assert (evidence / "browser" / "final.png").is_file()


def test_guest_fails_closed_when_runtime_or_egress_is_unavailable(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    bad_runtime = _manifest(8001, python_version="99.1")
    result = run_environment(bad_runtime, source, tmp_path / "evidence", case_id="case-001")
    assert result["status"] == "ENVIRONMENT_UNAVAILABLE"
    assert result["environment_error"] == "RUNTIME_VERSION_MISMATCH"
    with pytest.raises(ValueError):
        run_environment(
            _manifest(8001, external=("https://registry.npmjs.org",)),
            source, tmp_path / "external", case_id="case-001",
        )
