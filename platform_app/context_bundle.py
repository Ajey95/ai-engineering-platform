"""Bounded, source-backed context for the synthetic repair model call (FR-CTX-01)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from platform_app.models import MemoryFact, Run, Task, ToolAction
from platform_app.service import ServiceError
from platform_app.telemetry import tracer
from platform_app.tool_broker import ToolCallError, fixture_tool_result


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _item(
    category: str,
    locator: str,
    revision: str,
    excerpt: str,
    captured_at: str,
    trust_label: str,
    scope: str,
) -> dict:
    raw = excerpt.encode("utf-8")
    digest = _digest(raw)
    return {
        "source_id": _digest(f"{category}:{locator}:{revision}:{digest}".encode())[:24],
        "category": category,
        "locator": locator,
        "source_revision": revision,
        "content_sha256": digest,
        "excerpt": excerpt,
        "captured_at": captured_at,
        "trust_label": trust_label,
        "token_estimate": (len(raw) + 2) // 3,
        "authorization_scope": scope,
    }


def _output_excerpt(root: Path, run_id: str, filename: str, expected_sha256: str) -> dict:
    if Path(filename).name != filename or not filename or len(expected_sha256) != 64:
        raise ServiceError("EFFECT_OUTCOME_UNKNOWN", "Tool output reference is invalid", 409)
    resolved_root = root.resolve(strict=True)
    if (resolved_root / run_id).is_symlink():
        raise ServiceError("EFFECT_OUTCOME_UNKNOWN", "Tool output is unavailable", 409)
    baseline_path = resolved_root / run_id / "baseline"
    if baseline_path.is_symlink():
        raise ServiceError("EFFECT_OUTCOME_UNKNOWN", "Tool output is unavailable", 409)
    baseline = baseline_path.resolve(strict=True)
    if not baseline.is_relative_to(resolved_root):
        raise ServiceError("EFFECT_OUTCOME_UNKNOWN", "Tool output is unavailable", 409)
    artifact_path = baseline / filename
    if artifact_path.is_symlink():
        raise ServiceError("EFFECT_OUTCOME_UNKNOWN", "Tool output is unavailable", 409)
    artifact = artifact_path.resolve(strict=True)
    if not artifact.is_relative_to(baseline) or not artifact.is_file():
        raise ServiceError("EFFECT_OUTCOME_UNKNOWN", "Tool output is unavailable", 409)
    digest = hashlib.sha256()
    head = b""
    tail = b""
    size = 0
    with artifact.open("rb") as source:
        while chunk := source.read(65536):
            digest.update(chunk)
            size += len(chunk)
            if len(head) < 2048:
                head += chunk[: 2048 - len(head)]
            tail = (tail + chunk)[-2048:]
    if digest.hexdigest() != expected_sha256:
        raise ServiceError("EFFECT_OUTCOME_UNKNOWN", "Tool output digest changed", 409)
    return {
        "artifact_ref": f"{run_id}/baseline/{filename}",
        "sha256": expected_sha256,
        "bytes": size,
        "head": head.decode("utf-8", errors="replace"),
        "tail": tail.decode("utf-8", errors="replace") if size > len(head) else "",
        "truncated": size > 4096,
    }


def verified_memory_items(run: Run, memory_facts: list[MemoryFact] | None) -> list[dict]:
    """Recheck canonical scope and validity before memory enters a model prompt."""
    scope = f"tenant:{run.tenant_id}/project:{run.project_id}"
    selected_memory = []
    for fact in (memory_facts or [])[:5]:
        now = datetime.now(UTC)
        valid_from = fact.valid_from
        valid_until = fact.valid_until
        if valid_from is not None and valid_from.tzinfo is None:
            valid_from = valid_from.replace(tzinfo=UTC)
        if valid_until is not None and valid_until.tzinfo is None:
            valid_until = valid_until.replace(tzinfo=UTC)
        if (
            fact.tenant_id != run.tenant_id
            or fact.project_id != run.project_id
            or fact.source_revision != run.base_commit
            or fact.status != "verified"
            or valid_from is None
            or valid_from > now
            or valid_until is not None and valid_until <= now
            or not isinstance(fact.source_refs, list)
            or not fact.source_refs
            or not all(isinstance(ref, str) and ref for ref in fact.source_refs)
        ):
            raise ServiceError("MEMORY_SCOPE", "Selected memory is not verified for this run", 409)
        statement_bytes = fact.statement.encode("utf-8")
        selected_memory.append({
            "fact_id": fact.id,
            "subject": fact.subject,
            "statement_excerpt": fact.statement[:1000],
            "statement_sha256": _digest(statement_bytes),
            "truncated": len(fact.statement) > 1000,
            "source_revision": fact.source_revision,
            "source_refs": fact.source_refs[:3],
            "source_refs_truncated": len(fact.source_refs) > 3,
            "trust_label": "verified_project_memory_data",
            "authorization_scope": scope,
        })
    return selected_memory


@tracer.start_as_current_span("context.build")
def fixture_context_bundle(
    run: Run,
    task: Task,
    baseline_receipts: dict,
    server_source: str,
    instruction: str,
    history: list[dict] | None = None,
    artifact_root: Path | None = None,
    tool_actions: dict[str, ToolAction] | None = None,
    memory_facts: list[MemoryFact] | None = None,
) -> dict:
    source_bytes = server_source.encode("utf-8")
    if len(source_bytes) > 50_000:
        raise ServiceError("CONTEXT_UNSATISFIABLE", "Source excerpt exceeds policy", 409)
    scope = f"tenant:{run.tenant_id}/project:{run.project_id}"
    # A persisted run has a stable capture anchor so replay builds identical source IDs.
    captured_at = (run.created_at or datetime.now(UTC)).isoformat()
    items = [
        _item(
            "repository_file",
            "server.py",
            run.base_commit,
            server_source,
            captured_at,
            "untrusted_repository_content",
            scope,
        )
    ]
    for name, receipt in sorted(baseline_receipts.items()):
        pair = (tool_actions or {}).get(name)
        if artifact_root is not None and (
            pair is None
            or pair.run_id != run.id
            or pair.tenant_id != run.tenant_id
            or pair.step_id != name
            or pair.logical_action != f"fixture.{name}"
            or pair.status != "COMPLETED"
            or pair.receipt != receipt
        ):
            raise ServiceError("EFFECT_OUTCOME_UNKNOWN", "Tool call and result differ", 409)
        output = None
        if artifact_root is not None and receipt.get("output_file"):
            try:
                output = _output_excerpt(
                    artifact_root, run.id, receipt["output_file"], receipt["output_sha256"]
                )
            except (OSError, KeyError, TypeError, ValueError) as error:
                raise ServiceError(
                    "EFFECT_OUTCOME_UNKNOWN", "Tool output is unavailable", 409
                ) from error
        try:
            typed_result = fixture_tool_result(name, receipt, output, run.base_commit)
        except ToolCallError as error:
            raise ServiceError("EFFECT_OUTCOME_UNKNOWN", str(error), 409) from error
        excerpt = json.dumps(
            {
                "tool_result": asdict(typed_result),
                "tool_call_id": pair.id if pair else None,
                "tool_result_for_call_id": pair.id if pair else None,
                "output": output,
                "steps": (receipt.get("steps") or [])[:5],
                "responses": (receipt.get("responses") or [])[:5],
                "page_errors": (receipt.get("page_errors") or [])[:5],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if len(excerpt.encode("utf-8")) > 20_000:
            raise ServiceError("CONTEXT_UNSATISFIABLE", "Evidence excerpt exceeds policy", 409)
        items.append(
            _item(
                "tool_receipt",
                f"baseline:{name}",
                run.base_commit,
                excerpt,
                captured_at,
                "untrusted_tool_output",
                scope,
            )
        )
    return {
        "schema_version": "1.0",
        "task_id": task.id,
        "base_commit": run.base_commit,
        "policy_ref": run.config_snapshot.get("policy_version", "unknown"),
        "task_constraints": {
            "report": task.report,
            "expected_behavior": task.expected_behavior,
            "actual_behavior": task.actual_behavior,
        },
        "permission_boundaries": instruction,
        "task_state": {"run_id": run.id, "state": run.state},
        "source_items": items,
        "selected_memory": verified_memory_items(run, memory_facts),
        "concise_history": (history or [])[-3:],
        "summary_ref": None,
        "tool_set_ref": _digest(b"no-tools"),
        "token_plan": {
            "estimator": "utf8_bytes_divided_by_three_rounded_up",
            "source_estimate": sum(item["token_estimate"] for item in items),
        },
        "trust_annotations": {
            "repository_and_tool_output": "data_only",
            "captured_at_meaning": "run_creation_time",
        },
    }
