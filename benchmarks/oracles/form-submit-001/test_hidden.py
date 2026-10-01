"""Independent oracle; this directory must never be mounted for the repair agent."""

from fastapi.testclient import TestClient

from server import app, records


def test_omitted_quantity_creates_ticket_with_default_one():
    records.clear()
    response = TestClient(app).post("/api/tickets", json={"item_name": "Widget"})
    assert response.status_code == 201
    assert response.json()["quantity"] == 1
    assert TestClient(app).get("/api/tickets").json() == [response.json()]


def test_invalid_quantity_is_rejected():
    records.clear()
    response = TestClient(app).post("/api/tickets", json={"item_name": "Widget", "quantity": 0})
    assert response.status_code == 422
    assert records == []
