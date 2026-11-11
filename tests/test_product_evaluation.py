import importlib.util
from pathlib import Path

import pytest

from travel_agent.deepseek import DeepSeekSettings
from travel_agent.planning_models import PlanningInput
from travel_agent.product_planner import ProductPlanner
from travel_agent.service_budget import PriceCard, ServiceBudget
from travel_agent.synthetic import SyntheticProviders

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "evaluate_product", ROOT / "scripts/evaluate_product.py"
)
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)
DEVELOPMENT, DIGEST = evaluation.load_cases("development")


@pytest.mark.parametrize("case", DEVELOPMENT, ids=lambda case: case["id"])
def test_frozen_product_development_contracts(case):
    synthetic = SyntheticProviders(flags=case.get("source_flags"))
    runner = ProductPlanner(
        DeepSeekSettings("synthetic"),
        "synthetic",
        PriceCard(amap_free_quota_only=True),
        today=evaluation.date(2026, 11, 11),
        model_transport=synthetic.model_transport,
        source_transport=synthetic.source_transport,
    )
    result = runner.run(PlanningInput.model_validate(case["planning"]), ledger=ServiceBudget())
    assert evaluation.score(case, result)["passed"]


def test_frozen_fixture_hash_rejects_modified_cases(tmp_path, monkeypatch):
    path = tmp_path / "cases.json"
    path.write_text("{}", encoding="utf-8")
    path.with_suffix(".sha256").write_text(DIGEST, encoding="ascii")
    monkeypatch.setattr(evaluation, "FIXTURE", path)
    with pytest.raises(ValueError, match="frozen_product_fixture_changed"):
        evaluation.load_cases("development")


def test_metric_cannot_count_a_known_conflict_as_a_successful_draft():
    case = DEVELOPMENT[0]
    synthetic = SyntheticProviders()
    runner = ProductPlanner(
        DeepSeekSettings("synthetic"),
        "synthetic",
        PriceCard(amap_free_quota_only=True),
        today=evaluation.date(2026, 11, 11),
        model_transport=synthetic.model_transport,
        source_transport=synthetic.source_transport,
    )
    result = runner.run(PlanningInput.model_validate(case["planning"]), ledger=ServiceBudget())
    result["plan"]["checks"].append({"rule": "time_overlap", "status": "fail"})
    assert not evaluation.score(case, result)["passed"]
