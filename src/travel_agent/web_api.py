"""Local web integration boundary. Public hosting requires durable tasks and real identity."""

import os
import re
import secrets
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from time import monotonic
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field

from .deepseek import DeepSeekSettings
from .evidence_store import save_provenance
from .models import Contract
from .plan_render import render_markdown
from .planner import BaselineRunner
from .planner_cli import save_run
from .planning_models import PlannerLimits, PlanningInput
from .service_budget import PriceCard
from .source_models import SourceBatch
from .sources import amap_key_from_env, fingerprint

router = APIRouter(prefix="/v1/web", tags=["web"])


class WebPlanRequest(Contract):
    request_id: UUID
    planning: PlanningInput
    max_replans: Annotated[int, Field(strict=True, ge=0, le=1)] = 0


def web_owner(request: Request):
    token = os.environ.get("TRAVEL_WEB_TOKEN", "")
    if len(token) < 32:
        raise HTTPException(503, "web_not_configured")
    if not secrets.compare_digest(request.headers.get("x-travel-bridge", ""), token):
        raise HTTPException(403, "web_bridge_required")
    owner = request.headers.get("x-travel-session", "")
    if not re.fullmatch(r"[a-f0-9]{64}", owner):
        raise HTTPException(403, "web_session_required")
    return owner


def live_enabled():
    return os.environ.get("TRAVEL_WEB_LIVE") == "1"


def run_plan(data: WebPlanRequest):
    result = BaselineRunner(
        DeepSeekSettings.from_env(),
        amap_key_from_env(),
        limits=PlannerLimits(
            service_cap_minor=min(int(os.environ.get("SERVICE_BUDGET_MINOR", "200")), 200),
            max_replans=data.max_replans,
        ),
        prices=PriceCard(amap_free_quota_only=os.environ.get("AMAP_FREE_QUOTA_ONLY") == "1"),
    ).run(data.planning)
    # Existing minimal audit excludes the itinerary, raw request, quotes and prompt.
    audit_path = Path("runtime/web-audit.sqlite3")
    try:
        for batch in result["sources"]:
            save_provenance(SourceBatch.model_validate(batch), audit_path)
        save_run(result, audit_path)
        audit_saved = True
    except (OSError, sqlite3.Error):
        audit_saved = False
    return {
        "city": data.planning.city,
        "result": result,
        "markdown": render_markdown(result),
        "audit_saved": audit_saved,
    }


@dataclass
class Task:
    owner: str
    request_id: UUID
    input_hash: str
    created: float
    state: str = "running"
    output: dict | None = None
    error: str | None = None


class WebTasks:
    """One active task; bounded, private, ephemeral results. Not a production queue."""

    def __init__(self, runner=run_plan):
        self.lock = Lock()
        self.tasks: dict[str, Task] = {}
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="travel-web")
        self.runner = runner

    def create(self, owner, data):
        signature = fingerprint(data.model_dump_json())
        with self.lock:
            now = monotonic()
            self.tasks = {
                key: task
                for key, task in self.tasks.items()
                if task.state == "running" or now - task.created < 1800
            }
            for key, task in self.tasks.items():
                if task.owner == owner and task.request_id == data.request_id:
                    if task.input_hash != signature:
                        raise HTTPException(409, "request_id_conflict")
                    return self.view(key, task)
            if any(task.state == "running" for task in self.tasks.values()):
                raise HTTPException(409, "planner_busy")
            if len(self.tasks) >= 32:
                raise HTTPException(429, "web_task_capacity")
            task_id = str(uuid4())
            task = Task(owner, data.request_id, signature, now)
            self.tasks[task_id] = task
        try:
            self.pool.submit(self.execute, task_id, data)
        except RuntimeError:
            with self.lock:
                del self.tasks[task_id]
            raise HTTPException(503, "planning_unavailable") from None
        return self.get(owner, task_id)

    def execute(self, task_id, data):
        try:
            output = self.runner(data)
            with self.lock:
                self.tasks[task_id].output = output
                self.tasks[task_id].state = "completed"
        except Exception:
            # Do not echo supplier bodies, user input, credentials or exception text.
            with self.lock:
                self.tasks[task_id].state = "failed"
                self.tasks[task_id].error = "planning_unavailable"

    @staticmethod
    def view(task_id, task):
        return {"task_id": task_id, "state": task.state, "output": task.output, "error": task.error}

    def get(self, owner, task_id):
        with self.lock:
            task = self.tasks.get(task_id)
            if task is None or task.owner != owner or monotonic() - task.created >= 1800:
                raise HTTPException(404, "task_not_found")
            return self.view(task_id, task)


tasks = WebTasks()


@router.get("/config")
def web_config(owner: Annotated[str, Depends(web_owner)]):
    return {
        "live_enabled": live_enabled(),
        "service_cap_minor": 200,
        "production_ready": False,
        "storage": "ephemeral_30_minutes",
    }


@router.post("/tasks", status_code=202)
def create_task(data: WebPlanRequest, owner: Annotated[str, Depends(web_owner)]):
    if not live_enabled():
        raise HTTPException(503, "live_planning_disabled")
    if data.planning.page_urls or data.planning.references:
        raise HTTPException(422, "web_reference_import_not_enabled")
    return tasks.create(owner, data)


@router.get("/tasks/{task_id}")
def get_task(task_id: UUID, owner: Annotated[str, Depends(web_owner)]):
    return tasks.get(owner, str(task_id))
