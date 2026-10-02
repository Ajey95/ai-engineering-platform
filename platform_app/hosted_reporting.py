"""Tenant-scoped display of recorded hosted guest evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from platform_app.models import Run, RunEvent, SandboxLease, Task
from platform_app.service import ServiceError


def verified_hosted_artifact(run: Run, event: RunEvent, artifact_root: Path) -> dict:
    payload = event.payload or {}
    relative = f"{run.id}/candidate/guest-review.json"
    if payload.get("scope") != "declared_guest_checks" or payload.get("artifact_ref") != relative:
        raise ServiceError("EVIDENCE_UNAVAILABLE", "Hosted review artifact is not pinned", 409)
    path = artifact_root.resolve() / run.id / "candidate" / "guest-review.json"
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_000_000:
            raise ValueError("Review artifact is unavailable")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != payload.get("artifact_sha256"):
            raise ValueError("Review artifact digest changed")
        artifact = json.loads(raw)
        if not isinstance(artifact, dict) or any(
            artifact.get(key) != payload.get(key)
            for key in ("scope", "status", "patch_sha256", "candidate_tree_sha256")
        ):
            raise ValueError("Review artifact no longer matches its receipt")
        return artifact
    except (OSError, ValueError, TypeError) as error:
        raise ServiceError(
            "EVIDENCE_UNAVAILABLE", "Hosted review artifact could not be verified", 409
        ) from error


def hosted_review_packet(
    run: Run, task: Task, leases: list[SandboxLease],
    verification: RunEvent | None, review: RunEvent | None,
    artifact_root: Path, model_completed: bool, spend_usd: str,
    publication_status: str = "DISABLED", publication_url: str | None = None,
    media_manifest_urls: dict[str, str] | None = None,
    deleted_recording_labels: list[str] | None = None,
) -> dict:
    latest = {}
    for lease in sorted(leases, key=lambda item: item.generation):
        if lease.result_sha256 and isinstance(lease.result_summary, dict):
            latest[lease.phase] = lease
    baseline = (latest.get("baseline").result_summary or {}).get("baseline", {}) \
        if "baseline" in latest else {}
    candidate = (latest.get("candidate").result_summary or {}).get("candidate", {}) \
        if "candidate" in latest else {}
    if not isinstance(baseline, dict) or not isinstance(candidate, dict):
        raise ServiceError("EVIDENCE_UNAVAILABLE", "Guest observation is malformed", 409)
    artifact = verified_hosted_artifact(run, verification, artifact_root) \
        if verification else None
    baseline_named = baseline.get("named_tests") or {}
    candidate_named = candidate.get("named_tests") or {}
    if not isinstance(baseline_named, dict) or not isinstance(candidate_named, dict):
        raise ServiceError("EVIDENCE_UNAVAILABLE", "Guest checks are malformed", 409)
    reproduced = (
        baseline.get("status") == "BASELINE_RECORDED"
        and isinstance(baseline.get("browser"), dict)
        and baseline["browser"].get("status") == "FAILED"
    )
    changed = [
        line[6:] for line in (artifact or {}).get("diff", "").splitlines()
        if line.startswith("+++ b/")
    ]
    return {
        "schema_version": "1.0", "run_id": run.id, "base_commit": run.base_commit,
        "report": task.report, "expected_behavior": task.expected_behavior,
        "actual_behavior": task.actual_behavior,
        "reproduction_status": "REPRODUCED" if reproduced else
        "INCONCLUSIVE" if baseline else "NOT_RUN",
        "qualification_scope": "declared_guest_checks" if verification else "hosted_baseline_only",
        "autonomous_repair": bool(verification and model_completed),
        "diagnosis_evidence_refs": [
            f"/v1/runs/{run.id}/guest-evidence/{phase}"
            for phase in ("baseline", "candidate") if phase in latest
        ],
        "patch_hash": (artifact or {}).get("patch_sha256"),
        "patch_url": f"/v1/runs/{run.id}/patch" if artifact else None,
        "changed_files": changed,
        "diagnosis_hypothesis": (artifact or {}).get("diagnosis"),
        "baseline_tests": [baseline_named[key] for key in sorted(baseline_named)],
        "baseline_browser": baseline.get("browser"), "baseline_oracle": None,
        "new_tests": [],
        "patched_tests": [candidate_named[key] for key in sorted(candidate_named)],
        "candidate_browser": candidate.get("browser"), "candidate_oracle": None,
        "browser_evidence_refs": [], "screenshot_urls": {},
        "verification_status": run.verdict,
        "review_decision": (review.payload or {}).get("decision") if review else None,
        "review_reason": (review.payload or {}).get("reason") if review else None,
        "publication_status": publication_status, "publication_url": publication_url,
        "limitations": [
            "The verdict covers declared guest checks only; no independent hidden oracle was run."
        ] if verification else ["Candidate verification has not finished."],
        "actual_model_spend_usd": spend_usd, "media_status": run.media_status,
        "media_manifest_urls": media_manifest_urls or {},
        "media_manifest_url": (media_manifest_urls or {}).get("candidate")
        or (media_manifest_urls or {}).get("baseline"),
        "deleted_recording_labels": deleted_recording_labels or [],
        "config_snapshot": run.config_snapshot,
    }
