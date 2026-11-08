import json
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from travel_agent.api import app

client = TestClient(app)


@pytest.mark.smoke
def test_health_and_intake():
    assert client.get("/health").json()["live_providers"] is False
    response = client.post("/v1/intake", json={"destination": "南京附近", "budget": "5000以内"})
    assert response.status_code == 200
    data = response.json()
    assert data["budget"]["maximum_minor"] == 500000
    assert data["destination"]["mode"] == "nearby"
    assert "nearby_boundary" in data["missing_fields"]
    assert data["status"] == "draft"


@pytest.mark.smoke
def test_budget_api():
    response = client.post(
        "/v1/budget/check",
        json={
            "budget": {
                "raw_text": "500以内",
                "kind": "ceiling",
                "maximum_minor": 50000,
                "scope": "total",
                "cap_confirmed": True,
            },
            "items": [
                {"item_id": "meal", "category": "food", "low_minor": 10000, "high_minor": 20000}
            ],
            "required_categories": ["food"],
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "within_estimated_cap"


@pytest.mark.parametrize(
    "payload",
    [
        {"budget": "500美元"},
        {"destination": "附近"},
        {"adults": True},
        {"book": True},
        {"budget": "每人2000", "budget_scope": "total"},
    ],
)
def test_invalid_api_input(payload):
    assert client.post("/v1/intake", json=payload).status_code == 422


def test_read_only_api_has_no_transaction_routes():
    for path in ("/v1/book", "/v1/pay", "/v1/cancel"):
        assert client.post(path, json={}).status_code == 404
    assert client.get("/openapi.json").status_code == 200


@pytest.mark.smoke
def test_installed_cli():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from travel_agent.cli import main; raise SystemExit(main())",
            "--destination",
            "上海周边",
            "--budget",
            "2000~3000",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"},
        check=True,
    )
    assert json.loads(result.stdout)["budget"]["maximum_minor"] == 300000


def test_cli_invalid_budget(monkeypatch, capsys):
    from travel_agent.cli import main

    monkeypatch.setattr(sys, "argv", ["travel-agent", "--destination", "上海", "--budget", "-1"])
    assert main() == 2
    assert "输入无效" in capsys.readouterr().err
