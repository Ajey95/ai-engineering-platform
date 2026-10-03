"""Exercise benchmark browser reproduction on loopback synthetic fixtures."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from platform_app.browser_runner import run_scenario
from scripts.generate_benchmark_cases import API_CASES, CSV_CASES, FORM_CASES, NONBUG_CASES


async def verify(case_id: str, category: str, root: Path, reference: bool) -> bool:
    manifest_path = root / "benchmarks" / "fixtures" / case_id / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["start_command"][0] = sys.executable
    workspace = (
        root / "benchmarks" / "references" / case_id
        if reference else root / "benchmarks" / "fixtures" / case_id / "base"
    )
    artifact_root = root / "artifacts" / "benchmark-browser" / case_id
    result = await run_scenario(
        manifest, workspace, artifact_root / ("reference" if reference else "baseline")
    )
    expected = "PASSED" if reference or category == "non_bug" else "FAILED"
    actual = result["status"]
    print(f"{case_id}/{('reference' if reference else 'baseline')}: {actual}", flush=True)
    return actual == expected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case")
    parser.add_argument("--reference", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    cases = FORM_CASES + CSV_CASES + NONBUG_CASES
    catalog = {case.case_id: case.category for case in cases}
    catalog.update({case_id: "api_contract" for case_id, *_ in API_CASES})
    selected = [args.case] if args.case else sorted(catalog)
    if any(case_id not in catalog for case_id in selected):
        parser.error("Unknown generated benchmark case")
    passed = True
    for case_id in selected:
        try:
            passed = asyncio.run(verify(
                case_id, catalog[case_id], root, args.reference
            )) and passed
        except Exception as error:
            print(f"{case_id}: {type(error).__name__}: {error}", file=sys.stderr)
            passed = False
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
