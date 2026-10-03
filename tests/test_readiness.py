"""The load balancer must withdraw API targets when canonical storage is down."""

from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from platform_app import api


def test_readiness_requires_database_connection(monkeypatch):
    database = create_engine("sqlite://")
    monkeypatch.setattr(api, "engine", database)
    response = TestClient(api.app).get("/v1/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
    assert api.health()["status"] == "ok"


def test_readiness_closes_on_database_failure(monkeypatch):
    class BrokenEngine:
        def connect(self):
            raise SQLAlchemyError("private detail")

    monkeypatch.setattr(api, "engine", BrokenEngine())
    response = TestClient(api.app).get("/v1/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert b"private detail" not in response.content


def test_database_outage_returns_sanitized_retryable_api_error(monkeypatch):
    monkeypatch.setattr(api, "settings", lambda: SimpleNamespace(
        environment="development", dev_token="", dev_tenant="local-tenant",
        dev_actor="local-developer",
    ))

    def failed_session():
        raise OperationalError("SELECT secret", {}, Exception("private detail"))

    api.app.dependency_overrides[api.db_session] = failed_session
    try:
        response = TestClient(api.app).get("/v1/projects")
    finally:
        api.app.dependency_overrides.pop(api.db_session)
    assert response.status_code == 503
    assert response.json()["code"] == "DATABASE_UNAVAILABLE"
    assert response.headers["Retry-After"] == "3"
    assert b"private detail" not in response.content
