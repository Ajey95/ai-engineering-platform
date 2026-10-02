from copy import deepcopy

from platform_app.guest_comparison import compare_guest_observations
from platform_app.sandbox_transport import GuestOutput


def _output(phase, lease, source, status, browser):
    tree = "a" * 64 if phase == "baseline" else "b" * 64
    observation = {
        "status": status, "manifest_sha256": "m" * 64,
        "baseline_tree_sha256": tree,
        "named_tests": {"unit": {
            "status": "PASSED", "tested_tree_sha256": tree,
            "post_test_tree_sha256": tree,
        }},
        "browser": {
            "status": browser, "tested_tree_sha256": tree,
            "post_browser_tree_sha256": tree,
        },
    }
    return GuestOutput({
        "phase": phase, "lease_id": lease, "source_sha256": source,
        "guest_exit_code": 0, phase: observation,
    }, b"tar")


def test_guest_comparison_requires_reproduced_failure_and_pinned_candidate():
    baseline = _output("baseline", "lease-a", "1" * 64,
                       "BASELINE_RECORDED", "FAILED")
    candidate = _output("candidate", "lease-b", "2" * 64,
                        "CANDIDATE_RECORDED", "PASSED")
    decision = compare_guest_observations(
        baseline, candidate, manifest_sha256="m" * 64,
        candidate_tree_sha256="b" * 64,
    )
    assert decision.status == "SUPPORTED"
    assert decision.reason == "declared_checks_improved"
    no_repro = deepcopy(baseline)
    no_repro.result["baseline"]["browser"]["status"] = "PASSED"
    assert compare_guest_observations(
        no_repro, candidate, manifest_sha256="m" * 64,
        candidate_tree_sha256="b" * 64,
    ).reason == "reported_failure_not_reproduced"
    wrong_tree = compare_guest_observations(
        baseline, candidate, manifest_sha256="m" * 64,
        candidate_tree_sha256="c" * 64,
    )
    assert wrong_tree.status == "INCONCLUSIVE"
    failing_candidate = deepcopy(candidate)
    failing_candidate.result["candidate"]["named_tests"]["unit"]["status"] = "FAILED"
    assert compare_guest_observations(
        baseline, failing_candidate, manifest_sha256="m" * 64,
        candidate_tree_sha256="b" * 64,
    ).status == "FAILED"
