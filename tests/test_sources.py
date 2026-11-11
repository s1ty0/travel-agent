import io
import json
import sqlite3
from contextlib import closing

import httpx
import pytest
from pydantic import ValidationError

from travel_agent.deepseek import DeepSeekSettings, ProviderError
from travel_agent.evidence_store import save_provenance
from travel_agent.source_models import (
    Coordinate,
    NearbyQuery,
    PlaceQuery,
    SearchQuery,
    WalkingQuery,
)
from travel_agent.sources import (
    amap_key_from_env,
    find_places,
    public_url,
    search_web,
    walking_route,
)

KEY = "test-source-credential-not-real"
SETTINGS = DeepSeekSettings(KEY)
ORIGIN = Coordinate(longitude=118.8, latitude=32.0)
DESTINATION = Coordinate(longitude=118.81, latitude=32.01)


def transport(data, status=200):
    return httpx.MockTransport(lambda request: httpx.Response(status, json=data))


def hit(url="https://example.com/place#details", **kwargs):
    return {"url": url, "title": "Example source", "snippet": "Unverified search lead", **kwargs}


def search(records=None):
    return search_web(
        SearchQuery(query="南京景点", count=3),
        SETTINGS,
        transport=transport({"results": [hit()] if records is None else records}),
    )


@pytest.mark.smoke
def test_search_contract_uses_fixed_host_and_no_full_text():
    calls = []

    def handle(request):
        calls.append(request)
        assert str(request.url) == "https://api.deepseek.com/v1/tools/search"
        assert request.headers["authorization"] == "Bearer " + KEY
        assert json.loads(request.content) == {
            "query": "南京景点",
            "count": 3,
            "need_content": False,
        }
        return httpx.Response(200, json={"results": [hit()]})

    batch = search_web(
        SearchQuery(query="南京景点", count=3), SETTINGS, transport=httpx.MockTransport(handle)
    )
    assert len(calls) == 1
    assert batch.status == "ok"
    assert batch.hits[0].url == "https://example.com/place"
    assert batch.hits[0].evidence_kind == "search_snippet"
    assert batch.facts_verified is False
    assert batch.retrieved_at.utcoffset() is not None
    assert batch.pagination_status == "not_supported"
    assert batch.billed_amount_minor is None
    assert len(batch.query_sha256) == 64
    assert KEY not in batch.model_dump_json()


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "file:///x",
        "http://localhost/x",
        "https://a.local/x",
        "http://127.0.0.1/x",
        "http://[::1]/",
        "http://169.254.169.254/",
        "https://user:pass@example.com/x",
        "https://example.com:8080/x",
        "http://internal/x",
        "https://example.com/?api_key=secret",
        "https://example.com/?access_token=secret",
        "https://example.com/?poc_token=secret",
        "https://exa mple.com/x",
        "//example.com/x",
        "https://example.com:wrong/",
    ],
)
def test_unsafe_links_rejected(url):
    with pytest.raises(ValueError):
        public_url(url)


def test_search_empty_partial_and_duplicates_are_distinct():
    assert search([]).status == "empty"
    batch = search([hit(), hit(), {"url": "javascript:alert(1)", "title": "bad"}])
    assert batch.status == "partial"
    assert len(batch.hits) == 1
    assert batch.received_count == 3
    assert "duplicate_source:1" in batch.issues
    assert "invalid_search_record:2" in batch.issues
    assert search([{"title": "missing url"}]).status == "partial"


def test_search_extra_count_and_bad_optional_fields():
    batch = search([hit(f"https://example.com/{i}") for i in range(4)])
    assert len(batch.hits) == 3
    assert "provider_exceeded_requested_count" in batch.issues
    for record in [hit(title=5), hit(snippet=5), hit(date=5), None]:
        assert search([record]).status == "partial"
    fallback = search([{"url": "https://example.com/", "title": "title", "summary": "summary"}])
    assert fallback.hits[0].snippet == "summary"


@pytest.mark.parametrize("body", [[], None, {"results": {}}, {"results": None}])
def test_bad_search_envelope(body):
    with pytest.raises(ProviderError, match="invalid_source_response"):
        search_web(SearchQuery(query="南京"), SETTINGS, transport=transport(body))


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "authentication_failed"),
        (402, "insufficient_balance"),
        (403, "permission_denied"),
        (429, "rate_limited"),
        (302, "upstream_error"),
        (503, "upstream_error"),
    ],
)
def test_source_http_errors_safe(status, code):
    with pytest.raises(ProviderError, match=code) as error:
        search_web(
            SearchQuery(query="南京"), SETTINGS, transport=transport({"sensitive": KEY}, status)
        )
    assert KEY not in str(error.value)


@pytest.mark.parametrize(
    "error,code",
    [
        (httpx.ReadTimeout(KEY), "upstream_timeout"),
        (httpx.ConnectError(KEY), "upstream_connection_error"),
    ],
)
def test_source_connection_errors_safe(error, code):
    def fail(request):
        raise error

    with pytest.raises(ProviderError, match=code):
        search_web(SearchQuery(query="南京"), SETTINGS, transport=httpx.MockTransport(fail))


def test_bounded_and_malformed_source_responses():
    for content, code in [
        (b"x" * (512 * 1024 + 1), "source_response_too_large"),
        (b"not json", "invalid_source_response"),
    ]:
        with pytest.raises(ProviderError, match=code):
            search_web(
                SearchQuery(query="南京"),
                SETTINGS,
                transport=httpx.MockTransport(
                    lambda request, content=content: httpx.Response(200, content=content)
                ),
            )


def place(**changes):
    return {
        "id": "TEST-POI-1",
        "name": "Synthetic place",
        "location": "118.800000,32.000000",
        "address": "Synthetic address",
        "adcode": "320100",
        **changes,
    }


def places(records=None, **body):
    return find_places(
        PlaceQuery(keywords="公园", city="南京"),
        KEY,
        transport=transport(
            {"status": "1", "count": "1", "pois": [place()] if records is None else records, **body}
        ),
    )


@pytest.mark.smoke
def test_amap_city_limited_query_and_private_key():
    def handle(request):
        assert request.url.host == "restapi.amap.com"
        assert request.url.path == "/v3/place/text"
        assert request.url.params["key"] == KEY
        assert request.url.params["citylimit"] == "true"
        assert request.url.params["city"] == "南京"
        return httpx.Response(200, json={"status": "1", "pois": [place()], "count": "1"})

    batch = find_places(
        PlaceQuery(keywords="公园", city="南京"), KEY, transport=httpx.MockTransport(handle)
    )
    assert batch.hits[0].coordinate.system == "GCJ-02"
    assert batch.hits[0].coordinate.longitude == 118.8
    assert KEY not in batch.model_dump_json()
    assert batch.pagination_status == "end"


def test_nearby_query_does_not_become_city_discovery():
    def handle(request):
        assert request.url.path == "/v3/place/around"
        assert request.url.params["location"] == "118.800000,32.000000"
        assert request.url.params["radius"] == "5000"
        assert request.url.params["sortrule"] == "distance"
        return httpx.Response(200, json={"status": "1", "count": "0", "pois": []})

    batch = find_places(
        NearbyQuery(keywords="餐厅", center=ORIGIN), KEY, transport=httpx.MockTransport(handle)
    )
    assert batch.operation == "nearby"
    assert batch.status == "empty"


@pytest.mark.parametrize(
    "code,error",
    [
        ("10001", "authentication_failed"),
        ("10003", "rate_limited"),
        ("10004", "rate_limited"),
        ("10007", "permission_denied"),
        ("10009", "permission_denied"),
        ("99999", "amap_rejected"),
    ],
)
def test_amap_application_errors_even_on_http_200(code, error):
    with pytest.raises(ProviderError, match=error):
        places(status="0", infocode=code, info=KEY)


def test_amap_invalid_or_missing_coordinates_stay_explicit():
    for raw in ("200,32", "118.1234567,32", "nan,32", 123):
        assert places([place(location=raw)]).status == "partial"
    for raw in (None, "", []):
        assert places([place(location=raw)]).hits[0].coordinate is None
    assert places([place(address=[], adcode=[])]).hits[0].address is None
    assert places([place(address=5)]).status == "partial"
    assert places([place(), place()]).issues == ("duplicate_place:1",)
    assert places([place(name="")]).status == "partial"
    with pytest.raises(ProviderError):
        places({}, status="1")


def test_amap_pagination_without_auto_fetch():
    batch = places(count="12")
    assert batch.next_page == 2
    assert batch.pagination_status == "more"
    assert places(count=None).pagination_status == "unknown"
    batch = find_places(
        PlaceQuery(keywords="公园", city="南京", page=8, count=25),
        KEY,
        transport=transport({"status": "1", "count": "201", "pois": [place()]}),
    )
    assert batch.pagination_status == "limit_reached"
    assert batch.next_page is None


def test_walking_uses_provider_seconds_not_straight_line_estimate():
    def handle(request):
        assert request.url.path == "/v3/direction/walking"
        assert request.url.params["origin"] == ORIGIN.as_amap()
        return httpx.Response(
            200,
            json={"status": "1", "route": {"paths": [{"distance": "1500", "duration": "1200"}]}},
        )

    batch = walking_route(
        WalkingQuery(origin=ORIGIN, destination=DESTINATION),
        KEY,
        transport=httpx.MockTransport(handle),
    )
    assert batch.hits[0].distance_meters == 1500
    assert batch.hits[0].duration_seconds == 1200
    assert batch.facts_verified is False
    assert batch.hits[0].mode == "walking"


@pytest.mark.parametrize(
    "route",
    [
        {},
        {"paths": None},
        {"paths": [{"distance": "1"}]},
        {"paths": [{"distance": "-1", "duration": "5"}]},
        {"paths": [{"distance": 1, "duration": 5}]},
    ],
)
def test_bad_routes_fail_closed(route):
    with pytest.raises(ProviderError):
        walking_route(
            WalkingQuery(origin=ORIGIN, destination=DESTINATION),
            KEY,
            transport=transport({"status": "1", "route": route}),
        )


def test_empty_route_does_not_invent_path():
    batch = walking_route(
        WalkingQuery(origin=ORIGIN, destination=DESTINATION),
        KEY,
        transport=transport({"status": "1", "route": {"paths": []}}),
    )
    assert batch.status == "empty"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"query": " "},
        {"query": "x" * 129},
        {"query": "南京", "count": True},
        {"query": "南京", "count": 51},
    ],
)
def test_invalid_search_requests(kwargs):
    with pytest.raises(ValidationError):
        SearchQuery(**kwargs)


def test_geo_and_key_configuration_validation(monkeypatch):
    for call in (
        lambda: Coordinate(longitude=float("nan"), latitude=32),
        lambda: Coordinate(longitude=118, latitude=91),
        lambda: Coordinate(longitude=118, latitude=32, system="unsupported"),
        lambda: PlaceQuery(keywords=" ", city="南京"),
        lambda: PlaceQuery(keywords="公园", city="南京", page=9),
        lambda: NearbyQuery(keywords="餐厅", center=ORIGIN, radius_meters=80000),
        lambda: NearbyQuery(keywords=" ", center=ORIGIN),
    ):
        with pytest.raises(ValidationError):
            call()
    with pytest.raises(ValueError, match="amap_requires_gcj02"):
        Coordinate(longitude=118, latitude=32, system="WGS84").as_amap()
    monkeypatch.delenv("AMAP_WEB_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="missing_or_invalid_amap_key"):
        amap_key_from_env()
    with pytest.raises(ProviderError):
        find_places(PlaceQuery(keywords="公园", city="南京"), " ")
    monkeypatch.setenv("AMAP_WEB_API_KEY", KEY)
    assert amap_key_from_env() == KEY


def test_provenance_is_private_minimal_and_append_only(tmp_path):
    batch = search()
    path = tmp_path / "private/evidence.sqlite3"
    save_provenance(batch, path)
    with closing(sqlite3.connect(path)) as db:
        row = db.execute("SELECT metadata_json,sources_json FROM source_runs").fetchone()
    metadata, sources = map(json.loads, row)
    assert metadata["query_sha256"] == batch.query_sha256
    assert sources[0]["locator"] == batch.hits[0].url
    assert "南京景点" not in str(row)
    assert "Unverified search lead" not in str(row)
    assert KEY not in str(row)
    with pytest.raises(sqlite3.IntegrityError):
        save_provenance(batch, path)
    save_provenance(places(), path)
    route = walking_route(
        WalkingQuery(origin=ORIGIN, destination=DESTINATION),
        KEY,
        transport=transport(
            {"status": "1", "route": {"paths": [{"distance": "0", "duration": "0"}]}}
        ),
    )
    save_provenance(route, path)


def test_source_cli_opt_in_and_invalid_input(monkeypatch, capsys):
    from travel_agent.sources_cli import main

    monkeypatch.setattr("sys.argv", ["sources", "search"])
    assert main() == 2
    assert "live_flag_required" in capsys.readouterr().err
    monkeypatch.setattr("sys.argv", ["sources", "search", "--live"])
    for raw in ("bad json", "x" * 8193):
        monkeypatch.setattr("sys.stdin", io.StringIO(raw))
        assert main() == 2
        assert "invalid_source_input" in capsys.readouterr().err


def test_source_cli_success_missing_key_and_save_failure(monkeypatch, capsys, tmp_path):
    from travel_agent.sources_cli import main

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["sources", "search", "--live"])
    monkeypatch.setattr("sys.stdin", io.StringIO('{"query":"南京"}'))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert main() == 2
    assert "missing_or_invalid_api_key" in capsys.readouterr().err
    monkeypatch.setenv("DEEPSEEK_API_KEY", KEY)
    monkeypatch.setattr("travel_agent.sources_cli.search_web", lambda *args: search())
    monkeypatch.setattr("sys.stdin", io.StringIO('{"query":"南京"}'))
    assert main() == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ok"
    assert (tmp_path / "runtime/evidence.sqlite3").is_file()

    def fail_save(*args):
        raise OSError("private path must not leak")

    monkeypatch.setattr("travel_agent.sources_cli.save_provenance", fail_save)
    monkeypatch.setattr("sys.stdin", io.StringIO('{"query":"南京"}'))
    assert main() == 3
    captured = capsys.readouterr()
    assert "provenance_save_failed" in captured.err
    assert "private path" not in captured.err
    assert json.loads(captured.out)["hits"]


@pytest.mark.parametrize(
    "operation,payload",
    [
        ("places", {"keywords": "公园", "city": "南京"}),
        ("nearby", {"keywords": "公园", "center": {"longitude": 118.8, "latitude": 32}}),
        (
            "walking",
            {
                "origin": {"longitude": 118.8, "latitude": 32},
                "destination": {"longitude": 118.81, "latitude": 32.01},
            },
        ),
    ],
)
def test_map_cli_dispatch_without_network(monkeypatch, capsys, tmp_path, operation, payload):
    from travel_agent.sources_cli import main

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AMAP_WEB_API_KEY", KEY)
    monkeypatch.setattr("sys.argv", ["sources", operation, "--live"])
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr("travel_agent.sources_cli.find_places", lambda *args: places())
    monkeypatch.setattr("travel_agent.sources_cli.walking_route", lambda *args: places())
    assert main() == 0
    assert json.loads(capsys.readouterr().out)["provider"] == "amap"


def test_metadata_validation_cannot_mark_partial_as_ok():
    from datetime import datetime

    from travel_agent.source_models import SourceBatch

    data = search().model_dump()
    for changes in (
        {"retrieved_at": datetime(2026, 11, 8)},
        {"hits": (*data["hits"], *data["hits"])},
        {"status": "ok", "issues": ("missing",)},
        {"status": "empty"},
    ):
        with pytest.raises(ValidationError):
            SourceBatch.model_validate({**data, **changes})
