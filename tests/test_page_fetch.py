import io
import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import date

import httpx
import pytest
from pydantic import ValidationError

from travel_agent.deepseek import DeepSeekSettings, ProviderError
from travel_agent.page_cli import save_page_audit
from travel_agent.page_fetch import READ_POLICIES, PageReadInput, read_page
from travel_agent.references import digest
from travel_agent.service_budget import PriceCard, ServiceBudget

URL = READ_POLICIES[0].url
DAY = date(2026, 11, 8)
KEY = "test-page-credential-not-real"
SETTINGS = DeepSeekSettings(KEY)
CONTENT = "合成测试，非现实事实。\n🧳南京：周一闭馆，假日另行公告。"


def read(body=None, *, status=200, **kwargs):
    return read_page(
        PageReadInput(url=URL, quotes=[{"quote": "周一闭馆"}]),
        SETTINGS,
        today=DAY,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                status,
                content=json.dumps(
                    body if body is not None else {"title": "合成资料", "content": CONTENT}
                ).encode(),
            )
        ),
        **kwargs,
    )


@pytest.mark.smoke
def test_fetch_fixed_endpoint_no_target_credentials_or_implicit_storage(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    calls = []

    def handle(request):
        calls.append(request)
        assert str(request.url) == "https://api.deepseek.com/v1/tools/fetch"
        assert request.headers["authorization"] == "Bearer " + KEY
        assert json.loads(request.content) == {"url": URL}
        return httpx.Response(200, json={"title": "合成资料", "content": CONTENT})

    outcome = read_page(
        PageReadInput(url=URL, quotes=[{"quote": "周一闭馆"}]),
        SETTINGS,
        today=DAY,
        transport=httpx.MockTransport(handle),
    )
    report = outcome.report()
    assert len(calls) == report["call_count"] == 1
    assert outcome.status == "acquired"
    assert report["page"]["content_sha256"] == digest(CONTENT)
    assert report["quote_checks"][0]["status"] == "matched"
    assert report["page"]["final_url"] is None
    assert report["page"]["content_origin_verified"] is False
    assert report["page"]["facts_verified"] is False
    assert report["read_policy"]["identity_scope"] == "reviewed_requested_url_only"
    assert report["service_cost"]["estimated_upper"] == 5000
    assert report["service_cost"]["actual_billed"] is None
    assert "content" not in report["page"]
    assert "title" not in report["page"]
    assert outcome.report(include_content=True)["page"]["content"] == CONTENT
    assert not (tmp_path / "runtime").exists()
    assert KEY not in json.dumps(report)
    assert CONTENT not in repr(outcome)


@pytest.mark.parametrize(
    "url",
    [
        "http://www.njmuseum.org.cn/zh/visitIndex",
        URL + "?key=private",
        URL + "#fragment",
        URL + "/../admin",
        URL.replace("www.njmuseum.org.cn", "www.njmuseum.org.cn.evil.example"),
        URL.replace("www.njmuseum.org.cn", "private@www.njmuseum.org.cn"),
        URL.replace("www.njmuseum.org.cn", "www.njmuseum.org.cn:443"),
        URL.replace("/zh/visitIndex", "/files/private"),
        "http://169.254.169.254/latest/meta-data/",
        "https://127.0.0.1/",
        "https://2130706433/",
        "file:///etc/passwd",
        "https://www.njmuseum.com/zh/visitIndex",
    ],
)
def test_only_exact_reviewed_urls_are_accepted(url):
    with pytest.raises(ValidationError, match="page_url_not_reviewed"):
        PageReadInput(url=url)


def test_both_reviewed_urls_are_explicit_and_time_limited():
    for policy in READ_POLICIES:
        assert PageReadInput(url=policy.url).url == policy.url
        assert policy.reviewed_on <= DAY <= policy.valid_until
    with pytest.raises(ValidationError):
        PageReadInput(url=URL, quotes=[{"quote": "  "}])
    with pytest.raises(ValidationError):
        PageReadInput(url=URL, quotes=[{"quote": "x"}])
    with pytest.raises(ValidationError):
        PageReadInput(url=URL, quotes=[{"quote": "xx", "start_offset": True}])


@pytest.mark.parametrize(
    ("code", "issue"),
    [
        (400, "fetch_input_rejected"),
        (401, "authentication_failed"),
        (402, "insufficient_balance"),
        (403, "permission_denied"),
        (404, "page_content_unavailable"),
        (408, "upstream_timeout"),
        (413, "fetch_input_rejected"),
        (429, "rate_limited"),
        (500, "upstream_error"),
        (504, "upstream_timeout"),
        (302, "upstream_error"),
    ],
)
def test_provider_failures_stop_without_retry_or_body_leak(code, issue):
    ledger = ServiceBudget()
    outcome = read({"error": {"message": "private-error-" + KEY}}, status=code, ledger=ledger)
    report = outcome.report()
    assert report["status"] == "unavailable"
    assert report["issue"] == issue
    assert report["call_count"] == 1
    assert report["page"] is None
    assert report["service_cost"]["status"] == "uncertain"
    assert report["service_cost"]["reserved_unsettled"] == 5000
    assert "private-error" not in json.dumps(report)
    assert KEY not in json.dumps(report)
    with pytest.raises(ProviderError, match="service_cost_unknown"):
        ledger.reserve("later", 1)


@pytest.mark.parametrize(
    ("body", "issue"),
    [
        ({"title": "x", "content": " "}, "page_content_unavailable"),
        ({"title": "x", "content": ""}, "page_content_unavailable"),
        ({"title": "x"}, "invalid_fetch_response"),
        ({"title": [], "content": "xx"}, "invalid_fetch_response"),
        ({"title": "x", "content": ["xx"]}, "invalid_fetch_response"),
        ({"title": "x", "content": "x" * 80001}, "page_content_too_large"),
        ({"title": "x" * 301, "content": "xx"}, "page_content_too_large"),
        ({"title": "x", "content": "\ud800"}, "invalid_fetch_response"),
    ],
)
def test_no_truncation_or_upgrade_of_unusable_response(body, issue):
    outcome = read(body)
    assert outcome.issue == issue
    assert outcome.status == "unavailable"
    assert outcome.page is None
    assert outcome.service_cost["status"] == "uncertain"


@pytest.mark.parametrize("failure", ["timeout", "connection", "too_large", "malformed"])
def test_transport_failure_and_response_limits(failure):
    calls = []

    def handle(request):
        calls.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("private-network-error", request=request)
        if failure == "connection":
            raise httpx.ConnectError("private-network-error", request=request)
        raw = b"x" * (512 * 1024 + 1) if failure == "too_large" else b"private-invalid-json"
        return httpx.Response(200, content=raw)

    outcome = read_page(
        PageReadInput(url=URL), SETTINGS, today=DAY, transport=httpx.MockTransport(handle)
    )
    assert len(calls) == 1
    assert outcome.status == "unavailable"
    assert "private" not in json.dumps(outcome.report())
    assert outcome.service_cost["reserved_unsettled"] == 5000


def test_preflight_cost_price_and_policy_failures_make_no_request():
    def forbidden(request):
        pytest.fail("preflight failure must not make a request")

    full = ServiceBudget()
    full.reserve("other-work", 2000000)
    cases = (
        ({"ledger": full}, "service_budget_exhausted"),
        ({"prices": replace(PriceCard(), valid_until=date(2026, 11, 7))}, "price_card_expired"),
        ({"prices": replace(PriceCard(), fetch_micro_cny_per_call=-1)}, "invalid_price_card"),
        ({"today": date(2026, 11, 16)}, "page_read_policy_expired"),
    )
    for options, issue in cases:
        outcome = read_page(
            PageReadInput(url=URL),
            SETTINGS,
            transport=httpx.MockTransport(forbidden),
            **{"today": DAY, **options},
        )
        assert outcome.status == "stopped" and outcome.issue == issue
        assert outcome.call_count == 0
    # Chat-model selection has no bearing on Fetch's separate per-request rate.
    assert read(prices=replace(PriceCard(), model="unselected-model")).status == "acquired"


def test_shared_ledger_limits_multiple_reads_before_another_network_request():
    ledger = ServiceBudget(cap_minor=1)
    assert read(ledger=ledger).status == "acquired"
    assert read(ledger=ledger).status == "acquired"
    third = read(ledger=ledger)
    assert third.status == "stopped" and third.issue == "service_budget_exhausted"
    assert third.call_count == 0
    assert third.service_cost["estimated_upper"] == 10000
    assert third.service_cost["reserved_unsettled"] == 0


@pytest.mark.regression
def test_negation_context_and_repetition_never_become_fact_verification():
    text = "合成说明：不是周一开放；假期例外。周一开放不是当前承诺。"
    query = PageReadInput(url=URL, quotes=[{"quote": "周一开放"}, {"quote": "假期例外"}])
    result = read_page(
        query,
        SETTINGS,
        today=DAY,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"title": "合成说明", "content": text})
        ),
    )
    assert result.quotes[0]["status"] == "ambiguous"
    assert result.quotes[1]["status"] == "matched"
    assert all(check["semantic_support"] == "not_assessed" for check in result.quotes)
    assert all(check["date_applicability"] == "unknown" for check in result.quotes)
    assert result.report()["facts_verified"] is False


def test_quote_offsets_and_version_hashes_follow_exact_returned_markdown():
    content = "🧳南京：预约。预约。"
    query = PageReadInput(
        url=URL,
        quotes=[
            {"quote": "预约", "start_offset": 4},
            {"quote": "预约", "start_offset": 5},
            {"quote": "实际票价"},
        ],
    )
    result = read_page(
        query,
        SETTINGS,
        today=DAY,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"title": "合成说明", "content": content})
        ),
    )
    assert [check["status"] for check in result.quotes] == ["matched", "missing", "missing"]
    assert result.quotes[0]["span"]["start"] == 4
    same = read({"title": "合成说明", "content": content})
    changed = read({"title": "合成说明", "content": content + "变更"})
    assert result.page.metadata()["source_id"] == same.page.metadata()["source_id"]
    assert result.page.metadata()["source_id"] != changed.page.metadata()["source_id"]


def test_minimal_audit_omits_text_quotes_titles_and_does_not_overwrite(tmp_path):
    outcome = read({"title": "private-title", "content": CONTENT + "pay_now upgrade_account"})
    path = tmp_path / "audit.sqlite3"
    save_page_audit(outcome, path)
    with closing(sqlite3.connect(path)) as db:
        run_id, raw = db.execute("SELECT run_id, metadata_json FROM page_reads").fetchone()
    assert run_id == outcome.run_id
    assert json.loads(raw)["page"]["content_sha256"] == outcome.page.metadata()["content_sha256"]
    assert all(value not in raw for value in (KEY, CONTENT, "private-title", "周一闭馆", "pay_now"))
    with pytest.raises(sqlite3.IntegrityError):
        save_page_audit(outcome, path)


def test_cli_explicit_live_invalid_input_and_safe_settings_errors(monkeypatch, capsys):
    from travel_agent.page_cli import main

    def forbidden(*args, **kwargs):
        pytest.fail("invalid input must not read settings or call providers")

    monkeypatch.setattr("travel_agent.page_cli.DeepSeekSettings.from_env", forbidden)
    for argv, raw, issue in (
        (["page"], "", "live_flag_required"),
        (["page", "--live"], "private-invalid-json", "invalid_page_input"),
        (["page", "--live"], "x" * 8193, "invalid_page_input"),
        (["page", "--live"], json.dumps({"url": "http://127.0.0.1/"}), "invalid_page_input"),
    ):
        monkeypatch.setattr("sys.argv", argv)
        monkeypatch.setattr("sys.stdin", io.StringIO(raw))
        assert main() == 2
        output = capsys.readouterr()
        assert issue in output.err
        assert "private-invalid-json" not in output.err


def test_cli_content_output_is_explicit_and_never_in_audit(monkeypatch, capsys, tmp_path):
    from travel_agent.page_cli import main

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("travel_agent.page_cli.DeepSeekSettings.from_env", lambda: SETTINGS)
    monkeypatch.setattr("travel_agent.page_cli.read_page", lambda *args, **kwargs: read())
    for flags in ([], ["--include-content"]):
        monkeypatch.setattr("sys.argv", ["page", "--live", *flags])
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"url": URL})))
        assert main() == 0
        output = json.loads(capsys.readouterr().out)
        assert ("content" in output["page"]) == bool(flags)
    with closing(sqlite3.connect(tmp_path / "runtime/evidence.sqlite3")) as db:
        for (raw,) in db.execute("SELECT metadata_json FROM page_reads"):
            assert CONTENT not in raw


def test_cli_invalid_budget_and_provider_settings_are_safe(monkeypatch, capsys):
    from travel_agent.page_cli import main

    monkeypatch.setattr("sys.argv", ["page", "--live"])
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"url": URL})))
    monkeypatch.setenv("SERVICE_BUDGET_MINOR", "201")
    assert main() == 2
    assert "invalid_service_budget" in capsys.readouterr().err
    monkeypatch.setenv("SERVICE_BUDGET_MINOR", "200")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"url": URL})))

    def invalid_settings():
        raise ProviderError("missing_or_invalid_api_key")

    monkeypatch.setattr("travel_agent.page_cli.DeepSeekSettings.from_env", invalid_settings)
    assert main() == 2
    assert "missing_or_invalid_api_key" in capsys.readouterr().err


def test_cli_failure_and_audit_failure_are_not_success(monkeypatch, capsys, tmp_path):
    from travel_agent.page_cli import main

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["page", "--live"])
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"url": URL})))
    monkeypatch.setattr("travel_agent.page_cli.DeepSeekSettings.from_env", lambda: SETTINGS)
    monkeypatch.setattr("travel_agent.page_cli.read_page", lambda *args, **kwargs: read(status=404))
    assert main() == 2
    assert json.loads(capsys.readouterr().out)["status"] == "unavailable"
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"url": URL})))

    def failed_audit(*args, **kwargs):
        raise OSError("private-file-detail")

    monkeypatch.setattr("travel_agent.page_cli.save_page_audit", failed_audit)
    monkeypatch.setattr("travel_agent.page_cli.read_page", lambda *args, **kwargs: read())
    assert main() == 3
    output = capsys.readouterr()
    assert "page_audit_save_failed" in output.err
    assert "private-file-detail" not in output.err
    assert json.loads(output.out)["status"] == "acquired"
