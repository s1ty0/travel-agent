"""Reproduce the previously missed, untracked Markdown Python-example format error."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def check_script():
    path = Path(__file__).resolve().parents[1] / "scripts/check.py"
    spec = importlib.util.spec_from_file_location("travel_quality_check", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def workspace(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)
    (tmp_path / ".gitignore").write_text("runtime/\nartifacts/\n", encoding="utf-8")
    return tmp_path


@pytest.mark.regression
@pytest.mark.parametrize("relative", ["NEW.md", "docs/new guide.md"])
def test_untracked_python_example_format_error_is_detected_before_staging(
    check_script, tmp_path, relative
):
    root = workspace(tmp_path)
    document = root / relative
    document.parent.mkdir(parents=True, exist_ok=True)
    document.write_text("```python\nresult=dict(a=1,b=2)\n```\n", encoding="utf-8")
    assert not subprocess.check_output(["git", "ls-files"], cwd=root)
    result = subprocess.run(
        check_script.format_command(root, sys.executable),
        cwd=root,
        capture_output=True,
    )
    assert result.returncode == 1
    assert b"would be reformatted" in result.stdout + result.stderr


def test_public_examples_pass_without_reading_ignored_private_markdown(check_script, tmp_path):
    root = workspace(tmp_path)
    (root / "README.md").write_text("```python\nresult = dict(a=1, b=2)\n```\n", encoding="utf-8")
    private = root / "runtime/private.md"
    private.parent.mkdir()
    private.write_text("```python\nresult=dict(a=1,b=2)\n```\n", encoding="utf-8")
    result = subprocess.run(
        check_script.format_command(root, sys.executable),
        cwd=root,
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "runtime/private.md" not in check_script.format_command(root, sys.executable)


@pytest.mark.regression
def test_quality_gate_records_failure_and_stops_before_later_checks(
    check_script, tmp_path, monkeypatch
):
    root = workspace(tmp_path)
    (root / "docs").mkdir()
    (root / "docs/NEW.md").write_text("```python\nresult=dict(a=1,b=2)\n```\n", encoding="utf-8")
    run = subprocess.run

    def bounded_run(command, **kwargs):
        assert "ruff" in command, "checks after the format failure must not run"
        return run(command, **kwargs, capture_output=True)

    monkeypatch.setattr(check_script, "ROOT", root)
    monkeypatch.setattr(check_script.subprocess, "run", bounded_run)
    assert check_script.main() == 1
    report = json.loads((root / "artifacts/qa/summary.json").read_text("utf-8"))
    assert report["passed"] is False and report["production_release_ready"] is False
    assert [gate["name"] for gate in report["gates"]] == ["lint", "format"]
    assert report["gates"][-1]["passed"] is False
