from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
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


def test_cloud_cannot_create_paid_tasks_when_trial_guard_is_disabled(durable_api, monkeypatch):
    client, _ = durable_api
    monkeypatch.delenv("TRAVEL_TRIAL_ENABLED", raising=False)
    result = client.post(
        "/v1/web/tasks",
        json={"request_id": str(uuid4()), "idea": "杭州一天"},
        headers={**headers(), "x-travel-cloud": "1"},
    )
    assert result.status_code == 503 and result.json()["detail"] == "trial_not_configured"


def test_cloud_worker_endpoint_requires_bridge_and_ownership(durable_api, monkeypatch):
    client, manager = durable_api
    monkeypatch.setenv("TRAVEL_WEB_LIVE", "0")
    monkeypatch.setenv("TRAVEL_DEMO_MODE", "1")
    monkeypatch.setenv("TRAVEL_VERCEL_WORKER", "1")
    monkeypatch.setenv("TRAVEL_DATABASE_URL", "postgresql://synthetic-only")
    response = client.post(
        "/v1/web/tasks", headers=headers(), json={"request_id": str(uuid4()), "idea": "杭州一天"}
    )
    assert response.status_code == 202
    path = f"/v1/web/tasks/{response.json()['task_id']}/work"
    assert client.post(path, headers=headers(OTHER)).status_code == 404
    assert client.post(path).status_code == 403
    assert client.post(path, headers=headers()).json() == {"claimed": True}
    assert client.post(path, headers=headers()).json() == {"claimed": False}
    assert manager.get(OWNER, response.json()["task_id"])["state"] == "completed"


def test_anonymous_key_is_save_once_per_session_and_can_be_removed(durable_api, monkeypatch):
    client, manager = durable_api
    monkeypatch.setenv("TRAVEL_TRIAL_ENABLED", "1")
    monkeypatch.setenv("TRAVEL_TRIAL_GLOBAL_CAP", "0")
    monkeypatch.setenv("TRAVEL_TASK_KEY_ENCRYPTION", Fernet.generate_key().decode())
    captured = []

    def runner(*args, user_api_key, **kwargs):
        captured.append(user_api_key)
        return output()

    manager.runner = runner
    key = "sk-synthetic-user-test-key"
    saved = client.post("/v1/web/credentials", headers={**headers(), "x-travel-deepseek-key": key})
    assert saved.json() == {"has_user_key": True} and key not in saved.text
    assert client.get("/v1/web/config", headers=headers()).json()["has_user_key"] is True
    assert client.get("/v1/web/config", headers=headers(OTHER)).json()["has_user_key"] is False
    for _ in range(2):
        result = client.post(
            "/v1/web/tasks",
            headers=headers(),
            json={"request_id": str(uuid4()), "idea": "杭州一天"},
        )
        assert result.status_code == 202
        assert manager.execute(result.json()["task_id"])
    assert captured == [key, key]
    assert client.delete("/v1/web/credentials", headers=headers()).json() == {"has_user_key": False}
    blocked = client.post(
        "/v1/web/tasks", headers=headers(), json={"request_id": str(uuid4()), "idea": "杭州一天"}
    )
    assert blocked.status_code == 403 and blocked.json()["detail"] == "own_deepseek_key_required"
