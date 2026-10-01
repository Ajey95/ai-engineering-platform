from fastapi.testclient import TestClient

from server import app


def test_ticket_list_starts_empty():
    response = TestClient(app).get("/api/tickets")
    assert response.status_code == 200
    assert response.json() == []
