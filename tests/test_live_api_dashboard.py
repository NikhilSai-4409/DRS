import json

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from core.api_server import create_app


@pytest.fixture(autouse=True)
def _isolated_session(tmp_path, monkeypatch):
    # Keep tests off the operator's real desktop session + match archive on disk.
    # (new_match archives-then-clears the current match, so this must be isolated.)
    monkeypatch.setattr("core.api_server.SESSION_PATH", tmp_path / "session.json")
    monkeypatch.setattr("core.api_server.MATCHES_DIR", tmp_path / "matches")


@pytest.mark.asyncio
async def test_dashboard_backend_routes_without_camera_startup():
    app = create_app([0], record=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        health = await client.get("/api/system/health")
        cameras = await client.get("/api/cameras/fps")
        decision = await client.get("/api/decision/current")
        review = await client.post("/api/appeal/request", json={"camera_ids": [0]})
        replay = await client.post("/api/replay/control", json={"action": "pause"})
        export = await client.post("/api/replay/export")
        mode = await client.post("/api/analysis-mode", json={"mode": "thermal_demo"})

    assert health.status_code == 200
    assert "camera_fps" in health.json()
    assert cameras.status_code == 200
    assert cameras.json()["cameras"][0]["id"] == 0
    assert "status" in decision.json()
    assert review.status_code == 200
    assert "decision" in review.json()
    assert replay.status_code == 200
    assert "total_frames" in replay.json()
    assert export.status_code == 200
    assert export.json()["path"].endswith(".mp4")
    assert mode.json()["id"] == "thermal_demo"


@pytest.mark.asyncio
async def test_audio_waveform_honest_without_microphone():
    # No lifespan startup here, so audio_pipeline never exists: the waveform route
    # must degrade honestly (same contract as /api/audio/edge), never fabricate data.
    app = create_app([0], record=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/audio/waveform")

    assert response.status_code == 200
    body = response.json()
    assert body["available"] is False
    assert body["buckets"] == []
    assert "reason" in body


@pytest.mark.asyncio
async def test_broadcast_export_honest_without_replay_frames():
    # No cameras started -> no frozen buffer with frames: the export must refuse
    # with a clear reason, never write an empty/fabricated clip.
    app = create_app([0], record=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/broadcast/export", json={})

    assert response.status_code == 409
    assert "replay buffer" in response.json()["detail"]


@pytest.mark.asyncio
async def test_confirm_decision_records_review_history():
    app = create_app([0], record=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        confirm = await client.post("/api/decision/confirm", json={"outcome": "OUT"})
        reviews = await client.get("/api/reviews")

    assert confirm.status_code == 200
    assert confirm.json()["status"] == "OUT"
    assert reviews.json()["reviews"][0]["decision"] == "OUT"


@pytest.mark.asyncio
async def test_reset_returns_to_idle_after_confirmation():
    # RESULT -> IDLE: confirming records the review, then /api/decision/reset clears
    # the active review so the dashboard shows WAITING again (the recorded review
    # stays in history). Without this the 5s poll would snap the UI back to the verdict.
    app = create_app([0], record=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/decision/confirm", json={"outcome": "OUT"})
        reset = await client.post("/api/decision/reset")
        after = await client.get("/api/decision/current")
        reviews = await client.get("/api/reviews")

    assert reset.status_code == 200
    assert reset.json()["status"] == "WAITING"
    assert after.json()["status"] == "WAITING"
    # The confirmed verdict is preserved in history, not in the active decision.
    assert reviews.json()["reviews"][0]["decision"] == "OUT"


@pytest.mark.asyncio
async def test_new_match_archives_current_and_starts_empty():
    app = create_app([0], record=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/decision/confirm", json={"outcome": "OUT"})
        before = await client.get("/api/match/current")
        started = await client.post("/api/match/new", json={"name": "India vs Australia"})
        after = await client.get("/api/match/current")
        history = await client.get("/api/matches")

    assert before.json()["review_count"] == 1                     # current match had one review
    assert started.json()["name"] == "India vs Australia"
    assert after.json()["review_count"] == 0                      # the new match starts empty
    assert after.json()["name"] == "India vs Australia"
    archived = history.json()["matches"]
    assert len(archived) == 1 and archived[0]["review_count"] == 1  # old match went to history


@pytest.mark.asyncio
async def test_active_review_is_interrupted_not_resumed(tmp_path):
    # A review in flight when the app closed must NOT resume: the dashboard opens
    # WAITING and the unfinished review is recorded as INTERRUPTED.
    (tmp_path / "session.json").write_text(json.dumps({
        "current_match": {"id": "match_1", "name": "M", "teams": {}, "overs": None,
                          "started_at": 0, "reviews": []},
        "current_decision": {"status": "OUT", "outcome": "OUT"},
    }), encoding="utf-8")

    app = create_app([0], record=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        decision = await client.get("/api/decision/current")
        match = await client.get("/api/match/current")

    assert decision.json()["status"] == "WAITING"                 # active review not resumed
    assert match.json()["reviews"][0]["decision"] == "INTERRUPTED"


@pytest.mark.asyncio
async def test_match_persists_across_restart_but_active_review_does_not():
    # Item 6: verify persistence. A fresh backend (simulating an app restart) must
    # resume the current match's name + reviews, but always open in WAITING.
    app1 = create_app([0], record=False)
    async with AsyncClient(transport=ASGITransport(app=app1), base_url="http://test") as c1:
        await c1.post("/api/match/new", json={"name": "India vs Australia"})
        await c1.post("/api/decision/confirm", json={"outcome": "OUT"})
        await c1.post("/api/decision/reset")  # the UI always resets to IDLE after confirming

    app2 = create_app([0], record=False)  # brand-new backend reads the saved session
    async with AsyncClient(transport=ASGITransport(app=app2), base_url="http://test") as c2:
        match = (await c2.get("/api/match/current")).json()
        decision = (await c2.get("/api/decision/current")).json()

    assert match["name"] == "India vs Australia"   # match name resumed
    assert match["review_count"] == 1              # its review persisted
    assert decision["status"] == "WAITING"         # active review did NOT resume


def test_live_websocket_streams_camera_frame_payloads(monkeypatch):
    # Camera 9999 never exists, so this needs the synthetic-feed fallback to produce
    # frames. That fallback is OFF by default (DRS_SYNTHETIC_CAMERAS=0) so real absent
    # cameras report "not connected" instead of faking a feed — enable it just here.
    monkeypatch.setattr("core.camera_manager.SYNTHETIC_CAMERAS", True)
    app = create_app([9999], record=False)
    with TestClient(app) as client:
        with client.websocket_connect("/ws/live") as websocket:
            payload = websocket.receive_json()
            for _ in range(8):
                if payload.get("frames", {}).get("9999", {}).get("jpeg_base64"):
                    break
                payload = websocket.receive_json()

    assert payload["type"] == "live"
    assert payload["cameras"][0]["connected"] is True
    assert payload["frames"]["9999"]["jpeg_base64"]
