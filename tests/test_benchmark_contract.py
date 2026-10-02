import hashlib
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from platform_app.benchmark_contract import (
    BenchmarkError,
    BenchmarkSuite,
    CaseResult,
    score_complete_suite,
    verify_suite_assets,
)


def suite(commit: str = "a" * 40, oracle_hash: str = "b" * 64, lock_hash: str = "c" * 64):
    cases = []
    for category in ("form_frontend", "api_contract", "csv_timestamp", "non_bug"):
        for index in range(10):
            case_id = f"{category}-{index:02d}"
            cases.append({
                "case_id": case_id, "category": category,
                "split": "development" if index < 5 else (
                    "regression" if index < 8 else "release"
                ),
                "base_commit": commit,
                "fixture_path": f"benchmarks/fixtures/{case_id}/base",
                "hidden_oracle_path": f"benchmarks/oracles/{case_id}/hidden.py",
                "hidden_oracle_sha256": oracle_hash,
                "dependency_lock_path": "benchmarks/locks/deps.lock",
                "dependency_lock_sha256": lock_hash,
                "environment_image_sha256": "d" * 64,
                "run_seed": index,
                "expected_reproduction": (
                    "NO_SUPPORTED_DEFECT" if category == "non_bug" else "BUG_REPRODUCED"
                ),
                "accepted_outcomes": (
                    ["NO_DEFECT", "INCONCLUSIVE"] if category == "non_bug"
                    else ["VERIFIED_REPAIR", "INCONCLUSIVE"]
                ),
            })
    return {
        "schema_version": "1.0", "benchmark_revision": "benchmark-v1",
        "oracle_revision": "oracle-v1", "cases": cases,
    }


def results(manifest: BenchmarkSuite):
    values = []
    for case in manifest.cases:
        if case.category == "non_bug":
            outcome = "NO_DEFECT" if case.run_seed < 9 else "FAILED"
        else:
            outcome = "VERIFIED_REPAIR" if case.run_seed < 7 else "FAILED"
        values.append(CaseResult(
            case_id=case.case_id,
            benchmark_revision=manifest.benchmark_revision,
            model_entry_id="model-a", run_id=f"run-{case.case_id}",
            outcome=outcome, reproduced=case.category != "non_bug",
            hidden_check_executed=outcome == "VERIFIED_REPAIR",
            hidden_check_passed=outcome == "VERIFIED_REPAIR",
            actual_cost_usd=Decimal("0.25"), duration_ms=1000,
        ))
    return values


def test_complete_suite_counts_and_conservative_scores():
    manifest = BenchmarkSuite.model_validate(suite())
    score = score_complete_suite(manifest, results(manifest))
    assert score["cases"] == 40
    assert score["verified_repair_rate"] == "0.7"
    assert score["non_bug_correct_rate"] == "0.9"
    assert score["metric_targets_met"] is True
    assert score["qualification_status"] == "UNVERIFIED_RESULTS"
    incomplete = suite()
    incomplete["cases"].pop()
    with pytest.raises(ValidationError, match="exactly 10"):
        BenchmarkSuite.model_validate(incomplete)
    repeated = suite()
    repeated["cases"][1]["fixture_path"] = repeated["cases"][0]["fixture_path"]
    with pytest.raises(ValidationError, match="distinct fixture"):
        BenchmarkSuite.model_validate(repeated)
    with pytest.raises(BenchmarkError, match="exactly one result"):
        score_complete_suite(manifest, results(manifest)[:-1])


def test_false_success_and_unaccepted_repair_fail_the_metric_gate():
    manifest = BenchmarkSuite.model_validate(suite())
    values = results(manifest)
    values[0].hidden_check_passed = False
    nonbug = next(item for item in values if item.case_id.startswith("non_bug-"))
    nonbug.outcome = "VERIFIED_REPAIR"
    score = score_complete_suite(manifest, values)
    assert values[0].case_id in score["false_success_case_ids"]
    assert nonbug.case_id in score["false_success_case_ids"]
    assert score["metric_targets_met"] is False
    duplicate = results(manifest)
    duplicate[1].run_id = duplicate[0].run_id
    with pytest.raises(BenchmarkError, match="distinct run IDs"):
        score_complete_suite(manifest, duplicate)
    numeric = duplicate[0].model_dump()
    numeric["actual_cost_usd"] = 0.25
    assert CaseResult.model_validate(numeric).actual_cost_usd == Decimal("0.25")


def test_asset_verifier_checks_pinned_commit_and_digest(tmp_path: Path):
    root = tmp_path / "repo"
    lock = root / "benchmarks/locks/deps.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text("locked==1\n", encoding="utf-8")
    for case in suite()["cases"]:
        fixture = root / case["fixture_path"]
        oracle = root / case["hidden_oracle_path"]
        fixture.mkdir(parents=True)
        oracle.parent.mkdir(parents=True)
        (fixture / "server.py").write_text("print('base')\n", encoding="utf-8")
        oracle.write_text("assert True\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run([
        "git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
        "commit", "-qm", "pinned benchmark",
    ], cwd=root, check=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()

    def pinned_digest(relative: str) -> str:
        content = subprocess.check_output(["git", "show", f"{commit}:{relative}"], cwd=root)
        return hashlib.sha256(content).hexdigest()

    manifest = BenchmarkSuite.model_validate(suite(
        commit, pinned_digest("benchmarks/oracles/form_frontend-00/hidden.py"),
        pinned_digest("benchmarks/locks/deps.lock"),
    ))
    verify_suite_assets(manifest, root)
    invalid = BenchmarkSuite.model_validate(suite(commit, "0" * 64,
        pinned_digest("benchmarks/locks/deps.lock")))
    with pytest.raises(BenchmarkError, match="digest changed"):
        verify_suite_assets(invalid, root)
