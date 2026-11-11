"""SQLite task/version repository with transactional ownership, claims and idempotency."""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from time import time
from uuid import uuid4

from fastapi import HTTPException

from .sources import fingerprint
from .trial import SESSION_KEY_TTL, seal_key

ACTIVE = ("queued", "running", "cancelling")
TTL = 7 * 86400
MAX_ACTIVE_CREDENTIALS = 200


SCHEMA = """
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, request_id TEXT NOT NULL,
                    input_hash TEXT NOT NULL, input_json TEXT NOT NULL,
                    base_id TEXT, root_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    created REAL NOT NULL, updated REAL NOT NULL, expires REAL NOT NULL,
                    state TEXT NOT NULL, stage TEXT NOT NULL, steps INTEGER NOT NULL DEFAULT 0,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    worker TEXT, lease_until REAL, output_json TEXT, error TEXT, cost_json TEXT,
                    UNIQUE(owner, request_id), UNIQUE(root_id, revision)
                );
                CREATE INDEX IF NOT EXISTS task_owner ON tasks(owner, expires);
                CREATE TABLE IF NOT EXISTS requests (
                    owner TEXT NOT NULL, request_id TEXT NOT NULL, input_hash TEXT NOT NULL,
                    task_id TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY(owner, request_id)
                );
                CREATE TABLE IF NOT EXISTS usage_events (
                    owner TEXT NOT NULL, created REAL NOT NULL, task_id TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS trial_grants (
                    task_id TEXT PRIMARY KEY, owner TEXT NOT NULL, network TEXT NOT NULL,
                    created REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS task_admission (
                    task_id TEXT PRIMARY KEY, encrypted_key BLOB, expires REAL
                );
                CREATE TABLE IF NOT EXISTS session_credentials (
                    owner TEXT PRIMARY KEY, encrypted_key BLOB NOT NULL, expires REAL NOT NULL
                );
            """


class TaskStore:
    def __init__(self, path, *, clock=time):
        self.path, self.clock = Path(path), clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _initialize(self):
        with self.db() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2):
                raise RuntimeError("unsupported_task_schema")
            db.executescript("PRAGMA journal_mode=WAL;" + SCHEMA + "PRAGMA user_version=2;")

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA secure_delete=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def owner_key(owner):
        return fingerprint(owner)

    def _recover(self, db, now):
        db.execute(
            "UPDATE tasks SET state='failed',stage='failed',error='task_key_expired',updated=? "
            "WHERE state='queued' AND id IN (SELECT task_id FROM task_admission WHERE expires<?)",
            (now, now),
        )
        db.execute("DELETE FROM task_admission WHERE expires<?", (now,))
        db.execute("DELETE FROM session_credentials WHERE expires<?", (now,))
        db.execute(
            "UPDATE tasks SET state='interrupted', stage='interrupted', "
            "error='worker_interrupted_no_retry', updated=? "
            "WHERE state IN ('running','cancelling') AND lease_until < ?",
            (now, now),
        )
        db.execute(
            "DELETE FROM tasks WHERE expires < ? AND state NOT IN ('running','cancelling')", (now,)
        )
        db.execute("DELETE FROM task_admission WHERE task_id NOT IN (SELECT id FROM tasks)")
        db.execute("DELETE FROM requests WHERE created < ?", (now - 30 * 86400,))
        db.execute("DELETE FROM usage_events WHERE created < ?", (now - 30 * 86400,))

    def create(self, owner, data, *, admission=None):
        owner = self.owner_key(owner)
        now, signature = self.clock(), fingerprint(data.model_dump_json())
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            self._recover(db, now)
            replay = db.execute(
                "SELECT * FROM requests WHERE owner=? AND request_id=?",
                (owner, str(data.request_id)),
            ).fetchone()
            if replay:
                if replay["input_hash"] != signature:
                    raise HTTPException(409, "request_id_conflict")
                row = db.execute("SELECT * FROM tasks WHERE id=?", (replay["task_id"],)).fetchone()
                if not row:
                    raise HTTPException(410, "task_removed_no_replay")
                return self.view(row), False
            base = None
            if data.base_task_id:
                base = db.execute(
                    "SELECT * FROM tasks WHERE id=? AND owner=? AND expires>?",
                    (str(data.base_task_id), owner, now),
                ).fetchone()
                if not base:
                    raise HTTPException(404, "task_not_found")
                base_output = json.loads(base["output_json"]) if base["output_json"] else {}
                if admission is not None and base_output.get("is_demo"):
                    raise HTTPException(404, "task_not_found")
                if not base_output.get("planning"):
                    raise HTTPException(409, "base_conditions_unavailable")
            active = db.execute(
                "SELECT owner FROM tasks WHERE state IN ('queued','running','cancelling')"
            ).fetchall()
            if any(row["owner"] == owner for row in active) or len(active) >= 2:
                raise HTTPException(409, "planner_busy")
            counts = db.execute(
                "SELECT owner FROM usage_events WHERE created>=?", (now - 86400,)
            ).fetchall()
            if sum(row["owner"] == owner for row in counts) >= 20 or len(counts) >= 100:
                raise HTTPException(429, "daily_task_quota")
            task_id = str(uuid4())
            root = base["root_id"] if base else task_id
            revision = db.execute(
                "SELECT COALESCE(MAX(revision),0)+1 FROM tasks WHERE root_id=?", (root,)
            ).fetchone()[0]
            if revision > 32:
                raise HTTPException(429, "version_capacity")
            if admission is not None:
                admission.admit(db, owner, task_id, now)
            db.execute(
                "INSERT INTO tasks(id,owner,request_id,input_hash,input_json,base_id,root_id,"
                "revision,created,updated,expires,state,stage) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,'queued','queued')",
                (
                    task_id,
                    owner,
                    str(data.request_id),
                    signature,
                    data.model_dump_json(),
                    str(data.base_task_id) if data.base_task_id else None,
                    root,
                    revision,
                    now,
                    now,
                    now + TTL,
                ),
            )
            db.execute(
                "INSERT INTO requests VALUES(?,?,?,?,?)",
                (owner, str(data.request_id), signature, task_id, now),
            )
            db.execute("INSERT INTO usage_events VALUES(?,?,?)", (owner, now, task_id))
            row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            return self.view(row), True

    def claim(self, worker, task_id=None):
        now = self.clock()
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            self._recover(db, now)
            row = db.execute(
                "SELECT * FROM tasks WHERE state='queued' AND cancel_requested=0 "
                + ("AND id=? " if task_id else "")
                + "ORDER BY created LIMIT 1",
                (task_id,) if task_id else (),
            ).fetchone()
            if row is None:
                return None
            db.execute(
                "UPDATE tasks SET state='running',stage='starting',worker=?,"
                "lease_until=?,updated=? "
                "WHERE id=?",
                (worker, now + 60, now, row["id"]),
            )
            admission = db.execute(
                "SELECT encrypted_key FROM task_admission WHERE task_id=?", (row["id"],)
            ).fetchone()
            if admission is not None:
                db.execute("DELETE FROM task_admission WHERE task_id=?", (row["id"],))
            return {
                **dict(row),
                "admitted": admission is not None,
                "encrypted_key": admission[0] if admission is not None else None,
            }

    def heartbeat(self, task_id, worker):
        with self.db() as db:
            return (
                db.execute(
                    "UPDATE tasks SET lease_until=? WHERE id=? AND worker=? "
                    "AND state IN ('running','cancelling')",
                    (self.clock() + 60, task_id, worker),
                ).rowcount
                == 1
            )

    def progress(self, task_id, worker, stage, steps, cost=None):
        with self.db() as db:
            db.execute(
                "UPDATE tasks SET stage=CASE WHEN cancel_requested=1 THEN 'cancelling' ELSE ? END,"
                "steps=?,updated=?,lease_until=?,cost_json=? WHERE id=? AND worker=? "
                "AND state IN ('running','cancelling')",
                (
                    stage,
                    steps,
                    self.clock(),
                    self.clock() + 60,
                    json.dumps(cost) if cost else None,
                    task_id,
                    worker,
                ),
            )

    def cancelled(self, task_id, worker):
        with self.db() as db:
            row = db.execute(
                "SELECT cancel_requested,state,worker FROM tasks WHERE id=?", (task_id,)
            ).fetchone()
            return (
                not row
                or row["worker"] != worker
                or row["cancel_requested"] == 1
                or row["state"] not in ("running", "cancelling")
            )

    def finish(self, task_id, worker, output=None, error=None):
        raw = json.dumps(output, ensure_ascii=False) if output is not None else None
        if raw is not None and len(raw.encode()) > 512 * 1024:
            raw, error = None, "task_output_limit"
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM tasks WHERE id=? AND worker=?", (task_id, worker)
            ).fetchone()
            if not row or row["state"] not in ("running", "cancelling"):
                return
            state = "cancelled" if row["cancel_requested"] else "failed" if error else "completed"
            if state == "cancelled" and output:
                from .plan_render import render_markdown

                output["result"]["status"], output["result"]["issue"] = "stopped", "cancelled"
                output["markdown"] = render_markdown(output["result"])
                raw = json.dumps(output, ensure_ascii=False)
            db.execute(
                "UPDATE tasks SET state=?,stage=?,output_json=?,error=?,updated=?,lease_until=NULL "
                "WHERE id=? AND worker=?",
                (state, state, raw, error, self.clock(), task_id, worker),
            )

    def _owned(self, db, owner, task_id):
        row = db.execute(
            "SELECT * FROM tasks WHERE id=? AND owner=? AND expires>?",
            (task_id, self.owner_key(owner), self.clock()),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "task_not_found")
        return row

    def maintenance(self):
        with self.db() as db:
            self._recover(db, self.clock())

    def get(self, owner, task_id):
        self.maintenance()
        with self.db() as db:
            return self.view(self._owned(db, owner, task_id))

    def has_key(self, owner):
        with self.db() as db:
            return (
                db.execute(
                    "SELECT 1 FROM session_credentials WHERE owner=? AND expires>=?",
                    (self.owner_key(owner), self.clock()),
                ).fetchone()
                is not None
            )

    def save_key(self, owner, key):
        owner = self.owner_key(owner)
        encrypted = seal_key(owner, key)
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            now = self.clock()
            db.execute("DELETE FROM session_credentials WHERE expires<?", (now,))
            already_saved = db.execute(
                "SELECT 1 FROM session_credentials WHERE owner=?", (owner,)
            ).fetchone()
            if (
                not already_saved
                and db.execute("SELECT COUNT(*) FROM session_credentials").fetchone()[0]
                >= MAX_ACTIVE_CREDENTIALS
            ):
                raise HTTPException(429, "credential_capacity")
            db.execute(
                "INSERT INTO session_credentials VALUES(?,?,?) ON CONFLICT(owner) DO UPDATE "
                "SET encrypted_key=excluded.encrypted_key,expires=excluded.expires",
                (owner, encrypted, now + SESSION_KEY_TTL),
            )

    def remove_key(self, owner):
        with self.db() as db:
            db.execute("DELETE FROM session_credentials WHERE owner=?", (self.owner_key(owner),))

    def cancel(self, owner, task_id):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._owned(db, owner, task_id)
            if row["state"] in ACTIVE:
                state = "cancelled" if row["state"] == "queued" else "cancelling"
                db.execute(
                    "UPDATE tasks SET cancel_requested=1,state=?,stage=?,updated=? WHERE id=?",
                    (state, state, self.clock(), task_id),
                )
                if state == "cancelled":
                    db.execute("DELETE FROM task_admission WHERE task_id=?", (task_id,))
        return self.get(owner, task_id)

    def delete(self, owner, task_id):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._owned(db, owner, task_id)
            active = db.execute(
                "SELECT 1 FROM tasks WHERE root_id=? "
                "AND state IN ('queued','running','cancelling')",
                (row["root_id"],),
            ).fetchone()
            if active:
                raise HTTPException(409, "cancel_before_delete")
            db.execute(
                "DELETE FROM tasks WHERE root_id=? AND owner=?", (row["root_id"], row["owner"])
            )
        return {"deleted": True}

    def history(self, owner, task_id=None):
        with self.db() as db:
            self._recover(db, self.clock())
            if task_id:
                row = self._owned(db, owner, task_id)
                rows = db.execute(
                    "SELECT * FROM tasks WHERE root_id=? AND owner=? AND expires>? "
                    "ORDER BY revision DESC LIMIT 32",
                    (row["root_id"], row["owner"], self.clock()),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM tasks t WHERE owner=? AND expires>? "
                    "AND revision=(SELECT MAX(revision) FROM tasks x WHERE x.root_id=t.root_id) "
                    "ORDER BY created DESC LIMIT 20",
                    (self.owner_key(owner), self.clock()),
                ).fetchall()
            return [self.summary(row) for row in rows]

    @staticmethod
    def view(row):
        cost = json.loads(row["cost_json"]) if row["cost_json"] else None
        if cost and row["state"] == "interrupted":
            cost["status"] = "uncertain"
        return {
            "task_id": row["id"],
            "state": "running" if row["state"] in ACTIVE else row["state"],
            "stage": row["stage"],
            "steps": row["steps"],
            "version": row["revision"],
            "root_id": row["root_id"],
            "base_task_id": row["base_id"],
            "created_at": row["created"],
            "expires_at": row["expires"],
            "output": json.loads(row["output_json"]) if row["output_json"] else None,
            "error": row["error"],
            "service_cost_snapshot": cost,
        }

    @staticmethod
    def summary(row):
        output = json.loads(row["output_json"]) if row["output_json"] else {}
        return {
            "task_id": row["id"],
            "root_id": row["root_id"],
            "version": row["revision"],
            "created_at": row["created"],
            "state": row["state"],
            "city": output.get("city", "旅行草案"),
            "result_status": output.get("result", {}).get("status"),
            "is_demo": bool(output.get("is_demo")),
        }
