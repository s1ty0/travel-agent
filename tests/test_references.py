import io
import json
from datetime import date, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from travel_agent.references import (
    QuoteRequest,
    ReferenceDocument,
    ReferenceInput,
    Snapshot,
    check_quote,
    delete_snapshot,
    digest,
    import_reference,
    load_snapshot,
    save_snapshot,
    snapshot_path,
)

CASES = json.loads(
    (Path(__file__).parent / "fixtures/reference_replay_cases.json").read_text("utf-8")
)


def material(**changes):
    return ReferenceInput(
        title="合成测试资料，非真实旅游事实", text="参观须预约，周一闭馆。", **changes
    )


@pytest.mark.regression
@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_fixed_evidence_replay(case, tmp_path):
    reference = ReferenceInput(
        title="合成样例 " + case["id"],
        text=case["text"],
        url=case.get("url"),
        text_kind=case.get("kind", "user_text"),
        allow_snapshot=True,
        valid_from=case.get("valid_from"),
        valid_until=case.get("valid_until"),
    )
    doc = import_reference(reference)
    request = QuoteRequest(
        source_id=doc.source_id, quote=case["quote"], at_date=case.get("at_date")
    )
    original = check_quote((doc,), request)
    snapshot_id = save_snapshot((doc,), tmp_path)
    replay = check_quote(load_snapshot(snapshot_id, tmp_path), request)
    assert original == replay
    assert replay["status"] == case["expected"]
    assert replay["facts_verified"] is False
    assert replay["date_applicability"] == case.get("date_status", "unknown")
    if "line" in case:
        assert replay["span"]["line"] == case["line"]
    if "start" in case:
        assert replay["span"]["start"] == case["start"]
    if replay["span"]:
        span = replay["span"]
        assert doc.material.text[span["start"] : span["end"]] == request.quote


@pytest.mark.smoke
def test_offline_import_quote_snapshot_and_deletion(tmp_path):
    doc = import_reference(material(allow_snapshot=True))
    assert doc.material.allow_model_use is False
    assert "text" not in doc.summary()
    request = QuoteRequest(source_id=doc.source_id, quote="周一闭馆")
    assert check_quote((doc,), request)["status"] == "matched"
    snapshot_id = save_snapshot((doc,), tmp_path)
    restored = load_snapshot(snapshot_id, tmp_path)
    assert restored == (doc,)
    assert doc.material.text == restored[0].material.text
    delete_snapshot(snapshot_id, tmp_path)
    with pytest.raises(FileNotFoundError):
        load_snapshot(snapshot_id, tmp_path)


def test_snapshot_requires_separate_permission_and_preserves_identity(tmp_path):
    doc = import_reference(material(allow_model_use=True))
    with pytest.raises(ValidationError, match="snapshot_permission_required"):
        save_snapshot((doc,), tmp_path)
    doc = import_reference(material(allow_snapshot=True))
    snapshot_id = save_snapshot((doc,), tmp_path)
    with pytest.raises(FileExistsError):
        save_snapshot((doc,), tmp_path)
    assert load_snapshot(snapshot_id, tmp_path) == (doc,)
    with pytest.raises(ValidationError, match="duplicate_reference"):
        Snapshot(documents=(doc, doc))


def test_import_revisions_have_distinct_ids_but_content_hash_is_stable():
    first = import_reference(material())
    second = import_reference(material())
    changed = import_reference(ReferenceInput(title=first.material.title, text="周一开放。"))
    assert first.source_id == second.source_id
    assert first.content_sha256 == second.content_sha256
    assert first.source_id != changed.source_id
    assert first.content_sha256 != changed.content_sha256
    assert first.content_sha256 == digest(first.material.text)


def test_hash_and_timestamp_tampering_rejected():
    doc = import_reference(material())
    for changes in (
        {"source_id": "forged"},
        {"content_sha256": "0" * 64},
        {"imported_at": datetime(2026, 11, 8)},
    ):
        with pytest.raises(ValidationError):
            ReferenceDocument.model_validate({**doc.model_dump(), **changes})


def test_snapshot_byte_tampering_size_limit_and_unknown_schema(tmp_path):
    doc = import_reference(material(allow_snapshot=True))
    sid = save_snapshot((doc,), tmp_path)
    path = snapshot_path(tmp_path, sid)
    path.write_text(path.read_text("utf-8") + " ", encoding="utf-8")
    with pytest.raises(ValueError, match="snapshot_integrity_failed"):
        load_snapshot(sid, tmp_path)
    path.write_bytes(b"x" * (256 * 1024 + 1))
    with pytest.raises(ValueError, match="snapshot_too_large"):
        load_snapshot(sid, tmp_path)
    body = (
        Snapshot(documents=(doc,))
        .model_dump_json()
        .replace('"schema_version":1', '"schema_version":9')
    )
    bad = digest(body)
    snapshot_path(tmp_path, bad).write_text(body, encoding="utf-8")
    with pytest.raises(ValidationError):
        load_snapshot(bad, tmp_path)


@pytest.mark.parametrize("sid", ["../private", "a" * 63, "x" * 64, "../" + "a" * 64, "C:/key", ""])
def test_snapshot_identifiers_cannot_traverse_paths(tmp_path, sid):
    with pytest.raises(ValueError, match="invalid_snapshot_id"):
        load_snapshot(sid, tmp_path)
    with pytest.raises(ValueError):
        delete_snapshot(sid, tmp_path)


def test_ambiguous_quotes_need_explicit_exact_offset():
    doc = import_reference(ReferenceInput(title="合成重复", text="预约。预约。"))
    assert (
        check_quote((doc,), QuoteRequest(source_id=doc.source_id, quote="预约"))["status"]
        == "ambiguous"
    )
    report = check_quote(
        (doc,), QuoteRequest(source_id=doc.source_id, quote="预约", start_offset=3)
    )
    assert report["status"] == "matched" and report["span"]["start"] == 3
    assert (
        check_quote((doc,), QuoteRequest(source_id=doc.source_id, quote="预约", start_offset=2))[
            "status"
        ]
        == "missing"
    )
    assert (
        check_quote((doc,), QuoteRequest(source_id="unknown", quote="预约"))["status"]
        == "unknown_source"
    )


def test_declared_date_range_is_not_independent_truth():
    doc = import_reference(material(valid_from=date(2026, 1, 1), valid_until=date(2026, 12, 31)))
    report = check_quote(
        (doc,), QuoteRequest(source_id=doc.source_id, quote="周一闭馆", at_date=date(2026, 11, 8))
    )
    assert report["date_applicability"] == "within_declared_range"
    assert report["facts_verified"] is False


@pytest.mark.parametrize(
    "payload",
    [
        {"title": " ", "text": "x"},
        {"title": "x"},
        {"title": "x", "text": " "},
        {"title": "x", "text": "x" * 8001},
        {"title": "x", "url": "http://127.0.0.1/"},
        {"title": "x", "url": "https://example.com/#fragment"},
        {"title": "x", "text": "x", "text_kind": "official_verified_page"},
        {"title": "x", "text": "x", "allow_model_use": "true"},
        {"title": "x", "text": "x", "allow_snapshot": 1},
        {"title": "x", "text": "x", "valid_from": "2027-01-01", "valid_until": "2026-01-01"},
    ],
)
def test_invalid_or_misleading_imports_rejected(payload):
    with pytest.raises(ValidationError):
        ReferenceInput.model_validate(payload)


def test_evidence_cli_errors_are_safe(monkeypatch, capsys):
    from travel_agent.evidence_cli import main

    for argv, text in (
        (["evidence", "inspect"], "private-invalid-json"),
        (["evidence", "inspect"], "x" * 64001),
        (["evidence", "replay"], "{}"),
        (["evidence", "delete", "--snapshot", "../secret"], ""),
        (["evidence", "inspect", "--snapshot", "a" * 64], "{}"),
    ):
        monkeypatch.setattr("sys.argv", argv)
        monkeypatch.setattr("sys.stdin", io.StringIO(text))
        assert main() == 2
        captured = capsys.readouterr()
        assert "invalid_or_unavailable_reference" in captured.err
        assert "private-invalid-json" not in captured.err


def test_blank_quotes_and_duplicate_imports_are_rejected(monkeypatch, capsys):
    from travel_agent.evidence_cli import main
    from travel_agent.planning_models import PlanningInput

    raw = material().model_dump(mode="json")
    with pytest.raises(ValidationError):
        QuoteRequest(source_id="known", quote="  ")
    with pytest.raises(ValidationError):
        PlanningInput(city="南京", trip={"destination": "南京"}, references=(raw, raw))
    monkeypatch.setattr("sys.argv", ["evidence", "inspect"])
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"references": [raw, raw]})))
    assert main() == 2
    assert "invalid_or_unavailable_reference" in capsys.readouterr().err


def test_local_reference_api_is_stateless_and_offline(monkeypatch, tmp_path):
    import httpx
    from fastapi.testclient import TestClient

    from travel_agent.api import app

    def forbidden(*args, **kwargs):
        pytest.fail("inspection must not use external providers")

    monkeypatch.setattr(httpx, "Client", forbidden)
    monkeypatch.chdir(tmp_path)
    doc = import_reference(material(allow_snapshot=True, allow_model_use=True))
    payload = {
        "references": [doc.material.model_dump(mode="json")],
        "quotes": [{"source_id": doc.source_id, "quote": "周一闭馆"}],
    }
    client = TestClient(app)
    response = client.post("/v1/references/inspect", json=payload)
    assert response.status_code == 200
    assert response.json()["quotes"][0]["status"] == "matched"
    assert response.json()["network_calls"] == 0
    assert not (tmp_path / "runtime").exists()
    assert "text" not in response.json()["references"][0]
    duplicate = {"references": [doc.material.model_dump(mode="json")] * 2}
    assert client.post("/v1/references/inspect", json=duplicate).status_code == 422
    assert client.post("/v1/references/inspect", json={"references": []}).status_code == 422


def test_evidence_cli_save_replay_delete_without_network(monkeypatch, capsys, tmp_path):
    import httpx

    from travel_agent.evidence_cli import main

    def forbidden(*args, **kwargs):
        pytest.fail("offline evidence workflow must not create network clients")

    monkeypatch.setattr(httpx, "Client", forbidden)
    monkeypatch.chdir(tmp_path)
    input_doc = material(allow_snapshot=True)
    document = import_reference(input_doc)
    quote = QuoteRequest(source_id=document.source_id, quote="周一闭馆")
    payload = json.dumps(
        {
            "references": [input_doc.model_dump(mode="json")],
            "quotes": [quote.model_dump(mode="json")],
        }
    )
    monkeypatch.setattr("sys.argv", ["evidence", "inspect"])
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    assert main() == 0
    inspected = json.loads(capsys.readouterr().out)
    assert inspected["quotes"][0]["status"] == "matched"
    assert not (tmp_path / "runtime").exists()
    monkeypatch.setattr("sys.argv", ["evidence", "save"])
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    assert main() == 0
    sid = json.loads(capsys.readouterr().out)["snapshot_id"]
    monkeypatch.setattr("sys.argv", ["evidence", "replay", "--snapshot", sid])
    monkeypatch.setattr(
        "sys.stdin", io.StringIO(json.dumps({"quotes": [quote.model_dump(mode="json")]}))
    )
    assert main() == 0
    replay = json.loads(capsys.readouterr().out)
    assert replay["quotes"] == inspected["quotes"]
    assert replay["mode"] == "offline_replay"
    monkeypatch.setattr("sys.argv", ["evidence", "delete", "--snapshot", sid])
    assert main() == 0
    assert json.loads(capsys.readouterr().out)["status"] == "deleted"
    monkeypatch.setattr("sys.argv", ["evidence", "replay", "--snapshot", sid])
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert main() == 2
    assert "unavailable" in capsys.readouterr().err
