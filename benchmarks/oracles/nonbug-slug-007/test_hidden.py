"""Independent hidden checks; never mount beside the repair source."""
from fastapi.testclient import TestClient
from server import app

def test_reported_case():
    response = TestClient(app).post('/api/check', json={'value': 'Bad-Slug'})
    assert response.status_code == 422
    assert response.json() == {'message': 'Rejected'}

def test_control_case():
    response = TestClient(app).post('/api/check', json={'value': 'good-slug'})
    assert response.status_code == 200
    assert response.json() == {'message': 'Accepted'}
