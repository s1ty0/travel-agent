import time
from threading import Event
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from test_planner import data

from travel_agent.api import app
from travel_agent.web_api import Task, WebPlanRequest, WebTasks

TOKEN = "synthetic-web-bridge-token-0123456789"
OWNER = "a" * 64
HEADERS = {"x-travel-bridge": TOKEN, "x-travel-session": OWNER}


def payload():
    return {"request_id": str(uuid4()), "planning": data().model_dump(mode="json")}


@pytest.fixture
def web(monkeypatch):
    monkeypatch.setenv("TRAVEL_WEB_TOKEN", TOKEN)
    monkeypatch.setenv("TRAVEL_WEB_LIVE", "1")
    manager = WebTasks(lambda request: {"result": {"status": "draft"}})
    monkeypatch.setattr("travel_agent.web_api.tasks", manager)
    with TestClient(app) as client:
        yield client, manager
    manager.pool.shutdown(wait=True)


def test_web_configuration_never_exposes_credentials(web, monkeypatch):
    client, _ = web
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-do-not-disclose")
    response = client.get("/v1/web/config", headers=HEADERS)
    assert response.status_code == 200 and response.json()["live_enabled"] is True
    assert TOKEN not in response.text and "synthetic-do-not-disclose" not in response.text
    assert response.json()["production_ready"] is False


@pytest.mark.parametrize("kind", ["unconfigured", "bad_token", "missing_owner", "bad_owner"])
def test_web_bridge_requires_server_token_and_session(web, monkeypatch, kind):
    client, _ = web
    headers = dict(HEADERS)
    if kind == "unconfigured":
        monkeypatch.delenv("TRAVEL_WEB_TOKEN")
    elif kind == "bad_token":
        headers["x-travel-bridge"] = "wrong"
    elif kind == "missing_owner":
        del headers["x-travel-session"]
    else:
        headers["x-travel-session"] = "../not-a-session"
    response = client.get("/v1/web/config", headers=headers)
    assert response.status_code == (503 if kind == "unconfigured" else 403)


def test_live_flag_disabled_before_any_task_or_provider_call(web, monkeypatch):
    client, manager = web
    monkeypatch.delenv("TRAVEL_WEB_LIVE")
    response = client.post("/v1/web/tasks", json=payload(), headers=HEADERS)
    assert response.status_code == 503 and not manager.tasks


def test_tasks_are_private_and_repeated_submission_reuses_same_task(web):
    client, manager = web
    calls = []
    manager.runner = lambda request: (
        calls.append(request.request_id) or {"result": {"status": "draft"}}
    )
    request = payload()
    first = client.post("/v1/web/tasks", json=request, headers=HEADERS)
    second = client.post("/v1/web/tasks", json=request, headers=HEADERS)
    assert first.status_code == second.status_code == 202
    task_id = first.json()["task_id"]
    assert second.json()["task_id"] == task_id and len(calls) == 1
    assert client.get(f"/v1/web/tasks/{task_id}", headers=HEADERS).status_code == 200
    other = {**HEADERS, "x-travel-session": "b" * 64}
    assert client.get(f"/v1/web/tasks/{task_id}", headers=other).status_code == 404
    request["max_replans"] = 1
    assert client.post("/v1/web/tasks", json=request, headers=HEADERS).status_code == 409


def test_busy_task_does_not_enqueue_another_paid_run(web):
    client, manager = web
    entered, release = Event(), Event()

    def waiting(request):
        entered.set()
        assert release.wait(3)
        return {}

    manager.runner = waiting
    try:
        assert client.post("/v1/web/tasks", json=payload(), headers=HEADERS).status_code == 202
        assert entered.wait(1)
        response = client.post("/v1/web/tasks", json=payload(), headers=HEADERS)
        assert response.status_code == 409 and response.json()["detail"] == "planner_busy"
        assert len(manager.tasks) == 1
    finally:
        release.set()


def test_task_errors_are_safe_and_do_not_retry(web):
    client, manager = web
    calls = []

    def broken(request):
        calls.append(1)
        raise RuntimeError("private provider response and credentials")

    manager.runner = broken
    task_id = client.post("/v1/web/tasks", json=payload(), headers=HEADERS).json()["task_id"]
    for _ in range(50):
        response = client.get(f"/v1/web/tasks/{task_id}", headers=HEADERS)
        if response.json()["state"] != "running":
            break
        time.sleep(0.01)
    assert response.json()["error"] == "planning_unavailable" and len(calls) == 1
    assert "private" not in response.text and "credentials" not in response.text


def test_unknown_and_expired_task_have_same_not_found_result(web):
    client, manager = web
    task_id = str(uuid4())
    manager.tasks[task_id] = Task(
        OWNER, uuid4(), "hash", time.monotonic() - 1801, state="completed"
    )
    assert client.get(f"/v1/web/tasks/{task_id}", headers=HEADERS).status_code == 404
    assert client.get(f"/v1/web/tasks/{uuid4()}", headers=HEADERS).status_code == 404


def test_task_capacity_is_bounded_and_old_results_are_pruned(web):
    _, manager = web
    for _ in range(32):
        manager.tasks[str(uuid4())] = Task(
            OWNER, uuid4(), "hash", time.monotonic(), state="completed"
        )
    with pytest.raises(HTTPException) as error:
        manager.create(OWNER, WebPlanRequest.model_validate(payload()))
    assert error.value.status_code == 429
    for task in manager.tasks.values():
        task.created -= 1801
    result = manager.create(OWNER, WebPlanRequest.model_validate(payload()))
    assert result["task_id"] and len(manager.tasks) == 1


def test_web_page_imports_are_not_implicitly_enabled(web):
    client, manager = web
    request = payload()
    request["planning"]["references"] = [{"title": "test", "text": "synthetic"}]
    assert client.post("/v1/web/tasks", json=request, headers=HEADERS).status_code == 422
    assert not manager.tasks


def test_followup_task_resolves_base_only_with_matching_owner(web):
    client, manager = web
    base_id = str(uuid4())
    manager.tasks[base_id] = Task(
        OWNER,
        uuid4(),
        "base",
        time.monotonic(),
        state="completed",
        output={"planning": data().model_dump(mode="json")},
    )
    followup = {"request_id": str(uuid4()), "idea": "预算换为3000以内", "base_task_id": base_id}
    other = {**HEADERS, "x-travel-session": "b" * 64}
    assert client.post("/v1/web/tasks", json=followup, headers=other).status_code == 404
    captured = []

    def run(request, *, base_planning):
        captured.append(base_planning.city)
        return {"planning": base_planning.model_dump(mode="json")}

    manager.runner = run
    assert client.post("/v1/web/tasks", json=followup, headers=HEADERS).status_code == 202
    manager.pool.shutdown(wait=True)
    assert captured == ["南京"]
