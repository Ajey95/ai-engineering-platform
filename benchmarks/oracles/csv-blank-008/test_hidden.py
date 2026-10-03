"""Independent hidden checks; never mount beside the repair source."""
from fastapi.testclient import TestClient
from server import app

def test_reported_case():
    response = TestClient(app).post('/api/check', json={'value': 'name,qty\nA,1\n\n'})
    assert response.status_code == 200
    assert response.json() == {'message': 'Accepted'}

def test_control_case():
    response = TestClient(app).post('/api/check', json={'value': 'name,qty\nA,1\n'})
    assert response.status_code == 200
    assert response.json() == {'message': 'Accepted'}
