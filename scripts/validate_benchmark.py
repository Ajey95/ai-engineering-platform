"""Validate a complete pinned 30+10 benchmark and score supplied case results."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import ValidationError

from platform_app.benchmark_contract import (
    BenchmarkError,
    BenchmarkSuite,
    CaseResult,
    score_complete_suite,
    suite_digest,
    verify_suite_assets,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--results", type=Path)
    args = parser.parse_args()
    try:
        suite = BenchmarkSuite.model_validate_json(args.manifest.read_text(encoding="utf-8"))
        verify_suite_assets(suite, args.repository)
        output = {
            "benchmark_revision": suite.benchmark_revision,
            "suite_sha256": suite_digest(suite),
            "case_count": len(suite.cases),
            "asset_status": "VERIFIED_PINNED_FILES",
        }
        if args.results:
            raw = json.loads(args.results.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                raise BenchmarkError("Results must be a list")
            results = [CaseResult.model_validate(item) for item in raw]
            output["metrics"] = score_complete_suite(suite, results)
        print(json.dumps(output, indent=2))
        return 0
    except (OSError, ValueError, ValidationError, BenchmarkError) as error:
        parser.error(f"Benchmark validation failed: {type(error).__name__}: {error}")


if __name__ == "__main__":
    raise SystemExit(main())
