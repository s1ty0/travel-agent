"""Local durable runner; queued jobs are claimed once, interrupted paid work never replays."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock, Thread
from uuid import uuid4

from fastapi import HTTPException

from .task_store import TaskStore
from .trial import open_key, trial_enabled


class DurableTasks:
    def __init__(self, runner, *, path=None, embedded=True, database_url=None):
        self.runner = runner
        self.path = path or os.environ.get("TRAVEL_TASK_DB", "runtime/tasks.sqlite3")
        self.embedded = embedded
        self.database_url = database_url
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="travel-durable")
        self.lock, self._store = Lock(), None

    @property
    def store(self):
        with self.lock:
            if self._store is None:
                if self.database_url:
                    from .postgres_tasks import PostgresTaskStore

                    self._store = PostgresTaskStore(self.database_url)
                else:
                    self._store = TaskStore(self.path)
            return self._store

    def create(self, owner, data, *, admission=None):
        view, fresh = self.store.create(owner, data, admission=admission)
        if fresh and self.embedded:
            self.pool.submit(self.execute, view["task_id"])
        return self.get(owner, view["task_id"])

    def execute(self, task_id=None):
        from .web_api import WebPlanRequest

        worker = str(uuid4())
        row = self.store.claim(worker, task_id)
        if row is None:
            return False
        task_id = row["id"]
        stopped = Event()

        def heartbeat():
            while not stopped.wait(10):
                if not self.store.heartbeat(task_id, worker):
                    break

        lease_thread = Thread(target=heartbeat, daemon=True, name="travel-task-lease")
        lease_thread.start()
        try:
            if trial_enabled() and not row["admitted"]:
                raise HTTPException(409, "task_key_expired")
            credentials = {}
            if row["encrypted_key"] is not None:
                credentials["user_api_key"] = open_key(task_id, row["encrypted_key"])
            data = WebPlanRequest.model_validate_json(row["input_json"])
            base_output = None
            if row["base_id"]:
                with self.store.db() as db:
                    base = db.execute(
                        "SELECT output_json FROM tasks WHERE id=? AND owner=?",
                        (row["base_id"], row["owner"]),
                    ).fetchone()
                if base and base["output_json"]:
                    base_output = json.loads(base["output_json"])
            from .planning_models import PlanningInput

            base_planning = (
                PlanningInput.model_validate(base_output["planning"]) if base_output else None
            )
            output = self.runner(
                data,
                base_planning=base_planning,
                base_output=base_output,
                cancel=lambda: self.store.cancelled(task_id, worker),
                progress=lambda stage, steps, cost=None: self.store.progress(
                    task_id, worker, stage, steps, cost
                ),
                **credentials,
            )
            if base_output:
                output["change_summary"] = summarize_changes(base_output, output)
            self.store.finish(task_id, worker, output=output)
        except HTTPException as exc:
            error = (
                "task_key_expired" if exc.detail == "task_key_expired" else "planning_unavailable"
            )
            self.store.finish(task_id, worker, error=error)
        except Exception:
            self.store.finish(task_id, worker, error="planning_unavailable")
        finally:
            stopped.set()
            lease_thread.join(timeout=1)
        return True

    def get(self, owner, task_id):
        return self.store.get(owner, task_id)

    def cancel(self, owner, task_id):
        return self.store.cancel(owner, task_id)

    def history(self, owner, task_id=None):
        return self.store.history(owner, task_id)

    def delete(self, owner, task_id):
        return self.store.delete(owner, task_id)


def summarize_changes(before, after):
    def items(output):
        values, seen = {}, {}
        for activity in (output.get("result", {}).get("plan") or {}).get("activities", []):
            key = (activity["day"], activity["place_source_id"], activity["kind"])
            seen[key] = seen.get(key, 0) + 1
            values[(*key, seen[key])] = (activity["start_minute"], activity["end_minute"])
        return values

    a, b = items(before), items(after)
    return {
        "added": len(b.keys() - a.keys()),
        "removed": len(a.keys() - b.keys()),
        "time_changed": sum(a[key] != b[key] for key in a.keys() & b.keys()),
        "conditions_changed": before.get("planning") != after.get("planning"),
    }
