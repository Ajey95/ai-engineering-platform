"""Pin the 40-case synthetic catalog to committed Git assets and a built image."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

from platform_app.benchmark_contract import BenchmarkSuite, verify_suite_assets
from scripts.generate_benchmark_cases import API_CASES, CSV_CASES, FORM_CASES, NONBUG_CASES


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(root: Path, commit: str, image_digest: str) -> BenchmarkSuite:
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("A full pinned Git commit is required")
    digest = image_digest.removeprefix("sha256:")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("A built environment image SHA-256 is required")
    lock_relative = "benchmarks/locks/platform-uv.lock"
    lock_sha = _sha256(root / lock_relative)
    grouped = {
        "form_frontend": ["form-submit-001", *(case.case_id for case in FORM_CASES)],
        "api_contract": [case_id for case_id, *_ in API_CASES],
        "csv_timestamp": [case.case_id for case in CSV_CASES],
        "non_bug": [case.case_id for case in NONBUG_CASES],
    }
    cases = []
    for category, ids in grouped.items():
        for ordinal, case_id in enumerate(ids):
            fixture = (
                "benchmarks/fixtures/form-submit/base" if case_id == "form-submit-001"
                else f"benchmarks/fixtures/{case_id}/base"
            )
            oracle = f"benchmarks/oracles/{case_id}/test_hidden.py"
            cases.append({
                "case_id": case_id,
                "category": category,
                "split": "development" if ordinal < 3 else
                         "regression" if ordinal < 6 else "release",
                "base_commit": commit,
                "fixture_path": fixture,
                "hidden_oracle_path": oracle,
                "hidden_oracle_sha256": _sha256(root / oracle),
                "dependency_lock_path": lock_relative,
                "dependency_lock_sha256": lock_sha,
                "environment_image_sha256": digest,
                "run_seed": int.from_bytes(hashlib.sha256(case_id.encode()).digest()[:4]),
                "expected_reproduction": (
                    "NO_SUPPORTED_DEFECT" if category == "non_bug" else "BUG_REPRODUCED"
                ),
                "accepted_outcomes": (
                    ["NO_DEFECT", "INCONCLUSIVE"] if category == "non_bug"
                    else ["VERIFIED_REPAIR"]
                ),
            })
    suite = BenchmarkSuite.model_validate({
        "schema_version": "1.0", "benchmark_revision": "synthetic-v1",
        "oracle_revision": "1.0", "cases": cases,
    })
    verify_suite_assets(suite, root)
    return suite


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-digest", required=True)
    parser.add_argument("--commit")
    parser.add_argument("--output", type=Path, default=Path("benchmarks/suite-v1.json"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    commit = args.commit or subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
        text=True, check=True, timeout=10,
    ).stdout.strip()
    suite = build(root, commit, args.image_digest)
    output = (root / args.output).resolve()
    if not output.is_relative_to(root / "benchmarks"):
        parser.error("Suite output must stay under benchmarks")
    content = json.dumps(suite.model_dump(mode="json"), indent=2) + "\n"
    if output.exists() and output.read_text(encoding="utf-8") != content:
        parser.error("Existing suite differs; review and version the revision")
    output.write_text(content, encoding="utf-8")
    print(f"Verified and pinned {len(suite.cases)} cases at {commit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
