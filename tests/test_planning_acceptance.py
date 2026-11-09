"""Frozen synthetic contracts. They are separate from real-world quality evaluation."""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "tests/fixtures/planning_acceptance_v1.json"
CASES = json.loads(PATH.read_text("utf-8"))["cases"]


@pytest.fixture
def evaluator():
    spec = importlib.util.spec_from_file_location(
        "planning_acceptance", ROOT / "scripts/evaluate_planning.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_frozen_case_bytes_and_declared_evaluation_limits():
    manifest = json.loads(PATH.with_suffix(".sha256.json").read_text("utf-8"))
    assert hashlib.sha256(PATH.read_bytes()).hexdigest() == manifest["sha256"]
    assert len(CASES) == 10 and len({case["id"] for case in CASES}) == 10
    assert manifest["independent_human_or_unseen_real_world_evaluation"] is False


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_frozen_planning_contract(evaluator, case):
    result, counts, _, _ = evaluator.run_case(case)
    judged = evaluator.judge(case, result, counts)
    assert judged["passed"], judged


def test_report_rejects_changed_fixture_before_executing_cases(evaluator, monkeypatch, tmp_path):
    path = tmp_path / "cases.json"
    path.write_text("{}", encoding="utf-8")
    path.with_suffix(".sha256.json").write_text(json.dumps({"sha256": "0" * 64}), encoding="utf-8")
    monkeypatch.setattr(evaluator, "FIXTURE", path)
    with pytest.raises(ValueError, match="frozen_acceptance_fixture_changed"):
        evaluator.evaluate(tmp_path / "report.json")
