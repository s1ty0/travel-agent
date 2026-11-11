"""Cross-invocation behavior against a disposable PostgreSQL database in CI."""

import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from psycopg.conninfo import make_conninfo
from test_durable_tasks import OTHER, OWNER, output

from travel_agent.postgres_tasks import PostgresTaskStore
from travel_agent.trial import Admission, open_key
from travel_agent.web_api import WebPlanRequest


@pytest.fixture
def dsn():
    value = os.environ.get("TRAVEL_TEST_POSTGRES_DSN")
    if not value:
        pytest.skip("disposable PostgreSQL not configured")
    schema = "test_" + uuid4().hex
    with psycopg.connect(value, autocommit=True) as db:
        db.execute(f"CREATE SCHEMA {schema}")
        try:
            yield make_conninfo(value, options=f"-c search_path={schema}")
        finally:
            db.execute(f"DROP SCHEMA {schema} CASCADE")


def test_postgres_independent_instances_atomically_admit_claim_and_read(dsn, monkeypatch):
    monkeypatch.setenv("TRAVEL_TASK_KEY_ENCRYPTION", Fernet.generate_key().decode())
    api, worker = PostgresTaskStore(dsn), PostgresTaskStore(dsn)
    key = "sk-test-postgres-session-key"
    api.save_key(OWNER, key)
    request = WebPlanRequest(request_id=uuid4(), idea="杭州一天")
    admission = Admission("", 0)
    with ThreadPoolExecutor(max_workers=4) as pool:
        submitted = list(
            pool.map(lambda _: api.create(OWNER, request, admission=admission), range(4))
        )
    assert sum(fresh for _, fresh in submitted) == 1
    task_id = submitted[0][0]["task_id"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        claimed = [
            item for item in pool.map(lambda i: worker.claim(str(i), task_id), range(4)) if item
        ]
    assert len(claimed) == 1 and claimed[0]["admitted"]
    assert open_key(task_id, claimed[0]["encrypted_key"]) == key
    with worker.db() as db:
        owner = db.execute("SELECT worker FROM tasks WHERE id=?", (task_id,)).fetchone()[0]
    worker.finish(task_id, owner, output=output())
    assert api.get(OWNER, task_id)["state"] == "completed"
    assert api.history(OTHER) == []
    assert api.delete(OWNER, task_id)["deleted"]


def test_postgres_network_trial_is_shared_across_anonymous_sessions(dsn):
    store = PostgresTaskStore(dsn)
    admission = Admission("1" * 64, 20)
    for _ in range(10):
        request = WebPlanRequest(request_id=uuid4(), idea="杭州一天")
        task, fresh = store.create(OWNER, request, admission=admission)
        assert fresh and store.claim("worker", task["task_id"])["admitted"]
        store.finish(task["task_id"], "worker", output=output())
    request = WebPlanRequest(request_id=uuid4(), idea="杭州一天")
    with pytest.raises(HTTPException, match="own_deepseek_key_required"):
        store.create(OTHER, request, admission=admission)
