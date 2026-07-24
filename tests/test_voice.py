import io
import wave

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _silent_wav(seconds: float = 0.5, rate: int = 16000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(b"\x00\x00" * int(rate * seconds))
    return buffer.getvalue()


def test_voice_health() -> None:
    response = client.get("/api/v1/voice/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert isinstance(body["model"], str)


def test_transcribe_empty_upload_rejected() -> None:
    response = client.post(
        "/api/v1/voice/transcribe",
        files={"file": ("empty.wav", b"", "audio/wav")},
    )

    assert response.status_code == 400


def test_transcribe_returns_task_request() -> None:
    try:
        from app.voice.stt import load_model

        load_model()
    except Exception as exc:  # noqa: BLE001 - offline / model unavailable
        pytest.skip(f"whisper model unavailable: {exc}")

    response = client.post(
        "/api/v1/voice/transcribe",
        files={"file": ("clip.wav", _silent_wav(), "audio/wav")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "speech"
    assert isinstance(body["request_id"], str) and body["request_id"]
    assert isinstance(body["text"], str)
    assert "received_at" in body
