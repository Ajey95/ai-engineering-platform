"""Independent hidden checks; never mount beside the repair source."""
from fastapi.testclient import TestClient
from server import app

def test_reported_case():
    response = TestClient(app).post('/api/check', json={'value': 'duplicate'})
    assert response.status_code == 409
    assert response.json() == {'message': 'Rejected'}

def test_control_case():
    response = TestClient(app).post('/api/check', json={'value': 'ok'})
    assert response.status_code == 200
    assert response.json() == {'message': 'Accepted'}
