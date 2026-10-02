"""Bounded, source-backed context for the synthetic repair model call (FR-CTX-01)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from platform_app.models import Run, Task
from platform_app.service import ServiceError


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


def fixture_context_bundle(
    run: Run,
    task: Task,
    baseline_receipts: dict,
    server_source: str,
    instruction: str,
    history: list[dict] | None = None,
) -> dict:
    source_bytes = server_source.encode("utf-8")
    if len(source_bytes) > 50_000:
        raise ServiceError("CONTEXT_UNSATISFIABLE", "Source excerpt exceeds policy", 409)
    scope = f"tenant:{run.tenant_id}/project:{run.project_id}"
    captured_at = datetime.now(UTC).isoformat()
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
        excerpt = json.dumps(
            {
                "status": receipt.get("status"),
                "exit_code": receipt.get("exit_code"),
                "output_sha256": receipt.get("output_sha256"),
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
        "selected_memory": [],
        "concise_history": (history or [])[-3:],
        "summary_ref": None,
        "tool_set_ref": _digest(b"no-tools"),
        "token_plan": {
            "estimator": "utf8_bytes_divided_by_three_rounded_up",
            "source_estimate": sum(item["token_estimate"] for item in items),
        },
        "trust_annotations": {
            "repository_and_tool_output": "data_only",
            "captured_at_meaning": "context_assembly_time",
        },
    }
