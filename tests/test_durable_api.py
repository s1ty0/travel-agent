from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_durable_tasks import OTHER, OWNER, output
from test_web_api import TOKEN

from travel_agent.api import app
from travel_agent.durable_tasks import DurableTasks


@pytest.fixture
def durable_api(tmp_path, monkeypatch):
    monkeypatch.setenv("TRAVEL_WEB_TOKEN", TOKEN)
    monkeypatch.setenv("TRAVEL_WEB_LIVE", "1")
    manager = DurableTasks(output, path=tmp_path / "tasks.db", embedded=False)
    monkeypatch.setattr("travel_agent.web_api.tasks", manager)
    with TestClient(app) as client:
        yield client, manager
    manager.pool.shutdown()


def headers(owner=OWNER):
    return {"x-travel-bridge": TOKEN, "x-travel-session": owner}


def test_history_versions_cancel_and_delete_enforce_resource_owner(durable_api):
    client, manager = durable_api
    body = {"request_id": str(uuid4()), "idea": "南京一天"}
    response = client.post("/v1/web/tasks", json=body, headers=headers())
    assert response.status_code == 202
    task_id = response.json()["task_id"]
    assert manager.execute(task_id)
    assert client.get("/v1/web/tasks", headers=headers(OTHER)).json()["tasks"] == []
    for path, method in (
        (f"/v1/web/tasks/{task_id}/versions", "get"),
        (f"/v1/web/tasks/{task_id}/cancel", "post"),
        (f"/v1/web/tasks/{task_id}", "delete"),
    ):
        assert getattr(client, method)(path, headers=headers(OTHER)).status_code == 404
    versions = client.get(f"/v1/web/tasks/{task_id}/versions", headers=headers()).json()["tasks"]
    assert len(versions) == 1 and versions[0]["version"] == 1
    assert client.delete(f"/v1/web/tasks/{task_id}", headers=headers()).json()["deleted"]
    assert client.post("/v1/web/tasks", json=body, headers=headers()).status_code == 410


def test_cancel_queued_task_returns_terminal_state_without_provider_call(durable_api):
    client, manager = durable_api
    task_id = client.post(
        "/v1/web/tasks", json={"request_id": str(uuid4()), "idea": "杭州两天"}, headers=headers()
    ).json()["task_id"]
    assert (
        client.post(f"/v1/web/tasks/{task_id}/cancel", headers=headers()).json()["state"]
        == "cancelled"
    )
    assert manager.execute(task_id) is False
