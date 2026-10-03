from fastapi.testclient import TestClient
from server import app

def test_health():
    assert TestClient(app).get('/__aip_health').status_code == 200
