import pytest
from httpx import ASGITransport, AsyncClient

from core.api_server import create_app


@pytest.fixture(autouse=True)
def _isolated_session(tmp_path, monkeypatch):
    # Keep the preflight checklist off the operator's real desktop session/archive.
    monkeypatch.setattr("core.api_server.SESSION_PATH", tmp_path / "session.json")
    monkeypatch.setattr("core.api_server.MATCHES_DIR", tmp_path / "matches")


def _keys(payload):
    return {item["key"] for item in payload["items"]}


@pytest.mark.asyncio
async def test_preflight_returns_full_checklist_structure():
    app = create_app([0, 1, 2], record=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/preflight")
    assert response.status_code == 200
    payload = response.json()

    # Aggregate shape the dashboard renders.
    assert isinstance(payload["match_ready"], bool)
    assert set(payload["summary"]) == {"pass", "warn", "fail", "skip"}
    assert payload["cameras_detected"] == [0, 1, 2]

    keys = _keys(payload)
    # One row per detected camera plus every fixed system check + the summary line.
    for expected in {"camera_0", "camera_1", "camera_2", "fps_stable", "calibration",
                     "models", "storage", "replay_buffer", "audio", "gpu", "match_ready"}:
        assert expected in keys

    # Every item exposes the fields the renderer depends on.
    for item in payload["items"]:
        assert {"key", "label", "group", "status", "detail", "required", "value"} <= set(item)
        assert item["status"] in {"pass", "warn", "fail", "skip"}


@pytest.mark.asyncio
async def test_preflight_respects_selected_cameras():
    # "Select cams available": choosing a subset must drop the other camera rows and
    # never let an unused index block readiness.
    app = create_app([0, 1, 2], record=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/preflight", params={"cameras": "0,2"})
    payload = response.json()
    assert payload["cameras_selected"] == [0, 2]
    keys = _keys(payload)
    assert "camera_0" in keys and "camera_2" in keys
    assert "camera_1" not in keys


@pytest.mark.asyncio
async def test_preflight_optional_checks_toggle_required():
    app = create_app([0], record=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        default = (await client.get("/api/preflight")).json()
        strict = (await client.get("/api/preflight", params={"require_audio": "true", "require_gpu": "true"})).json()

    def item(payload, key):
        return next(i for i in payload["items"] if i["key"] == key)

    # By default audio is skippable and GPU is a soft warning (CPU fallback exists).
    assert item(default, "audio")["required"] is False
    assert item(default, "gpu")["required"] is False
    # Requiring them flips required=True; audio with no capture becomes a hard fail.
    assert item(strict, "audio")["required"] is True
    assert item(strict, "audio")["status"] == "fail"
    assert item(strict, "gpu")["required"] is True


@pytest.mark.asyncio
async def test_system_health_reports_gpu_dict():
    app = create_app([0], record=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = (await client.get("/api/system/health")).json()
    assert isinstance(payload["gpu"], dict)
    assert "available" in payload["gpu"]
