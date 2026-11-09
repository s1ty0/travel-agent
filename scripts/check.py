"""Run all available offline gates. This report is not production release approval."""

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

ROOT = Path(__file__).resolve().parents[1]


def format_command(root: Path, python: str):
    # Explicit paths also cover new Markdown before Git tracking or staging.
    # Only public documentation roots are included, not private runtime/artifact folders.
    documents = sorted(
        path.relative_to(root).as_posix()
        for path in (*root.glob("*.md"), *(root / "docs").rglob("*.md"))
        if path.is_file()
    )
    return [python, "-m", "ruff", "format", "--check", ".", *documents]


def main():
    output = ROOT / "artifacts" / "qa"
    output.mkdir(parents=True, exist_ok=True)
    python = sys.executable
    gates = [
        ("lint", [python, "-m", "ruff", "check", "."]),
        ("format", format_command(ROOT, python)),
        (
            "dependency_consistency",
            ["uv", "--cache-dir", str(ROOT / ".cache" / "uv"), "pip", "check", "--python", python],
        ),
        ("smoke", [python, "-m", "pytest", "-q", "-m", "smoke"]),
        ("regression", [python, "-m", "pytest", "-q", "-m", "regression"]),
        ("planning_acceptance", [python, str(ROOT / "scripts" / "evaluate_planning.py")]),
        (
            "full_offline",
            [
                python,
                "-m",
                "pytest",
                "--cov=travel_agent",
                "--cov-branch",
                "--cov-report=term-missing",
                "--cov-fail-under=85",
                f"--junitxml={output / 'junit.xml'}",
            ],
        ),
    ]
    report = {
        "stage": "M2_single_agent_baseline",
        "production_release_ready": False,
        "started_at": datetime.now(UTC).isoformat(),
        "gates": [],
    }
    for name, command in gates:
        print(f"\nRunning {name}", flush=True)
        start = monotonic()
        result = subprocess.run(command, cwd=ROOT, check=False)
        report["gates"].append(
            {
                "name": name,
                "passed": result.returncode == 0,
                "seconds": round(monotonic() - start, 3),
            }
        )
        if result.returncode:
            break
    report["passed"] = len(report["gates"]) == len(gates) and all(
        gate["passed"] for gate in report["gates"]
    )
    (output / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
