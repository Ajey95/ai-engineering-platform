"""The load balancer must withdraw API targets when canonical storage is down."""

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError

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
