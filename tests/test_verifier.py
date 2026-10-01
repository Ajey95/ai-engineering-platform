import json
import sys

from platform_app.verifier import run_named_test, tree_hash


def test_verifier_records_exact_tree_command_and_exit(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "case.py").write_text("value = 1\n")
    manifest = {
        "case_id": "receipt-test",
        "oracle_revision": "1.0",
        "named_tests": {"baseline": [sys.executable, "-c", "assert 1 == 1"]},
    }
    before = tree_hash(workspace)
    receipt = run_named_test(manifest, "baseline", workspace, tmp_path / "artifacts")
    saved = json.loads((tmp_path / "artifacts" / "test-baseline.json").read_text())
    assert receipt == saved
    assert receipt["status"] == "PASSED"
    assert receipt["tested_tree_sha256"] == before
    assert receipt["exit_code"] == 0
    (workspace / "case.py").write_text("value = 2\n")
    assert tree_hash(workspace) != before


def test_verifier_does_not_treat_timeout_as_pass(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manifest = {
        "case_id": "timeout-test",
        "oracle_revision": "1.0",
        "named_tests": {"slow": [sys.executable, "-c", "import time; time.sleep(2)"]},
    }
    receipt = run_named_test(manifest, "slow", workspace, tmp_path / "artifacts", 1)
    assert receipt["status"] == "TIMEOUT"
    assert receipt["exit_code"] is None
