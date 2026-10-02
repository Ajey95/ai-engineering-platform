"""Compare independently recorded guest checks without claiming hidden correctness."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from platform_app.sandbox_transport import GuestOutput


@dataclass(frozen=True)
class GuestComparison:
    status: Literal["SUPPORTED", "FAILED", "INCONCLUSIVE"]
    reason: str
    baseline_checks: dict[str, str]
    candidate_checks: dict[str, str]


def _checks(observation: dict) -> dict[str, str] | None:
    named = observation.get("named_tests")
    browser = observation.get("browser")
    if not isinstance(named, dict) or not named or not isinstance(browser, dict):
        return None
    statuses = {}
    for name, receipt in named.items():
        if (
            not isinstance(name, str) or not isinstance(receipt, dict)
            or receipt.get("status") not in {"PASSED", "FAILED", "TIMEOUT", "INCONCLUSIVE"}
            or receipt.get("tested_tree_sha256") != receipt.get("post_test_tree_sha256")
        ):
            return None
        statuses[name] = receipt["status"]
    if (
        browser.get("status") not in {"PASSED", "FAILED", "TIMEOUT", "INCONCLUSIVE"}
        or browser.get("tested_tree_sha256") != browser.get("post_browser_tree_sha256")
    ):
        return None
    statuses["browser"] = browser["status"]
    return statuses


def compare_guest_observations(
    baseline: GuestOutput, candidate: GuestOutput,
    *, manifest_sha256: str, candidate_tree_sha256: str,
) -> GuestComparison:
    """Only the declared checks can be supported; independent oracle remains separate."""
    left = baseline.result
    right = candidate.result
    if (
        left.get("phase") != "baseline" or right.get("phase") != "candidate"
        or left.get("lease_id") == right.get("lease_id")
        or left.get("source_sha256") == right.get("source_sha256")
        or left.get("guest_exit_code") != 0 or right.get("guest_exit_code") != 0
    ):
        return GuestComparison("INCONCLUSIVE", "execution_identity_invalid", {}, {})
    base_observation = left.get("baseline")
    candidate_observation = right.get("candidate")
    if not isinstance(base_observation, dict) or not isinstance(candidate_observation, dict):
        return GuestComparison("INCONCLUSIVE", "observation_missing", {}, {})
    if (
        base_observation.get("manifest_sha256") != manifest_sha256
        or candidate_observation.get("manifest_sha256") != manifest_sha256
        or candidate_observation.get("baseline_tree_sha256") != candidate_tree_sha256
    ):
        return GuestComparison("INCONCLUSIVE", "pinned_inputs_mismatch", {}, {})
    base_checks = _checks(base_observation)
    candidate_checks = _checks(candidate_observation)
    if base_checks is None or candidate_checks is None or set(base_checks) != set(candidate_checks):
        return GuestComparison("INCONCLUSIVE", "check_receipts_invalid", {}, {})
    if base_observation.get("status") != "BASELINE_RECORDED":
        return GuestComparison("INCONCLUSIVE", "baseline_environment_unavailable",
                               base_checks, candidate_checks)
    if not any(status == "FAILED" for status in base_checks.values()):
        return GuestComparison("INCONCLUSIVE", "reported_failure_not_reproduced",
                               base_checks, candidate_checks)
    if candidate_observation.get("status") != "CANDIDATE_RECORDED":
        return GuestComparison("INCONCLUSIVE", "candidate_environment_unavailable",
                               base_checks, candidate_checks)
    if any(status != "PASSED" for status in candidate_checks.values()):
        return GuestComparison("FAILED", "candidate_declared_check_failed",
                               base_checks, candidate_checks)
    return GuestComparison("SUPPORTED", "declared_checks_improved",
                           base_checks, candidate_checks)
