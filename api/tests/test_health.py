from fastapi.testclient import TestClient

from api.app.main import app


def test_liveness_is_independent_of_backing_services() -> None:
    response = TestClient(app).get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "alive"}

