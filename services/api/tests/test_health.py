"""Health endpoints. `ready` needs the local PostgreSQL from infra/compose.yaml."""

from fastapi.testclient import TestClient

from it_procurement_api.app import app

client = TestClient(app)


def test_live():
    response = client.get("/api/v1/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_reports_database():
    response = client.get("/api/v1/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}
