"""End-to-end synthetic fixture evaluation and review packet construction.

This host runner proves the evidence flow with trusted local fixtures. It is
not an isolation boundary or an autonomous repair agent.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from uuid import uuid4

from platform_app.browser_runner import run_scenario
from platform_app.dev_sandbox import run_browser_fixture, run_verifier_fixture
from platform_app.media import MediaError, encode_hls
from platform_app.verifier import run_hidden_oracle, run_named_test, tree_hash


async def evaluate(
    manifest_path: Path, baseline: Path, candidate: Path, oracle: Path,
    artifacts: Path, candidate_origin: str, browser_runtime: str = "host",
) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if candidate_origin != "manual":
        raise ValueError("This development evaluator only accepts manual candidates")
    if browser_runtime not in {"host", "native", "wsl"}:
        raise ValueError("Unknown browser runtime")
    artifacts.mkdir(parents=True, exist_ok=True)
    baseline_dir = artifacts / "baseline"
    candidate_dir = artifacts / "candidate"

    async def browser(workspace: Path, target: Path) -> dict:
        if browser_runtime == "host":
            return await run_scenario(manifest, workspace, target)
        return await asyncio.to_thread(
            run_browser_fixture, "aip-dev-sandbox:0.1.0", workspace,
            manifest_path, target, f"aip-dev-{uuid4().hex[:12]}",
            runtime=browser_runtime,
        )

    async def named_test(workspace: Path, target: Path) -> dict:
        if browser_runtime == "host":
            return run_named_test(manifest, "baseline", workspace, target)
        return await asyncio.to_thread(
            run_verifier_fixture, "aip-dev-sandbox:0.1.0", workspace,
            manifest_path, target, f"aip-dev-{uuid4().hex[:12]}",
            mode="named", runtime=browser_runtime,
        )

    async def hidden_oracle(workspace: Path, target: Path) -> dict:
        if browser_runtime == "host":
            return run_hidden_oracle(manifest, workspace, oracle, target)
        return await asyncio.to_thread(
            run_verifier_fixture, "aip-dev-sandbox:0.1.0", workspace,
            manifest_path, target, f"aip-dev-{uuid4().hex[:12]}",
            mode="oracle", oracle=oracle, runtime=browser_runtime,
        )

    baseline_test = await named_test(baseline, baseline_dir)
    baseline_browser = await browser(baseline, baseline_dir)
    baseline_oracle = await hidden_oracle(baseline, baseline_dir)
    candidate_test = await named_test(candidate, candidate_dir)
    candidate_browser = await browser(candidate, candidate_dir)
    candidate_oracle = await hidden_oracle(candidate, candidate_dir)
    media = {}
    for label, browser in (("baseline", baseline_browser), ("candidate", candidate_browser)):
        if browser.get("recording"):
            source = artifacts / label / browser["recording"]
            try:
                media[label] = {
                    "status": "READY",
                    "master": str(encode_hls(
                        source, artifacts / "private-media", "fixture", label
                    )),
                }
            except (MediaError, OSError) as error:
                media[label] = {"status": "FAILED", "error": type(error).__name__}
        else:
            media[label] = {"status": "MISSING"}
    expected_baseline = (
        manifest["expected_baseline"] == "failure"
        and baseline_test["status"] == "PASSED"
        and baseline_browser["status"] == "FAILED"
        and baseline_oracle["status"] == "FAILED"
    )
    candidate_passed = all(
        receipt["status"] == "PASSED"
        for receipt in (candidate_test, candidate_browser, candidate_oracle)
    )
    verdict = "PASSED" if expected_baseline and candidate_passed else "FAILED"
    packet = {
        "schema_version": "1.0",
        "case_id": manifest["case_id"],
        "fixture_revision": manifest["fixture_revision"],
        "oracle_revision": manifest["oracle_revision"],
        "qualification_scope": (
            "synthetic_host_fixture" if browser_runtime == "host"
            else "synthetic_container_fixture"
        ),
        "autonomous_repair": False,
        "candidate_origin": candidate_origin,
        "verdict": verdict,
        "baseline_tree_sha256": tree_hash(baseline),
        "candidate_tree_sha256": tree_hash(candidate),
        "baseline": {"named_test": baseline_test, "browser": baseline_browser,
                     "oracle": baseline_oracle},
        "candidate": {"named_test": candidate_test, "browser": candidate_browser,
                      "oracle": candidate_oracle},
        "local_media": media,
        "limitations": [
            "Synthetic fixture only; this is not a customer sandbox.",
            "All fixture steps ran in development containers."
            if browser_runtime != "host" else "All fixture steps ran on the host.",
            "Candidate source was supplied externally and was not generated by this harness.",
            "Local HLS files have no hosted authorization or CDN grant.",
        ],
    }
    (artifacts / "review-packet.json").write_text(json.dumps(packet, indent=2), encoding="utf-8")
    return packet


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--candidate-origin", choices=["manual"], required=True)
    parser.add_argument("--browser-runtime", choices=["host", "native", "wsl"], default="host")
    args = parser.parse_args()
    result = asyncio.run(evaluate(
        args.manifest, args.baseline, args.candidate, args.oracle,
        args.artifacts, args.candidate_origin, args.browser_runtime,
    ))
    print(json.dumps({"case_id": result["case_id"], "verdict": result["verdict"]}))
    return 0 if result["verdict"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
