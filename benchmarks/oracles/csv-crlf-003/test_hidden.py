"""Independent hidden checks; never mount beside the repair source."""
from fastapi.testclient import TestClient
from server import app

def test_reported_case():
    response = TestClient(app).post('/api/check', json={'value': 'name,qty\r\nWidget,1\r\n'})
    assert response.status_code == 200
    assert response.json() == {'message': 'Accepted'}

def test_control_case():
    response = TestClient(app).post('/api/check', json={'value': 'name,qty\nWidget,1\n'})
    assert response.status_code == 200
    assert response.json() == {'message': 'Accepted'}
