"""Idempotent draft PR publication for an already approved, verified patch."""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import httpx
from sqlalchemy.orm import Session

from platform_app.general_patch import GeneralPatchError, parse_general_patch
from platform_app.models import PublicationApproval
from platform_app.patch_workspace import PatchError, parse_patch_response
from platform_app.publication import BRANCH, verify_draft_pr_approval
from platform_app.repository_connections import github_repository_ref
from platform_app.service import ServiceError
from platform_app.verifier import tree_hash

SHA = re.compile(r"^[0-9a-f]{40}$")
DESTINATION = re.compile(r"^github:([a-z0-9_.-]+/[a-z0-9_.-]+)@([A-Za-z0-9][A-Za-z0-9._/-]{0,99})$")


class GitHubPublicationError(Exception):
    pass


@dataclass(frozen=True)
class DraftPR:
    number: int
    url: str
    branch: str
    commit_sha: str
    reconciled: bool


class GitHubDraftPublisher:
    def __init__(self, token: str, client: httpx.Client | None = None):
        if not token or len(token) > 8192 or any(ord(c) < 33 for c in token):
            raise GitHubPublicationError("GitHub token is unavailable")
        self.client = client or httpx.Client(
            base_url="https://api.github.com", timeout=15, follow_redirects=False
        )
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _request(self, method: str, path: str, *, payload: dict | None = None,
                 params: dict | None = None, missing_ok: bool = False):
        try:
            response = self.client.request(
                method, path, headers=self.headers, json=payload, params=params
            )
        except httpx.HTTPError as error:
            raise GitHubPublicationError("GitHub request outcome is uncertain") from error
        if missing_ok and response.status_code == 404:
            return None
        if not 200 <= response.status_code < 300:
            raise GitHubPublicationError(f"GitHub returned HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as error:
            raise GitHubPublicationError("GitHub returned an invalid response") from error

    def _sha(self, payload: dict, *keys: str) -> str:
        try:
            for key in keys:
                payload = payload[key]
            value = payload
        except (KeyError, TypeError) as error:
            raise GitHubPublicationError("GitHub response omitted an object ID") from error
        if not isinstance(value, str) or not SHA.fullmatch(value):
            raise GitHubPublicationError("GitHub returned an invalid object ID")
        return value

    def _ref(self, repo: str, branch: str) -> str | None:
        result = self._request(
            "GET", f"/repos/{repo}/git/ref/heads/{quote(branch, safe='/')}", missing_ok=True
        )
        return self._sha(result, "object", "sha") if result is not None else None

    def _matching_pr(
        self, repo: str, branch: str, base_branch: str, commit_sha: str, marker: str
    ) -> DraftPR | None:
        owner = repo.split("/", 1)[0]
        result = self._request(
            "GET", f"/repos/{repo}/pulls",
            params={"state": "all", "head": f"{owner}:{branch}", "per_page": 100},
        )
        if not isinstance(result, list):
            raise GitHubPublicationError("GitHub pull request list is invalid")
        matches = [pr for pr in result if isinstance(pr, dict) and marker in (pr.get("body") or "")]
        if len(matches) > 1:
            raise GitHubPublicationError("Multiple pull requests match the run marker")
        if not matches:
            return None
        pr = matches[0]
        if (
            pr.get("state") != "open" or pr.get("draft") is not True
            or (pr.get("head") or {}).get("sha") != commit_sha
            or (pr.get("base") or {}).get("ref") != base_branch
            or not isinstance(pr.get("number"), int)
            or not isinstance(pr.get("html_url"), str)
            or not pr["html_url"].startswith(f"https://github.com/{repo}/pull/")
        ):
            raise GitHubPublicationError("Existing pull request conflicts with approval")
        return DraftPR(pr["number"], pr["html_url"], branch, commit_sha, True)

    def _validated_files(
        self, approval: PublicationApproval, workspace: Path, patch_receipt: dict
    ) -> dict[str, bytes]:
        if workspace.is_symlink() or tree_hash(workspace) != patch_receipt.get(
            "candidate_tree_sha256"
        ):
            raise GitHubPublicationError("Candidate workspace changed")
        names = patch_receipt.get("changed_files")
        if not isinstance(names, list) or not 1 <= len(names) <= 4 or len(set(names)) != len(names):
            raise GitHubPublicationError("Changed file list is invalid")
        files: dict[str, bytes] = {}
        for name in names:
            if not isinstance(name, str):
                raise GitHubPublicationError("Changed file path is invalid")
            path = workspace / name
            if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(
                workspace.resolve()
            ):
                raise GitHubPublicationError("Changed file is unavailable")
            raw = path.read_bytes()
            if len(raw) > 50_000:
                raise GitHubPublicationError("Changed file exceeds patch policy")
            files[name] = raw
        try:
            if patch_receipt.get("scope") == "declared_guest_checks":
                bases = patch_receipt.get("base_sha256_by_path")
                if not isinstance(bases, dict) or set(bases) != set(names):
                    raise GitHubPublicationError("General patch bases are invalid")
                proposal = parse_general_patch(
                    json.dumps({
                        "diagnosis": "Approved publication",
                        "files": [
                            {"path": name, "base_sha256": bases[name],
                             "content": raw.decode("utf-8")}
                            for name, raw in files.items()
                        ],
                    }, ensure_ascii=False), frozenset(names),
                )
            else:
                proposal = parse_patch_response(
                    json.dumps({
                        "diagnosis": "Approved publication",
                        "files": [
                            {"path": name, "content": raw.decode("utf-8")}
                            for name, raw in files.items()
                        ],
                    }, ensure_ascii=False),
                    frozenset(names),
                )
        except (PatchError, GeneralPatchError, UnicodeDecodeError) as error:
            raise GitHubPublicationError("Candidate patch is invalid") from error
        if proposal.patch_sha256 != approval.patch_sha256:
            raise GitHubPublicationError("Candidate patch differs from approval")
        return files

    def publish(
        self, db: Session, approval: PublicationApproval, workspace: Path,
        patch_receipt: dict, *, title: str, body: str,
    ) -> DraftPR:
        verify_draft_pr_approval(db, approval)
        match = DESTINATION.fullmatch(approval.destination)
        if match is None or not SHA.fullmatch(approval.base_commit):
            raise GitHubPublicationError("Approved destination is invalid")
        repo, base_branch = match.groups()
        try:
            if github_repository_ref(f"https://github.com/{repo}") != repo:
                raise GitHubPublicationError("Approved repository identity is invalid")
        except ServiceError as error:
            raise GitHubPublicationError("Approved repository identity is invalid") from error
        if (
            not BRANCH.fullmatch(base_branch) or ".." in base_branch
            or "//" in base_branch or base_branch.endswith(("/", "."))
        ):
            raise GitHubPublicationError("Approved base branch is invalid")
        files = self._validated_files(approval, workspace, patch_receipt)
        if not 1 <= len(title) <= 200 or len(body) > 20_000:
            raise GitHubPublicationError("Pull request text exceeds policy")
        branch = f"aip/{approval.run_id.replace('-', '')[:32]}"
        marker = f"<!-- aip-run:{approval.run_id}:{approval.patch_sha256} -->"
        commit_message = f"AIP run {approval.run_id} patch {approval.patch_sha256}"
        root = f"/repos/{repo}"
        base_commit = self._request("GET", f"{root}/git/commits/{approval.base_commit}")
        base_tree = self._sha(base_commit, "tree", "sha")
        tree_items = []
        for path, raw in sorted(files.items()):
            blob = self._request("POST", f"{root}/git/blobs", payload={
                "content": base64.b64encode(raw).decode("ascii"), "encoding": "base64",
            })
            tree_items.append({
                "path": path,
                "mode": "100755" if (workspace / path).stat().st_mode & 0o111 else "100644",
                "type": "blob", "sha": self._sha(blob, "sha"),
            })
        tree = self._request("POST", f"{root}/git/trees", payload={
            "base_tree": base_tree, "tree": tree_items,
        })
        expected_tree = self._sha(tree, "sha")
        branch_sha = self._ref(repo, branch)
        if branch_sha is None:
            if self._ref(repo, base_branch) != approval.base_commit:
                raise GitHubPublicationError("Approved base branch moved")
            commit = self._request("POST", f"{root}/git/commits", payload={
                "message": commit_message, "tree": expected_tree,
                "parents": [approval.base_commit],
            })
            created_sha = self._sha(commit, "sha")
            try:
                self._request("POST", f"{root}/git/refs", payload={
                    "ref": f"refs/heads/{branch}", "sha": created_sha,
                })
                branch_sha = created_sha
            except GitHubPublicationError:
                branch_sha = self._ref(repo, branch)
                if branch_sha is None:
                    raise
        branch_commit = self._request("GET", f"{root}/git/commits/{branch_sha}")
        if (
            branch_commit.get("message") != commit_message
            or self._sha(branch_commit, "tree", "sha") != expected_tree
            or not isinstance(branch_commit.get("parents"), list)
            or [item.get("sha") for item in branch_commit["parents"]] != [approval.base_commit]
        ):
            raise GitHubPublicationError("Existing branch does not match the approved patch")
        prior = self._matching_pr(repo, branch, base_branch, branch_sha, marker)
        if prior is not None:
            return prior
        if self._ref(repo, base_branch) != approval.base_commit:
            raise GitHubPublicationError("Approved base branch moved")
        try:
            result = self._request("POST", f"{root}/pulls", payload={
                "title": title, "head": branch, "base": base_branch,
                "body": f"{body.rstrip()}\n\n{marker}", "draft": True,
            })
        except GitHubPublicationError:
            recovered = self._matching_pr(repo, branch, base_branch, branch_sha, marker)
            if recovered is not None:
                return recovered
            raise
        if (
            not isinstance(result, dict) or result.get("draft") is not True
            or (result.get("head") or {}).get("sha") != branch_sha
            or not isinstance(result.get("number"), int)
            or not isinstance(result.get("html_url"), str)
            or not result["html_url"].startswith(f"https://github.com/{repo}/pull/")
        ):
            raise GitHubPublicationError("GitHub pull request response is inconsistent")
        return DraftPR(result["number"], result["html_url"], branch, branch_sha, False)
