import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from platform_app import github_publication
from platform_app.github_publication import GitHubDraftPublisher, GitHubPublicationError
from platform_app.models import PublicationApproval
from platform_app.patch_workspace import parse_patch_response
from platform_app.verifier import tree_hash

BASE = "b" * 40
BASE_TREE = "d" * 40
BLOB = "e" * 40
TREE = "f" * 40
COMMIT = "a" * 40


def _approval(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "server.py").write_text("fixed\n", encoding="utf-8", newline="\n")
    proposal = parse_patch_response(json.dumps({
        "diagnosis": "repair", "files": [{"path": "server.py", "content": "fixed\n"}],
    }))
    approval = PublicationApproval(
        id="approval", tenant_id="tenant", project_id="project", run_id="run-1",
        connection_id="connection", action="draft_pr", destination="github:team/repo@main",
        base_commit=BASE, patch_sha256=proposal.patch_sha256,
        test_evidence_sha256="1" * 64, actor="maintainer", status="approved",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    receipt = {
        "changed_files": ["server.py"], "candidate_tree_sha256": tree_hash(workspace),
    }
    return approval, workspace, receipt


@pytest.mark.parametrize("uncertain_pr_response", [False, True])
def test_github_draft_publication_reconciles_retry(
    tmp_path, monkeypatch, uncertain_pr_response
):
    monkeypatch.setattr(github_publication, "verify_draft_pr_approval", lambda *_: None)
    approval, workspace, receipt = _approval(tmp_path)
    state = {"branch": None, "pr": None, "creates": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method
        if method == "GET" and path.endswith("/git/ref/heads/main"):
            return httpx.Response(200, json={"object": {"sha": BASE}})
        if method == "GET" and path.endswith("/git/ref/heads/aip/run1"):
            return httpx.Response(200, json={"object": {"sha": state["branch"]}}) if state[
                "branch"
            ] else httpx.Response(404)
        if method == "GET" and path.endswith(f"/git/commits/{BASE}"):
            return httpx.Response(200, json={"tree": {"sha": BASE_TREE}})
        if method == "POST" and path.endswith("/git/blobs"):
            return httpx.Response(201, json={"sha": BLOB})
        if method == "POST" and path.endswith("/git/trees"):
            assert json.loads(request.content)["base_tree"] == BASE_TREE
            return httpx.Response(201, json={"sha": TREE})
        if method == "POST" and path.endswith("/git/commits"):
            return httpx.Response(201, json={"sha": COMMIT})
        if method == "POST" and path.endswith("/git/refs"):
            state["branch"] = COMMIT
            state["creates"] += 1
            return httpx.Response(201, json={"ref": "refs/heads/aip/run1"})
        if method == "GET" and path.endswith(f"/git/commits/{COMMIT}"):
            return httpx.Response(200, json={
                "message": f"AIP run {approval.run_id} patch {approval.patch_sha256}",
                "tree": {"sha": TREE}, "parents": [{"sha": BASE}],
            })
        if method == "GET" and path.endswith("/pulls"):
            return httpx.Response(200, json=[state["pr"]] if state["pr"] else [])
        if method == "POST" and path.endswith("/pulls"):
            body = json.loads(request.content)
            assert body["draft"] is True
            state["pr"] = {
                "number": 7, "html_url": "https://github.com/team/repo/pull/7",
                "draft": True, "state": "open", "head": {"sha": COMMIT},
                "base": {"ref": "main"}, "body": body["body"],
            }
            return httpx.Response(500) if uncertain_pr_response else httpx.Response(
                201, json=state["pr"]
            )
        raise AssertionError(f"Unexpected GitHub request: {method} {path}")

    with httpx.Client(
        base_url="https://api.github.com", transport=httpx.MockTransport(handler)
    ) as client:
        publisher = GitHubDraftPublisher("test-token", client)
        first = publisher.publish(
            None, approval, workspace, receipt, title="Fix form", body="Proof"
        )
        second = publisher.publish(
            None, approval, workspace, receipt, title="Fix form", body="Proof"
        )
    assert first.url == second.url == "https://github.com/team/repo/pull/7"
    assert first.reconciled is uncertain_pr_response and second.reconciled is True
    assert state["creates"] == 1


def test_publication_refuses_changed_candidate_before_remote_write(tmp_path, monkeypatch):
    monkeypatch.setattr(github_publication, "verify_draft_pr_approval", lambda *_: None)
    approval, workspace, receipt = _approval(tmp_path)

    def unexpected(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"Remote call before patch validation: {request.url}")

    with httpx.Client(
        base_url="https://api.github.com", transport=httpx.MockTransport(unexpected)
    ) as client:
        publisher = GitHubDraftPublisher("test-token", client)
        (workspace / "server.py").write_text("different\n", encoding="utf-8")
        with pytest.raises(GitHubPublicationError, match="workspace changed"):
            publisher.publish(None, approval, workspace, receipt, title="Fix form", body="Proof")
