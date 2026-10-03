"""Deterministic, source-backed context reduction for model requests."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from copy import deepcopy
from pathlib import Path

from platform_app.service import ServiceError


def _serialize(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _save_immutable(path: Path, content: bytes) -> None:
    if path.exists():
        if path.is_symlink() or path.read_bytes() != content:
            raise ServiceError("CONTEXT_INTEGRITY", "Context artifact changed", 409)
        return
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        if path.exists():
            if path.is_symlink() or path.read_bytes() != content:
                raise ServiceError("CONTEXT_INTEGRITY", "Context artifact changed", 409)
            return
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _context_root(artifact_root: Path, run_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", run_id):
        raise ServiceError("CONTEXT_INTEGRITY", "Run artifact scope is invalid", 409)
    root = artifact_root.resolve(strict=True)
    run_root = root / run_id
    if run_root.is_symlink():
        raise ServiceError("CONTEXT_INTEGRITY", "Run artifact scope changed", 409)
    context_root = run_root / "context"
    if context_root.is_symlink():
        raise ServiceError("CONTEXT_INTEGRITY", "Context artifact scope changed", 409)
    context_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not context_root.resolve(strict=True).is_relative_to(root):
        raise ServiceError("CONTEXT_INTEGRITY", "Context artifact scope changed", 409)
    return context_root


def load_compacted_context(
    artifact_root: Path, run_id: str, summary_ref: str
) -> tuple[dict, dict]:
    """Read a scoped summary and its original source after verifying their lineage."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", run_id):
        raise ServiceError("CONTEXT_INTEGRITY", "Run artifact scope is invalid", 409)
    match = re.fullmatch(
        rf"{re.escape(run_id)}/context/summary-([0-9a-f]{{64}})\.json", summary_ref
    )
    if match is None:
        raise ServiceError("CONTEXT_INTEGRITY", "Summary reference is invalid", 409)
    root = artifact_root.resolve(strict=True)
    run_root = root / run_id
    context_root = run_root / "context"
    if run_root.is_symlink() or context_root.is_symlink():
        raise ServiceError("CONTEXT_INTEGRITY", "Context artifact scope changed", 409)
    summary_path = context_root / f"summary-{match.group(1)}.json"
    if summary_path.is_symlink() or not summary_path.is_file():
        raise ServiceError("CONTEXT_INTEGRITY", "Summary artifact is unavailable", 409)
    if summary_path.stat().st_size > 32_768:
        raise ServiceError("CONTEXT_INTEGRITY", "Summary artifact exceeds policy", 409)
    summary_bytes = summary_path.read_bytes()
    if hashlib.sha256(summary_bytes).hexdigest() != match.group(1):
        raise ServiceError("CONTEXT_INTEGRITY", "Summary artifact changed", 409)
    try:
        summary = json.loads(summary_bytes)
        digest = summary["source_bundle_sha256"]
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ValueError("Invalid source digest")
        if summary["source_bundle_ref"] != f"{run_id}/context/{digest}.json":
            raise ValueError("Source reference differs")
        source_path = context_root / f"{digest}.json"
        if source_path.is_symlink() or not source_path.is_file():
            raise ValueError("Source artifact is unavailable")
        if source_path.stat().st_size > 1_000_000:
            raise ValueError("Source artifact exceeds policy")
        source_bytes = source_path.read_bytes()
        if hashlib.sha256(source_bytes).hexdigest() != digest:
            raise ValueError("Source artifact changed")
        source = json.loads(source_bytes)
        if (
            summary["task_requirements"] != source["task_constraints"]
            or summary["source_commit"] != source["base_commit"]
            or summary["user_decisions"] != source["concise_history"]
            or source["task_state"]["run_id"] != run_id
        ):
            raise ValueError("Required summary fields differ")
    except (KeyError, TypeError, ValueError, OSError) as error:
        raise ServiceError("CONTEXT_INTEGRITY", "Context lineage is invalid", 409) from error
    return summary, source


def compact_fixture_context(
    bundle: dict,
    input_capacity_bytes: int,
    artifact_root: Path,
    run_id: str,
    *,
    next_required_bytes: int = 0,
    pending_tool_cycle: bool = False,
) -> tuple[dict, str | None]:
    """Keep immutable constraints and call pairing; save the complete source bundle."""
    if input_capacity_bytes <= 0 or next_required_bytes < 0:
        raise ValueError("Context capacity must be positive")
    original = _serialize(bundle)
    trigger = len(original) * 5 >= input_capacity_bytes * 4
    overflow = len(original) + next_required_bytes > input_capacity_bytes
    if not trigger and not overflow:
        return bundle, None
    if pending_tool_cycle:
        raise ServiceError("CONTEXT_UNSATISFIABLE", "Pending tool cycle cannot be compacted", 409)
    digest = hashlib.sha256(original).hexdigest()
    source_ref = f"{run_id}/context/{digest}.json"
    compacted = deepcopy(bundle)
    facts = []
    attempts = []
    artifacts = []
    for item in compacted.get("source_items", []):
        if item.get("category") != "tool_receipt":
            continue
        evidence = json.loads(item["excerpt"])
        result = evidence.get("tool_result") or {}
        output = evidence.get("output") or {}
        status = result.get("status")
        facts.append({"source_id": item["source_id"], "status": status})
        if status in {"FAILED", "TIMEOUT", "INCONCLUSIVE", "ERROR"}:
            attempts.append(item["source_id"])
        artifacts.extend(result.get("artifact_refs") or [])
        reduced = {
            "tool_call_id": evidence.get("tool_call_id"),
            "tool_result_for_call_id": evidence.get("tool_result_for_call_id"),
            "status": status,
            "exit_code": (result.get("structured_fields") or {}).get("exit_code"),
            "output_ref": output.get("artifact_ref"),
            "output_sha256": output.get("sha256"),
            "failure_tail": (output.get("tail") or "")[-512:],
        }
        excerpt = json.dumps(reduced, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        item["compacted_from_sha256"] = item["content_sha256"]
        item["excerpt"] = excerpt
        item["content_sha256"] = hashlib.sha256(excerpt.encode()).hexdigest()
        item["token_estimate"] = (len(excerpt.encode()) + 2) // 3
    summary = {
        "schema_version": "1.0",
        "source_bundle_ref": source_ref,
        "source_bundle_sha256": digest,
        "task_requirements": compacted["task_constraints"],
        "source_commit": compacted["base_commit"],
        "current_plan": compacted.get("current_plan"),
        "confirmed_facts": facts,
        "competing_hypotheses": compacted.get("competing_hypotheses", []),
        "failed_attempts": attempts,
        "test_results": facts,
        "artifact_references": sorted(set(artifacts)),
        "unresolved_actions": compacted.get("unresolved_actions", []),
        "user_decisions": compacted.get("concise_history", []),
    }
    summary_bytes = _serialize(summary)
    if len(summary_bytes) > 32_768:
        raise ServiceError("CONTEXT_UNSATISFIABLE", "Summary exceeds policy", 409)
    summary_digest = hashlib.sha256(summary_bytes).hexdigest()
    summary_ref = f"{run_id}/context/summary-{summary_digest}.json"
    compacted["summary_ref"] = summary_ref
    compacted["compaction"] = {
        "source_bundle_ref": source_ref,
        "source_bundle_sha256": digest,
        "summary_sha256": summary_digest,
        "method": "deterministic_fixture_evidence_reduction",
    }
    if len(_serialize(compacted)) + next_required_bytes > input_capacity_bytes:
        raise ServiceError("CONTEXT_UNSATISFIABLE", "Required context cannot fit", 409)
    context_root = _context_root(artifact_root, run_id)
    _save_immutable(context_root / f"{digest}.json", original)
    _save_immutable(context_root / f"summary-{summary_digest}.json", summary_bytes)
    return compacted, summary_ref


def compact_general_context(
    prompt: dict,
    input_capacity_bytes: int,
    artifact_root: Path,
    run_id: str,
    *,
    next_required_bytes: int = 0,
    pending_tool_cycle: bool = False,
) -> tuple[dict, str | None]:
    """Reduce secondary evidence, retaining complete source needed for a replacement patch."""
    if input_capacity_bytes <= 0 or next_required_bytes < 0:
        raise ValueError("Context capacity must be positive")
    original = _serialize(prompt)
    if (len(original) * 5 < input_capacity_bytes * 4
            and len(original) + next_required_bytes <= input_capacity_bytes):
        return prompt, None
    if pending_tool_cycle:
        raise ServiceError("CONTEXT_UNSATISFIABLE", "Pending tool cycle cannot be compacted", 409)
    source_digest = hashlib.sha256(original).hexdigest()
    source_ref = f"{run_id}/context/{source_digest}.json"
    compacted = deepcopy(prompt)
    baseline = compacted["baseline"]
    for excerpt in baseline.get("log_excerpts", []):
        excerpt["tail"] = excerpt["tail"][-512:]
        excerpt["truncated"] = True
    baseline["browser_steps"] = baseline.get("browser_steps", [])[-3:]
    baseline["page_errors"] = baseline.get("page_errors", [])[-3:]
    for item in compacted.get("indexed_code", []):
        item["symbols"] = item.get("symbols", [])[:5]
        item["imports"] = item.get("imports", [])[:5]
    # Verified memory can be recovered from the source artifact. It is less
    # important than the exact source and baseline failure for this proposal.
    compacted["selected_memory"] = compacted.get("selected_memory", [])[:3]
    summary = {
        "schema_version": "1.0",
        "method": "deterministic_general_evidence_reduction",
        "source_bundle_ref": source_ref,
        "source_bundle_sha256": source_digest,
        "task": compacted["task"],
        "base_commit": compacted["base_commit"],
        "source_archive_sha256": compacted["source_archive_sha256"],
        "allowed_paths": compacted["allowed_paths"],
        "source_file_hashes": [
            {"path": item["path"], "base_sha256": item["base_sha256"]}
            for item in compacted["source_items"]
        ],
        "prior_attempts": compacted.get("prior_attempts", []),
        "current_plan": None,
        "confirmed_facts": {
            "named_tests": baseline.get("named_tests"),
            "browser_status": baseline.get("browser_status"),
        },
        "competing_hypotheses": [],
        "failed_attempts": [
            item for item in compacted.get("prior_attempts", [])
            if item.get("status") == "FAILED"
        ],
        "test_results": baseline.get("named_tests"),
        "artifact_references": [source_ref],
        "unresolved_actions": [],
        "user_decisions": [],
        "baseline_manifest_sha256": baseline.get("manifest_sha256"),
        "baseline_named_tests": baseline.get("named_tests"),
        "baseline_browser_status": baseline.get("browser_status"),
    }
    summary_bytes = _serialize(summary)
    if len(summary_bytes) > 32_768:
        raise ServiceError("CONTEXT_UNSATISFIABLE", "Summary exceeds policy", 409)
    summary_digest = hashlib.sha256(summary_bytes).hexdigest()
    summary_ref = f"{run_id}/context/summary-{summary_digest}.json"
    compacted["summary_ref"] = summary_ref
    compacted["compaction"] = {
        "source_bundle_ref": source_ref,
        "source_bundle_sha256": source_digest,
        "summary_sha256": summary_digest,
        "method": summary["method"],
    }
    if len(_serialize(compacted)) + next_required_bytes > input_capacity_bytes:
        raise ServiceError("CONTEXT_UNSATISFIABLE", "Required context cannot fit", 409)
    context_root = _context_root(artifact_root, run_id)
    _save_immutable(context_root / f"{source_digest}.json", original)
    _save_immutable(context_root / f"summary-{summary_digest}.json", summary_bytes)
    return compacted, summary_ref


def load_general_compacted_context(
    artifact_root: Path, run_id: str, summary_ref: str
) -> tuple[dict, dict]:
    """Verify a general summary and the complete prompt it was derived from."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", run_id):
        raise ServiceError("CONTEXT_INTEGRITY", "Run artifact scope is invalid", 409)
    match = re.fullmatch(
        rf"{re.escape(run_id)}/context/summary-([0-9a-f]{{64}})\.json", summary_ref
    )
    if match is None:
        raise ServiceError("CONTEXT_INTEGRITY", "Summary reference is invalid", 409)
    root = artifact_root.resolve(strict=True)
    context_root = root / run_id / "context"
    if (root / run_id).is_symlink() or context_root.is_symlink():
        raise ServiceError("CONTEXT_INTEGRITY", "Context artifact scope changed", 409)
    summary_path = context_root / f"summary-{match.group(1)}.json"
    if summary_path.is_symlink() or not summary_path.is_file():
        raise ServiceError("CONTEXT_INTEGRITY", "Summary artifact is unavailable", 409)
    if summary_path.stat().st_size > 32_768:
        raise ServiceError("CONTEXT_INTEGRITY", "Summary artifact exceeds policy", 409)
    summary_bytes = summary_path.read_bytes()
    if hashlib.sha256(summary_bytes).hexdigest() != match.group(1):
        raise ServiceError("CONTEXT_INTEGRITY", "Summary artifact changed", 409)
    try:
        summary = json.loads(summary_bytes)
        digest = summary["source_bundle_sha256"]
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ValueError("Invalid source digest")
        if summary["source_bundle_ref"] != f"{run_id}/context/{digest}.json":
            raise ValueError("Source reference differs")
        source_path = context_root / f"{digest}.json"
        if source_path.is_symlink() or not source_path.is_file():
            raise ValueError("Source artifact is unavailable")
        if source_path.stat().st_size > 1_000_000:
            raise ValueError("Source artifact exceeds policy")
        source_bytes = source_path.read_bytes()
        if hashlib.sha256(source_bytes).hexdigest() != digest:
            raise ValueError("Source artifact changed")
        source = json.loads(source_bytes)
        expected = {
            "task": source["task"],
            "base_commit": source["base_commit"],
            "source_archive_sha256": source["source_archive_sha256"],
            "allowed_paths": source["allowed_paths"],
            "source_file_hashes": [
                {"path": item["path"], "base_sha256": item["base_sha256"]}
                for item in source["source_items"]
            ],
            "prior_attempts": source.get("prior_attempts", []),
            "current_plan": None,
            "confirmed_facts": {
                "named_tests": source["baseline"].get("named_tests"),
                "browser_status": source["baseline"].get("browser_status"),
            },
            "competing_hypotheses": [],
            "failed_attempts": [
                item for item in source.get("prior_attempts", [])
                if item.get("status") == "FAILED"
            ],
            "test_results": source["baseline"].get("named_tests"),
            "artifact_references": [summary["source_bundle_ref"]],
            "unresolved_actions": [],
            "user_decisions": [],
            "baseline_manifest_sha256": source["baseline"].get("manifest_sha256"),
            "baseline_named_tests": source["baseline"].get("named_tests"),
            "baseline_browser_status": source["baseline"].get("browser_status"),
        }
        if any(summary[key] != value for key, value in expected.items()):
            raise ValueError("Required summary fields differ")
        if source.get("schema_version") != "1.0" or summary.get("method") != (
            "deterministic_general_evidence_reduction"
        ):
            raise ValueError("Context schema differs")
    except (KeyError, TypeError, ValueError, OSError) as error:
        raise ServiceError("CONTEXT_INTEGRITY", "Context lineage is invalid", 409) from error
    return summary, source
