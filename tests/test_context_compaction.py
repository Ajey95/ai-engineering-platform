import hashlib
import json
from datetime import UTC, datetime

import pytest

from platform_app.agent_patch import _prompt
from platform_app.context_compaction import (
    compact_fixture_context,
    compact_general_context,
    load_compacted_context,
    load_general_compacted_context,
)
from platform_app.models import Run, Task, ToolAction
from platform_app.service import ServiceError


def _scope():
    run = Run(
        id="run", tenant_id="tenant", project_id="project", task_id="task",
        created_by="actor", idempotency_key="key", request_hash="r" * 64,
        base_commit="a" * 40, model_entry_id="model", state="INVESTIGATING",
        config_snapshot={
            "policy_version": "1.0", "model_price_revision": "price-1",
            "model_price_per_m_input": "1", "model_price_per_m_output": "2",
            "model_context_limit": 32000, "model_output_limit": 4000,
            "spend_limit_usd": 5,
        },
        created_at=datetime(2026, 10, 2, tzinfo=UTC),
    )
    task = Task(
        id="task", tenant_id="tenant", project_id="project", report="Form fails",
        expected_behavior="Ticket created", actual_behavior="HTTP 500", created_by="actor",
    )
    return run, task


def test_fixture_prompt_compacts_logs_and_preserves_constraints_and_source(tmp_path):
    run, task = _scope()
    baseline = tmp_path / run.id / "baseline"
    baseline.mkdir(parents=True)
    receipts = {}
    actions = {}
    for name in ("named", "browser", "oracle"):
        raw = b"start\n" + b"noise\n" * 2000 + b"FAILED expected assertion\n"
        filename = f"{name}.log"
        (baseline / filename).write_bytes(raw)
        receipts[name] = {
            "status": "FAILED", "exit_code": 1, "output_file": filename,
            "output_sha256": hashlib.sha256(raw).hexdigest(),
        }
        actions[name] = ToolAction(
            id=f"action-{name}", tenant_id=run.tenant_id, run_id=run.id,
            step_id=name, logical_action=f"fixture.{name}", effect_key="e" * 64,
            arguments_hash="b" * 64, policy_result="allowed", status="COMPLETED",
            receipt=receipts[name],
        )
    prompt, summary_ref = _prompt(
        run, task, receipts, "x" * 12000,
        [{"event_id": "decision-1", "input_text": "Keep the current test"}],
        tmp_path, actions, False,
    )
    assert summary_ref is not None
    bundle = json.loads(prompt)
    assert bundle["permission_boundaries"].startswith("You are repairing")
    assert bundle["task_constraints"]["report"] == "Form fails"
    assert bundle["base_commit"] == run.base_commit
    assert bundle["concise_history"][0]["input_text"] == "Keep the current test"
    assert (tmp_path / summary_ref).is_file()
    replay_prompt, replay_ref = _prompt(
        run, task, receipts, "x" * 12000,
        [{"event_id": "decision-1", "input_text": "Keep the current test"}],
        tmp_path, actions, False,
    )
    assert (replay_prompt, replay_ref) == (prompt, summary_ref)
    original = json.loads((tmp_path / bundle["compaction"]["source_bundle_ref"]).read_text())
    assert original["source_items"][0]["excerpt"] == "x" * 12000
    summary, recovered = load_compacted_context(tmp_path, run.id, summary_ref)
    assert recovered == original
    assert summary["user_decisions"] == bundle["concise_history"]
    for item in bundle["source_items"][1:]:
        evidence = json.loads(item["excerpt"])
        assert evidence["tool_call_id"] == evidence["tool_result_for_call_id"]
        assert evidence["output_ref"].startswith("run/baseline/")
        assert "FAILED expected assertion" in evidence["failure_tail"]


def test_pending_tool_cycle_cannot_be_compacted(tmp_path):
    bundle = {
        "task_constraints": {"report": "r"}, "base_commit": "a" * 40,
        "permission_boundaries": "immutable", "source_items": [],
        "concise_history": [], "padding": "x" * 2000,
    }
    with pytest.raises(ServiceError) as error:
        compact_fixture_context(bundle, 1000, tmp_path, "run", pending_tool_cycle=True)
    assert error.value.code == "CONTEXT_UNSATISFIABLE"
    assert not (tmp_path / "run" / "context").exists()


def test_context_retrieval_rejects_scope_and_tampering(tmp_path):
    source = {
        "task_constraints": {"report": "r"}, "base_commit": "a" * 40,
        "task_state": {"run_id": "run"}, "permission_boundaries": "immutable",
        "source_items": [{
            "category": "tool_receipt", "source_id": "receipt-1",
            "content_sha256": "a" * 64, "excerpt": json.dumps({
                "tool_call_id": "action-1", "tool_result_for_call_id": "action-1",
                "tool_result": {"status": "FAILED", "structured_fields": {"exit_code": 1}},
                "output": {"artifact_ref": "run/baseline/test.log", "sha256": "b" * 64,
                           "head": "x" * 3000, "tail": "failed"},
            }),
        }], "concise_history": [],
    }
    _, summary_ref = compact_fixture_context(source, 3000, tmp_path, "run")
    assert summary_ref is not None
    with pytest.raises(ServiceError):
        load_compacted_context(tmp_path, "other", summary_ref)
    summary, recovered = load_compacted_context(tmp_path, "run", summary_ref)
    assert recovered == source
    assert summary["source_commit"] == source["base_commit"]
    source_file = tmp_path / summary["source_bundle_ref"]
    source_file.write_text("{}")
    with pytest.raises(ServiceError) as error:
        load_compacted_context(tmp_path, "run", summary_ref)
    assert error.value.code == "CONTEXT_INTEGRITY"


def test_general_compaction_preserves_repair_inputs_and_source_lineage(tmp_path):
    prompt = {
        "schema_version": "1.0", "task": {"report": "Fix form"},
        "base_commit": "a" * 40, "source_archive_sha256": "b" * 64,
        "allowed_paths": ["app.py"],
        "source_items": [{"path": "app.py", "base_sha256": "c" * 64,
                          "content": "def submit():\n    return False\n",
                          "trust_label": "untrusted_repository_content"}],
        "indexed_code": [{"path": "app.py", "symbols": list(range(20)),
                          "imports": list(range(20))}],
        "selected_memory": list(range(8)),
        "prior_attempts": [{"status": "FAILED", "reason": "browser check failed"}],
        "baseline": {"manifest_sha256": "d" * 64,
                     "named_tests": {"unit": "FAILED"}, "browser_status": "FAILED",
                     "browser_steps": list(range(10)), "page_errors": list(range(5)),
                     "log_excerpts": [{"path": "test-unit.log", "tail": "x" * 3500
                                       + "FAIL: form submit", "truncated": False}]},
    }
    compacted, summary_ref = compact_general_context(prompt, 4000, tmp_path, "run")
    assert summary_ref is not None
    assert compacted["source_items"] == prompt["source_items"]
    assert compacted["task"] == prompt["task"]
    assert compacted["prior_attempts"] == prompt["prior_attempts"]
    assert compacted["baseline"]["named_tests"] == {"unit": "FAILED"}
    assert compacted["baseline"]["log_excerpts"][0]["tail"].endswith(
        "FAIL: form submit"
    )
    summary, source = load_general_compacted_context(tmp_path, "run", summary_ref)
    assert source == prompt
    assert summary["source_file_hashes"][0]["base_sha256"] == "c" * 64
    with pytest.raises(ServiceError):
        load_general_compacted_context(tmp_path, "other", summary_ref)
    (tmp_path / summary["source_bundle_ref"]).write_text("{}")
    with pytest.raises(ServiceError) as error:
        load_general_compacted_context(tmp_path, "run", summary_ref)
    assert error.value.code == "CONTEXT_INTEGRITY"


def test_general_compaction_fails_closed_when_source_cannot_fit(tmp_path):
    prompt = {
        "schema_version": "1.0", "task": {"report": "Fix form"},
        "base_commit": "a" * 40, "source_archive_sha256": "b" * 64,
        "allowed_paths": ["app.py"], "indexed_code": [], "selected_memory": [],
        "prior_attempts": [],
        "source_items": [{"path": "app.py", "base_sha256": "c" * 64,
                          "content": "x" * 5000}],
        "baseline": {"manifest_sha256": "d" * 64, "named_tests": {},
                     "browser_status": "FAILED", "browser_steps": [],
                     "page_errors": [], "log_excerpts": []},
    }
    with pytest.raises(ServiceError) as error:
        compact_general_context(prompt, 3000, tmp_path, "run")
    assert error.value.code == "CONTEXT_UNSATISFIABLE"
    assert not (tmp_path / "run" / "context").exists()
