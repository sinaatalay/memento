from datetime import datetime, timezone

import httpx
import pytest

from memento.app import create_app
from memento.runtime import Runtime
from memento.settings import Settings
from memento.storage import Store


@pytest.fixture
def runtime(tmp_path):
    store = Store(tmp_path / "state.sqlite3")
    instance = Runtime(Settings(data_dir=tmp_path), store)
    yield instance
    store.close()


async def test_local_event_is_persisted_before_acceptance(runtime):
    app = create_app(runtime, run_workers=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1:8877") as client:
        response = await client.post("/api/events", json={"source": "chat", "text": "Remember my flight tomorrow", "demo": True})
    assert response.status_code == 202
    event = runtime.store.events()[0]
    assert event["id"] == response.json()["event_id"]
    assert event["status"] == "queued"
    assert event["payload"]["demo"] is True


async def test_foreign_websites_cannot_advance_local_clock(runtime):
    app = create_app(runtime, run_workers=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1:8877") as client:
        response = await client.post("/api/clock", headers={"origin": "https://other.example"}, json={"hours": 24})
    assert response.status_code == 403
    assert runtime.store.setting("demo_clock_offset", 0) == 0


async def test_demo_clock_never_changes_real_clock(runtime):
    app = create_app(runtime, run_workers=False)
    before = datetime.now(timezone.utc)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1:8877") as client:
        response = await client.post("/api/clock", json={"hours": 24})
        state = (await client.get("/api/state")).json()
    assert response.status_code == 200
    real = datetime.fromisoformat(state["now"])
    demo = datetime.fromisoformat(state["demo_now"])
    assert (real - before).total_seconds() < 3
    assert abs((demo - real).total_seconds() - 86400) < 1


async def test_invalid_source_is_rejected(runtime):
    app = create_app(runtime, run_workers=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1:8877") as client:
        response = await client.post("/api/events", json={"source": "contacts", "text": "Should not enter queue"})
    assert response.status_code == 400
    assert runtime.store.events() == []


async def test_manual_stop_survives_memory_reimport_until_explicit_resume(runtime):
    from memento.memory import render_memory
    memory_id = "memories/demo/stopped"
    markdown = render_memory("A watched memory", "My plan", "", [])
    await runtime.register_markdown(memory_id, markdown)
    app = create_app(runtime, run_workers=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1:8877") as client:
        assert (await client.post(f"/api/memories/{memory_id}/stop")).status_code == 200
        await runtime.register_markdown(memory_id, markdown)
        assert runtime.store.memory(memory_id)["active"] is False
        assert (await client.post(f"/api/memories/{memory_id}/resume")).status_code == 200
        assert runtime.store.memory(memory_id)["active"] is True
