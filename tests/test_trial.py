from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from test_durable_tasks import OTHER, OWNER, output

from travel_agent.durable_tasks import DurableTasks
from travel_agent.task_store import TaskStore
from travel_agent.trial import Admission, open_key
from travel_agent.web_api import WebPlanRequest

NET = "1" * 64
OTHER_NET = "2" * 64
KEY = "sk-synthetic-user-test-key"


def data():
    return WebPlanRequest(request_id=uuid4(), idea="杭州一天")


def accept(store, owner, admission, request=None):
    task, fresh = store.create(owner, request or data(), admission=admission)
    if fresh:
        assert store.claim("worker", task["task_id"])
        store.finish(task["task_id"], "worker", output=output())
    return task


def test_trial_cap_survives_reload_deletion_new_session_and_new_network(tmp_path):
    clock = [1000.0]
    store = TaskStore(tmp_path / "tasks.db", clock=lambda: clock[0])
    trial = Admission(NET, 4)
    request = data()
    first = accept(store, OWNER, trial, request)
    assert accept(store, OWNER, trial, request)["task_id"] == first["task_id"]
    store.delete(OWNER, first["task_id"])
    accept(store, OWNER, trial)
    accept(store, OWNER, trial)
    store = TaskStore(store.path, clock=lambda: clock[0])
    with pytest.raises(HTTPException, match="own_deepseek_key_required"):
        accept(store, OTHER, trial)
    with pytest.raises(HTTPException, match="own_deepseek_key_required"):
        accept(store, OWNER, Admission(OTHER_NET, 4))
    clock[0] += 86401
    accept(store, OTHER, trial)
    with pytest.raises(HTTPException, match="own_deepseek_key_required"):
        accept(store, OTHER, Admission(OTHER_NET, 4))


def test_session_key_is_reused_and_never_charges_trial(tmp_path, monkeypatch):
    monkeypatch.setenv("TRAVEL_TRIAL_ENABLED", "1")
    monkeypatch.setenv("TRAVEL_TASK_KEY_ENCRYPTION", Fernet.generate_key().decode())
    got = []

    def runner(data, *, user_api_key, **kwargs):
        got.append(user_api_key)
        return output()

    manager = DurableTasks(runner, path=tmp_path / "tasks.db", embedded=False)
    manager.store.save_key(OWNER, KEY)
    assert manager.store.has_key(OWNER) and not manager.store.has_key(OTHER)
    task = manager.create(OWNER, data(), admission=Admission("", 0))
    with manager.store.db() as db:
        encrypted = db.execute("SELECT encrypted_key FROM task_admission").fetchone()[0]
        assert KEY.encode() not in encrypted and KEY not in str(task)
        assert KEY not in db.execute("SELECT input_json FROM tasks").fetchone()[0]
        assert db.execute("SELECT COUNT(*) FROM trial_grants").fetchone()[0] == 0
    with pytest.raises(HTTPException, match="task_key_expired"):
        open_key(str(uuid4()), encrypted)
    assert manager.execute(task["task_id"])
    assert not manager.execute(task["task_id"])
    second = manager.create(OWNER, data(), admission=Admission("", 0))
    assert manager.execute(second["task_id"])
    assert got == [KEY, KEY]
    with manager.store.db() as db:
        assert db.execute("SELECT COUNT(*) FROM task_admission").fetchone()[0] == 0
    manager.store.remove_key(OWNER)
    with pytest.raises(HTTPException, match="own_deepseek_key_required"):
        manager.create(OWNER, data(), admission=Admission("", 0))
    manager.pool.shutdown()


def test_expired_or_cancelled_keys_never_reach_worker(tmp_path, monkeypatch):
    monkeypatch.setenv("TRAVEL_TASK_KEY_ENCRYPTION", Fernet.generate_key().decode())
    clock = [1000.0]
    store = TaskStore(tmp_path / "tasks.db", clock=lambda: clock[0])
    store.save_key(OWNER, KEY)
    task, _ = store.create(OWNER, data(), admission=Admission("", 0))
    store.cancel(OWNER, task["task_id"])
    assert store.claim("worker") is None
    task, _ = store.create(OWNER, data(), admission=Admission("", 0))
    clock[0] += 301
    assert store.claim("worker") is None
    assert store.get(OWNER, task["task_id"])["error"] == "task_key_expired"
    with store.db() as db:
        assert db.execute("SELECT COUNT(*) FROM task_admission").fetchone()[0] == 0
