import json
from pathlib import Path

import pytest

from platform_app.patch_workspace import PatchError, materialize_candidate, parse_patch_response
from platform_app.verifier import tree_hash


def test_candidate_changes_only_approved_file(tmp_path):
    source = Path(__file__).resolve().parents[1] / "benchmarks/fixtures/form-submit/base"
    original = (source / "server.py").read_text(encoding="utf-8")
    response = json.dumps(
        {
            "diagnosis": "Missing quantity must default to one",
            "files": [
                {
                    "path": "server.py",
                    "content": original.replace("quantity: int | None = None", "quantity: int = 1"),
                },
            ],
        }
    )
    proposal = parse_patch_response(response)
    candidate = tmp_path / "candidate"
    digest = materialize_candidate(source, candidate, proposal)
    assert proposal.patch_sha256 == parse_patch_response(response).patch_sha256
    alternate_diagnosis = json.dumps(
        {
            "diagnosis": "Different explanation",
            "files": [
                {
                    "path": "server.py",
                    "content": proposal.files[0][1],
                }
            ],
        }
    )
    assert parse_patch_response(alternate_diagnosis).patch_sha256 == proposal.patch_sha256
    assert digest == tree_hash(candidate)
    assert (candidate / "server.py").read_text(encoding="utf-8") != original
    assert (source / "server.py").read_text(encoding="utf-8") == original
    assert (candidate / "tests/test_health.py").read_bytes() == (
        source / "tests/test_health.py"
    ).read_bytes()


@pytest.mark.parametrize("path", ["../server.py", "/server.py", "tests/test_health.py"])
def test_patch_rejects_unauthorized_paths(path):
    response = json.dumps(
        {
            "diagnosis": "attempt",
            "files": [
                {"path": path, "content": "pass\n"},
            ],
        }
    )
    with pytest.raises(PatchError, match="authorized"):
        parse_patch_response(response)


def test_patch_rejects_extra_fields_and_noop(tmp_path):
    with pytest.raises(PatchError, match="fields"):
        parse_patch_response(json.dumps({"diagnosis": "x", "files": [], "approved": True}))
    source = Path(__file__).resolve().parents[1] / "benchmarks/fixtures/form-submit/base"
    unchanged = (source / "server.py").read_text(encoding="utf-8")
    proposal = parse_patch_response(
        json.dumps(
            {
                "diagnosis": "no change",
                "files": [{"path": "server.py", "content": unchanged}],
            }
        )
    )
    with pytest.raises(PatchError, match="no change"):
        materialize_candidate(source, tmp_path / "candidate", proposal)
