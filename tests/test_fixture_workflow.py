import os
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres import PostgresSaver

from platform_app.fixture_workflow import build_fixture_workflow, invoke_fixture_workflow


class Stages:
    def __init__(self):
        self.calls = []
        self.fail_patch_once = True

    def baseline_stage(self, run_id, fence, commit):
        self.calls.append(("baseline", fence))
        return True

    def qualification_stage(self, run_id, fence):
        self.calls.append(("qualify", fence))
        return True

    def patch_stage(self, run_id, fence, commit):
        self.calls.append(("patch", fence))
        if self.fail_patch_once:
            self.fail_patch_once = False
            raise RuntimeError("controlled interruption")
        return "a" * 64

    def verification_stage(self, run_id, fence, commit):
        self.calls.append(("verify", fence))
        return True


def test_checkpoint_resumes_at_failed_phase_with_new_fence():
    saver = InMemorySaver()
    stages = Stages()
    first = build_fixture_workflow(stages, "run", 1, "b" * 40, saver)
    with pytest.raises(RuntimeError, match="controlled interruption"):
        invoke_fixture_workflow(first, "run", "b" * 40)
    snapshot = first.get_state({"configurable": {"thread_id": "run"}})
    assert snapshot.next == ("patch",)
    second = build_fixture_workflow(stages, "run", 2, "b" * 40, saver)
    result = invoke_fixture_workflow(second, "run", "b" * 40)
    assert result["passed"] is True
    assert stages.calls == [
        ("baseline", 1), ("qualify", 1), ("patch", 1),
        ("patch", 2), ("verify", 2),
    ]
    assert invoke_fixture_workflow(second, "run", "b" * 40) == result
    with pytest.raises(ValueError, match="scope or revision"):
        invoke_fixture_workflow(second, "run", "c" * 40)


def test_postgres_checkpoint_survives_connection_restart():
    url = os.environ.get("AIP_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Set AIP_TEST_POSTGRES_URL for the PostgreSQL checkpoint gate")
    run_id = str(uuid4())
    stages = Stages()
    with PostgresSaver.from_conn_string(url) as saver:
        saver.conn.execute("SET search_path TO aip_workflow, public")
        saver.setup()
        first = build_fixture_workflow(stages, run_id, 1, "b" * 40, saver)
        with pytest.raises(RuntimeError, match="controlled interruption"):
            invoke_fixture_workflow(first, run_id, "b" * 40)
    with PostgresSaver.from_conn_string(url) as saver:
        saver.conn.execute("SET search_path TO aip_workflow, public")
        second = build_fixture_workflow(stages, run_id, 2, "b" * 40, saver)
        assert invoke_fixture_workflow(second, run_id, "b" * 40)["passed"] is True
    assert stages.calls == [
        ("baseline", 1), ("qualify", 1), ("patch", 1),
        ("patch", 2), ("verify", 2),
    ]
