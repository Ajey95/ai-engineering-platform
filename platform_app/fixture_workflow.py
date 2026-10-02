"""Checkpointed LangGraph phase owner for the bounded development fixture."""

from __future__ import annotations

from typing import Protocol, TypedDict

from langgraph.graph import END, START, StateGraph


class FixtureState(TypedDict, total=False):
    run_id: str
    source_commit: str
    reproduced: bool
    qualified: bool
    patch_sha256: str
    passed: bool


class FixtureStages(Protocol):
    def baseline_stage(self, run_id: str, fence: int, commit: str) -> bool: ...
    def qualification_stage(self, run_id: str, fence: int) -> bool: ...
    def patch_stage(self, run_id: str, fence: int, commit: str) -> str: ...
    def verification_stage(self, run_id: str, fence: int, commit: str) -> bool: ...


def build_fixture_workflow(
    stages: FixtureStages, run_id: str, fence: int, commit: str, checkpointer
):
    """Only IDs, a pinned revision and phase outcomes enter checkpoint state."""
    workflow = StateGraph(FixtureState)

    def baseline(_: FixtureState) -> dict:
        return {"reproduced": stages.baseline_stage(run_id, fence, commit)}

    def qualify(_: FixtureState) -> dict:
        return {"qualified": stages.qualification_stage(run_id, fence)}

    def patch(_: FixtureState) -> dict:
        return {"patch_sha256": stages.patch_stage(run_id, fence, commit)}

    def verify(_: FixtureState) -> dict:
        return {"passed": stages.verification_stage(run_id, fence, commit)}

    workflow.add_node("baseline", baseline)
    workflow.add_node("qualify", qualify)
    workflow.add_node("patch", patch)
    workflow.add_node("verify", verify)
    workflow.add_edge(START, "baseline")
    workflow.add_conditional_edges(
        "baseline", lambda state: "qualify" if state["reproduced"] else END
    )
    workflow.add_conditional_edges(
        "qualify", lambda state: "patch" if state["qualified"] else END
    )
    workflow.add_edge("patch", "verify")
    workflow.add_edge("verify", END)
    return workflow.compile(checkpointer=checkpointer)


def invoke_fixture_workflow(graph, run_id: str, commit: str) -> FixtureState:
    config = {"configurable": {"thread_id": run_id}}
    snapshot = graph.get_state(config)
    if snapshot.values:
        if (
            snapshot.values.get("run_id") != run_id
            or snapshot.values.get("source_commit") != commit
        ):
            raise ValueError("Checkpoint scope or revision changed")
        if not snapshot.next:
            return snapshot.values
        return graph.invoke(None, config, durability="sync")
    return graph.invoke(
        {"run_id": run_id, "source_commit": commit}, config, durability="sync"
    )
