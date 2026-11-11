import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
from fastapi import HTTPException

from travel_agent.durable_tasks import DurableTasks
from travel_agent.task_store import TaskStore
from travel_agent.web_api import WebPlanRequest

OWNER, OTHER = "a" * 64, "b" * 64


def request(**kwargs):
    return WebPlanRequest(request_id=uuid4(), idea="南京一天", **kwargs)


def output(data=None, **kwargs):
    return {
        "city": "南京",
        "planning": {"city": "南京", "trip": {"destination": "南京", "days": 1}},
        "result": {
            "status": "draft",
            "plan": None,
            "sources": [],
            "issue": None,
            "service_cost": {
                "cap": 5_000_000,
                "estimated_upper": 0,
                "reserved_unsettled": 0,
                "status": "estimated",
            },
        },
        "markdown": "合成结果",
    }


def complete(store, task_id, worker="test"):
    assert store.claim(worker, task_id)
    store.finish(task_id, worker, output=output())


def test_same_submission_survives_restart_and_does_not_create_another_job(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    first_store, data = TaskStore(path), request()
    first, fresh = first_store.create(OWNER, data)
    assert fresh
    complete(first_store, first["task_id"])
    restarted = TaskStore(path)
    replay, fresh = restarted.create(OWNER, data)
    assert not fresh and replay["task_id"] == first["task_id"]
    assert replay["state"] == "completed" and replay["output"]["city"] == "南京"
    with restarted.db() as db:
        assert db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1
        assert db.execute("SELECT owner FROM tasks").fetchone()[0] != OWNER
    with pytest.raises(HTTPException) as exc:
        restarted.get(OTHER, first["task_id"])
    assert exc.value.status_code == 404


def test_concurrent_duplicate_creation_and_claim_are_atomic(tmp_path):
    path, data = tmp_path / "tasks.sqlite3", request()
    stores = [TaskStore(path), TaskStore(path)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        values = list(pool.map(lambda i: stores[i % 2].create(OWNER, data), range(8)))
    assert sum(fresh for _, fresh in values) == 1
    task_id = values[0][0]["task_id"]
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(lambda i: stores[i % 2].claim(str(i), task_id), range(8)))
    assert sum(claim is not None for claim in claims) == 1


def test_cancel_before_claim_is_zero_execution_and_cannot_be_claimed_again(tmp_path):
    calls = []
    manager = DurableTasks(
        lambda *_args, **_kwargs: calls.append(1), path=tmp_path / "t.db", embedded=False
    )
    task = manager.create(OWNER, request())
    assert manager.cancel(OWNER, task["task_id"])["state"] == "cancelled"
    assert manager.execute(task["task_id"]) is False and not calls
    manager.pool.shutdown()


def test_inflight_cancel_preserves_cost_and_never_replays_provider_work(tmp_path):
    entered, release = Event(), Event()
    calls = []

    def runner(data, *, cancel, progress, **kwargs):
        calls.append(1)
        progress("experience_agent", 1, {"status": "estimated", "reserved_unsettled": 250000})
        entered.set()
        assert release.wait(3)
        assert cancel()
        return output()

    manager = DurableTasks(runner, path=tmp_path / "t.db")
    try:
        task = manager.create(OWNER, request())
        assert entered.wait(2)
        view = manager.cancel(OWNER, task["task_id"])
        assert view["stage"] == "cancelling"
        assert view["service_cost_snapshot"]["reserved_unsettled"] == 250000
        release.set()
        manager.pool.shutdown(wait=True)
        view = manager.get(OWNER, task["task_id"])
        assert view["state"] == "cancelled" and view["output"]["result"]["issue"] == "cancelled"
        assert manager.execute(task["task_id"]) is False and len(calls) == 1
    finally:
        release.set()
        manager.pool.shutdown(wait=True)


def test_expired_worker_lease_becomes_interrupted_with_unknown_cost(tmp_path):
    clock = [1000.0]
    store = TaskStore(tmp_path / "t.db", clock=lambda: clock[0])
    task, _ = store.create(OWNER, request())
    assert store.claim("dead", task["task_id"])
    store.progress(
        task["task_id"],
        "dead",
        "combined_research",
        1,
        {"status": "estimated", "reserved_unsettled": 256384},
    )
    clock[0] += 61
    view = store.get(OWNER, task["task_id"])
    assert view["state"] == "interrupted" and view["error"] == "worker_interrupted_no_retry"
    assert view["service_cost_snapshot"]["status"] == "uncertain"
    assert store.claim("new") is None
    store.finish(task["task_id"], "dead", output=output())
    assert store.get(OWNER, task["task_id"])["state"] == "interrupted"


def test_versions_private_diff_and_delete_do_not_erase_idempotency(tmp_path):
    store = TaskStore(tmp_path / "t.db")
    first_request = request()
    first, _ = store.create(OWNER, first_request)
    complete(store, first["task_id"])
    second, _ = store.create(OWNER, request(base_task_id=first["task_id"]))
    assert second["version"] == 2 and second["root_id"] == first["root_id"]
    complete(store, second["task_id"])
    assert len(store.history(OWNER)) == 1 and len(store.history(OWNER, first["task_id"])) == 2
    assert store.history(OTHER) == []
    for action in (
        lambda: store.delete(OTHER, first["task_id"]),
        lambda: store.cancel(OTHER, first["task_id"]),
        lambda: store.history(OTHER, first["task_id"]),
    ):
        with pytest.raises(HTTPException) as exc:
            action()
        assert exc.value.status_code == 404
    assert store.delete(OWNER, first["task_id"])["deleted"]
    assert store.history(OWNER) == []
    with pytest.raises(HTTPException) as exc:
        store.create(OWNER, first_request)
    assert exc.value.status_code == 410


def test_deleted_jobs_cannot_reset_rolling_daily_quota(tmp_path):
    store = TaskStore(tmp_path / "t.db")
    for _ in range(20):
        task, _ = store.create(OWNER, request())
        complete(store, task["task_id"])
        store.delete(OWNER, task["task_id"])
    with pytest.raises(HTTPException) as exc:
        store.create(OWNER, request())
    assert exc.value.status_code == 429 and exc.value.detail == "daily_task_quota"


def test_request_conflict_busy_capacity_and_cancel_before_delete(tmp_path):
    store, data = TaskStore(tmp_path / "t.db"), request()
    first, _ = store.create(OWNER, data)
    with pytest.raises(HTTPException, match="request_id_conflict"):
        store.create(OWNER, data.model_copy(update={"idea": "上海一天"}))
    with pytest.raises(HTTPException, match="planner_busy"):
        store.create(OWNER, request())
    with pytest.raises(HTTPException, match="cancel_before_delete"):
        store.delete(OWNER, first["task_id"])
    store.create(OTHER, request())
    with pytest.raises(HTTPException, match="planner_busy"):
        store.create("c" * 64, request())


def test_background_exception_and_oversized_output_are_sanitized(tmp_path):
    def broken(*args, **kwargs):
        raise RuntimeError("private-body-secret")

    manager = DurableTasks(broken, path=tmp_path / "t.db", embedded=False)
    task = manager.create(OWNER, request())
    assert manager.execute(task["task_id"])
    view = manager.get(OWNER, task["task_id"])
    assert view["error"] == "planning_unavailable" and "private-body" not in json.dumps(view)
    manager.runner = lambda *args, **kwargs: {"private": "a" * 600000}
    second = manager.create(OWNER, request())
    manager.execute(second["task_id"])
    view = manager.get(OWNER, second["task_id"])
    assert view["error"] == "task_output_limit" and view["output"] is None
    manager.pool.shutdown()


def test_task_ttl_deletes_sensitive_result_but_retains_replay_tombstone(tmp_path):
    clock = [1000.0]
    store = TaskStore(tmp_path / "t.db", clock=lambda: clock[0])
    data = request()
    task, _ = store.create(OWNER, data)
    complete(store, task["task_id"])
    clock[0] += 8 * 86400
    with pytest.raises(HTTPException):
        store.get(OWNER, task["task_id"])
    assert store.history(OWNER) == []
    with pytest.raises(HTTPException) as exc:
        store.create(OWNER, data)
    assert exc.value.status_code == 410
