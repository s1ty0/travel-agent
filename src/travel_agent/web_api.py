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
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field, model_validator

from .deepseek import DeepSeekSettings, ProviderError
from .demo import demo_enabled, demo_intake, demo_runner, mark_demo
from .durable_tasks import DurableTasks
from .evidence_store import save_provenance
from .idea import extract_idea
from .models import Contract
from .plan_render import render_markdown
from .planner import BaselineRunner
from .planner_cli import save_run
from .planning_models import DayWindow, PlannerLimits, PlanningInput
from .product_planner import ProductPlanner
from .refine_runner import refine_product
from .refinement import parse_local_edit, structured_edit
from .service_budget import PriceCard, ServiceBudget
from .source_models import SourceBatch
from .sources import amap_key_from_env, fingerprint

router = APIRouter(prefix="/v1/web", tags=["web"])


class WebPlanRequest(Contract):
    request_id: UUID
    planning: PlanningInput | None = None
    idea: Annotated[str, Field(min_length=1, max_length=2000)] | None = None
    base_task_id: UUID | None = None
    max_replans: Annotated[int, Field(strict=True, ge=0, le=1)] = 1
    research_mode: Literal["single_agent", "parallel_tools", "multi_agent"] | None = None

    @model_validator(mode="after")
    def one_input(self):
        if (self.idea is None) == (self.planning is None):
            raise ValueError("provide_idea_or_planning")
        if self.idea is not None and not self.idea.strip():
            raise ValueError("idea_empty")
        return self


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


def run_plan(
    data: WebPlanRequest, *, base_planning=None, base_output=None, cancel=None, progress=None
):
    start = monotonic()
    demo = demo_enabled()
    if demo and live_enabled():
        raise ProviderError("demo_live_mode_conflict")
    if base_output and bool(base_output.get("is_demo")) != demo:
        raise ProviderError("base_execution_mode_mismatch")
    settings = DeepSeekSettings("synthetic-demo-only") if demo else DeepSeekSettings.from_env()
    limits = PlannerLimits(
        allow_rule_draft=True,
        service_cap_minor=int(os.environ.get("SERVICE_BUDGET_MINOR", "500")),
        max_replans=data.max_replans,
    )
    prices = PriceCard(amap_free_quota_only=os.environ.get("AMAP_FREE_QUOTA_ONLY") == "1")
    use_product = demo or os.environ.get("TRAVEL_PLANNER_ENGINE", "source_first") != "baseline"
    runner = (
        demo_runner(data.research_mode or "single_agent")
        if demo
        else ProductPlanner(
            settings,
            amap_key_from_env(),
            prices,
            mode=data.research_mode or os.environ.get("TRAVEL_RESEARCH_MODE", "single_agent"),
        )
        if use_product
        else BaselineRunner(settings, amap_key_from_env(), limits=limits, prices=prices)
    )
    prices = runner.prices
    ledger = ServiceBudget(limits.service_cap_minor)
    planning, assumptions, events = data.planning, [], []
    initial_calls, edit = 0, None
    if (
        use_product
        and data.planning
        and base_planning
        and base_output
        and (
            data.research_mode is None
            or data.research_mode == base_output["result"].get("research_mode")
        )
    ):
        edit = structured_edit(data.planning, base_planning)
    if data.idea is not None:
        prices.validate(settings.model, runner.today)
        try:
            if cancel and cancel():
                raise ProviderError("cancelled")
            if use_product and base_planning and base_output:
                edit = parse_local_edit(data.idea, base_planning, base_output)
            if edit:
                planning, assumptions = edit.planning, [edit.note]
            elif demo:
                planning, assumptions = demo_intake(data.idea, base_planning)
            else:
                initial_calls = 1
                planning, assumptions = extract_idea(
                    data.idea,
                    settings,
                    prices,
                    ledger,
                    **({"base": base_planning} if base_planning else {}),
                    **(
                        {"on_reserve": lambda: progress("idea_extraction", 1, ledger.summary())}
                        if progress
                        else {}
                    ),
                )
            events = [
                {
                    "step": 1,
                    "operation": "local_edit" if edit else "idea_extraction",
                    "status": "ok",
                    "elapsed_ms": int((monotonic() - start) * 1000),
                }
            ]
        except Exception as exc:
            issue = str(exc) if isinstance(exc, ProviderError) else "invalid_model_or_tool_response"
            initial_calls = (
                0
                if issue
                in {
                    "service_budget_exhausted",
                    "cancelled",
                    "unsupported_local_refinement",
                    "ambiguous_place_change",
                    "day_override_outside_trip",
                }
                else initial_calls
            )
            failed = {
                "city": "旅行初稿",
                "planning": None,
                "assumptions": [],
                "audit_saved": False,
                "markdown": "本次想法解析未完成，请补充目的城市后再试。",
                "result": {
                    "run_id": str(uuid4()),
                    "status": "stopped",
                    "issue": issue,
                    "plan": None,
                    "sources": [],
                    "events": [],
                    "model_calls": initial_calls,
                    "source_calls": 0,
                    "replans": 0,
                    "service_cost": ledger.summary(),
                    "elapsed_ms": int((monotonic() - start) * 1000),
                },
            }
            return mark_demo(failed) if demo else failed
    if planning.day_window is None:
        planning = planning.model_copy(update={"day_window": DayWindow()})
        assumptions.append("每日时间框架暂按09:00至20:00，可在条件栏调整。")
    if use_product and planning.constraints.max_walk_minutes is None:
        planning = planning.model_copy(
            update={"constraints": planning.constraints.model_copy(update={"max_walk_minutes": 90})}
        )
        assumptions.append("每日步行上限暂按90分钟，市内交通目前仅有步行估算，可修改。")
    observer = (lambda stage, steps: progress(stage, steps, ledger.summary())) if progress else None
    result = (
        refine_product(
            runner, planning, base_output, edit, ledger, cancel=cancel, progress=observer
        )
        if edit
        else runner.run(
            planning,
            ledger=ledger,
            initial_model_calls=initial_calls,
            initial_events=events,
            started_at=start,
            **(
                {
                    "cancel": cancel,
                    "progress": observer,
                }
                if use_product
                else {}
            ),
        )
    )
    # Existing minimal audit excludes the itinerary, raw request, quotes and prompt.
    audit_path = Path("runtime/web-audit.sqlite3")
    try:
        for batch in result["sources"]:
            if batch["run_id"] not in result.get("reused_source_run_ids", []):
                save_provenance(SourceBatch.model_validate(batch), audit_path)
        save_run(result, audit_path)
        audit_saved = True
    except (OSError, sqlite3.Error):
        audit_saved = False
    output = {
        "city": planning.city,
        "planning": planning.model_dump(mode="json"),
        "assumptions": assumptions,
        "result": result,
        "markdown": render_markdown(result),
        "audit_saved": audit_saved,
    }
    return mark_demo(output) if demo else output


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
            base_planning = None
            if data.base_task_id is not None:
                base_task = self.tasks.get(str(data.base_task_id))
                if base_task is None or base_task.owner != owner:
                    raise HTTPException(404, "task_not_found")
                raw_base = (base_task.output or {}).get("planning")
                if raw_base is None:
                    raise HTTPException(409, "base_conditions_unavailable")
                base_planning = PlanningInput.model_validate(raw_base)
            if any(task.state == "running" for task in self.tasks.values()):
                raise HTTPException(409, "planner_busy")
            if len(self.tasks) >= 32:
                raise HTTPException(429, "web_task_capacity")
            task_id = str(uuid4())
            task = Task(owner, data.request_id, signature, now)
            self.tasks[task_id] = task
        try:
            self.pool.submit(self.execute, task_id, data, base_planning)
        except RuntimeError:
            with self.lock:
                del self.tasks[task_id]
            raise HTTPException(503, "planning_unavailable") from None
        return self.get(owner, task_id)

    def execute(self, task_id, data, base_planning=None):
        try:
            output = (
                self.runner(data, base_planning=base_planning)
                if base_planning
                else self.runner(data)
            )
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


tasks = DurableTasks(run_plan, embedded=os.environ.get("TRAVEL_TASK_WORKER_EXTERNAL") != "1")


@router.get("/config")
def web_config(owner: Annotated[str, Depends(web_owner)]):
    return {
        "live_enabled": live_enabled(),
        "demo_mode": demo_enabled(),
        "planning_enabled": (live_enabled() or demo_enabled())
        and not (live_enabled() and demo_enabled()),
        "service_cap_minor": 500,
        "production_ready": False,
        "storage": "private_session_7_days",
        "engine": os.environ.get("TRAVEL_PLANNER_ENGINE", "source_first"),
    }


@router.post("/tasks", status_code=202)
def create_task(data: WebPlanRequest, owner: Annotated[str, Depends(web_owner)]):
    if live_enabled() and demo_enabled():
        raise HTTPException(503, "demo_live_mode_conflict")
    if not (live_enabled() or demo_enabled()):
        raise HTTPException(503, "live_planning_disabled")
    if data.planning and (data.planning.page_urls or data.planning.references):
        raise HTTPException(422, "web_reference_import_not_enabled")
    return tasks.create(owner, data)


@router.get("/tasks/{task_id}")
def get_task(task_id: UUID, owner: Annotated[str, Depends(web_owner)]):
    return tasks.get(owner, str(task_id))


@router.get("/tasks")
def task_history(owner: Annotated[str, Depends(web_owner)]):
    return {"tasks": tasks.history(owner)}


@router.get("/tasks/{task_id}/versions")
def task_versions(task_id: UUID, owner: Annotated[str, Depends(web_owner)]):
    return {"tasks": tasks.history(owner, str(task_id))}


@router.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: UUID, owner: Annotated[str, Depends(web_owner)]):
    return tasks.cancel(owner, str(task_id))


@router.delete("/tasks/{task_id}")
def delete_task(task_id: UUID, owner: Annotated[str, Depends(web_owner)]):
    return tasks.delete(owner, str(task_id))
