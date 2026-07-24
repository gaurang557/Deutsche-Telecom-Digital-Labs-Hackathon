from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_voice_health() -> None:
    response = client.get("/api/v1/voice/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["model_loaded"] is False
    assert isinstance(body["model"], str)
