import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from platform_app.db import Base
from platform_app.models import AuditEvent, Project, RepositoryConnection, Tenant
from platform_app.repository_connections import (
    environment_secret_name,
    probe_github_repository,
    qualify_repository_connection,
)
from platform_app.service import ServiceError


def test_github_read_probe_requires_identity_push_and_default_ref():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        assert request.headers["authorization"] == "Bearer local-test-token"
        if request.url.path == "/repos/team/repo":
            return httpx.Response(200, json={
                "full_name": "Team/Repo", "default_branch": "main",
                "archived": False, "disabled": False, "permissions": {"push": True},
            })
        if request.url.path == "/repos/team/repo/git/ref/heads/main":
            return httpx.Response(200, json={
                "ref": "refs/heads/main", "object": {"sha": "a" * 40},
            })
        if request.url.path == "/repos/team/repo/pulls":
            return httpx.Response(200, json=[])
        raise AssertionError(request.url)

    with httpx.Client(
        base_url="https://api.github.com", transport=httpx.MockTransport(handler)
    ) as client:
        result = probe_github_repository("team/repo", "local-test-token", client)
    assert result.ready is True
    assert result.default_branch == "main" and result.head_sha == "a" * 40
    assert seen == [
        ("GET", "/repos/team/repo"),
        ("GET", "/repos/team/repo/git/ref/heads/main"),
        ("GET", "/repos/team/repo/pulls"),
    ]


@pytest.mark.parametrize("repo_response,reason", [
    (403, "github_read_failed"),
    ({"full_name": "Other/Repo", "permissions": {"push": True}},
     "repository_identity_mismatch"),
    ({"full_name": "Team/Repo", "default_branch": "main", "permissions": {"push": False}},
     "push_permission_unconfirmed"),
])
def test_probe_fails_closed_without_remote_write(repo_response, reason):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        if isinstance(repo_response, int):
            return httpx.Response(repo_response)
        return httpx.Response(200, json=repo_response)

    with httpx.Client(
        base_url="https://api.github.com", transport=httpx.MockTransport(handler)
    ) as client:
        result = probe_github_repository("team/repo", "local-test-token", client)
    assert result.ready is False and result.reason == reason
    assert calls == ["GET"]


def test_qualification_persists_readiness_and_revokes_it_on_failed_recheck():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Tenant(id="tenant", name="A"))
        db.add(Project(id="project", tenant_id="tenant", name="A"))
        connection = RepositoryConnection(
            id="connection", tenant_id="tenant", project_id="project",
            provider="github", repository_ref="team/repo",
            credential_ref="secret://env/AIP_GITHUB_TOKEN", created_by="maintainer",
            status="unverified",
        )
        db.add(connection)
        db.commit()

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/repos/team/repo":
                return httpx.Response(200, json={
                    "full_name": "team/repo", "default_branch": "main",
                    "permissions": {"push": True},
                })
            if request.url.path.endswith("/git/ref/heads/main"):
                return httpx.Response(200, json={
                    "ref": "refs/heads/main", "object": {"sha": "b" * 40},
                })
            return httpx.Response(200, json=[])

        with httpx.Client(base_url="https://api.github.com",
                          transport=httpx.MockTransport(handler)) as client:
            assert qualify_repository_connection(
                db, connection, "local-test-token", "maintainer", client
            ).ready
        assert connection.status == "ready" and connection.checked_at is not None
        with httpx.Client(base_url="https://api.github.com",
                          transport=httpx.MockTransport(lambda _: httpx.Response(403))) as client:
            assert not qualify_repository_connection(
                db, connection, "local-test-token", "maintainer", client
            ).ready
        assert connection.status == "unverified"
        assert db.query(AuditEvent).filter_by(action="repository.connection_qualify").count() == 2
        assert environment_secret_name(connection.credential_ref) == "AIP_GITHUB_TOKEN"
        with pytest.raises(ServiceError, match="process secret reference"):
            environment_secret_name("secret://github/app")
    engine.dispose()
