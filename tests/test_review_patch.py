import json
import subprocess
from pathlib import Path

import pytest

from platform_app.dev_sandbox import prepare_workspace
from platform_app.models import Run
from platform_app.patch_workspace import parse_patch_response
from platform_app.review_patch import verified_fixture_diff
from platform_app.service import ServiceError
from platform_app.verifier import tree_hash


def test_download_diff_rechecks_pinned_candidate_before_export(tmp_path):
    repository = Path(__file__).resolve().parents[1]
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repository, text=True
    ).strip()
    run = Run(
        id="11111111-1111-4111-8111-111111111111",
        tenant_id="fixture-tenant", task_id="task", project_id="project",
        created_by="actor", idempotency_key="key", request_hash="r" * 64,
        base_commit=commit, model_entry_id="model",
        config_snapshot={"reproduction": {"fixture_case_id": "form-submit-001"}},
    )
    candidate = tmp_path / run.id / "candidate" / "workspace"
    prepare_workspace(repository / "benchmarks/fixtures/form-submit/base", candidate)
    source = candidate / "server.py"
    changed = source.read_text(encoding="utf-8").replace(
        "if payload.quantity > 0:", "if (payload.quantity or 0) > 0:"
    )
    source.write_text(changed, encoding="utf-8")
    patch = parse_patch_response(json.dumps({
        "diagnosis": "Guard absent quantity",
        "files": [{"path": "server.py", "content": changed}],
    }))
    receipt = {
        "status": "COMPLETED", "changed_files": ["server.py"],
        "patch_sha256": patch.patch_sha256,
        "candidate_tree_sha256": tree_hash(candidate),
    }
    diff = verified_fixture_diff(run, receipt, tmp_path, repository)
    assert "-    if payload.quantity > 0:" in diff
    assert "+    if (payload.quantity or 0) > 0:" in diff
    source.write_text(changed + "\n# tampered\n", encoding="utf-8")
    with pytest.raises(ServiceError) as mismatch:
        verified_fixture_diff(run, receipt, tmp_path, repository)
    assert mismatch.value.code == "PATCH_CHANGED"
