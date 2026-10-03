"""Check that generated synthetic baselines fail and references satisfy hidden tests."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from scripts.generate_benchmark_cases import API_CASES, CSV_CASES, FORM_CASES, NONBUG_CASES


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.root.resolve()
    cases = FORM_CASES + CSV_CASES + NONBUG_CASES
    catalog = {case.case_id: case.category for case in cases}
    catalog.update({case_id: "api_contract" for case_id, *_ in API_CASES})
    failures = []
    for case_id, category in sorted(catalog.items()):
        oracle = root / "benchmarks" / "oracles" / case_id / "test_hidden.py"
        for variant in ("base", "reference"):
            workspace = (
                root / "benchmarks" / "fixtures" / case_id / "base"
                if variant == "base" else root / "benchmarks" / "references" / case_id
            )
            env = {
                **os.environ,
                "PYTHONPATH": str(workspace),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            }
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(oracle)],
                cwd=workspace, env=env, capture_output=True, text=True, timeout=30,
                check=False,
            )
            expected_pass = variant == "reference" or category == "non_bug"
            observed_pass = result.returncode == 0
            if observed_pass != expected_pass:
                failures.append(f"{case_id}/{variant}: expected pass={expected_pass}, "
                                f"return={result.returncode}: {result.stdout[-500:]}")
        print(f"{case_id}: baseline {'pass' if category == 'non_bug' else 'fails'}, "
              "reference passes", flush=True)
    if failures:
        for failure in failures:
            print(failure, file=sys.stderr)
        return 1
    print(f"Verified {len(catalog)} independent synthetic cases", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
