"""Bounded official-source adapters; no URL fetching, model generation, or purchases."""

import hashlib
import ipaddress
import json
import os
import re
from datetime import UTC, datetime
from time import monotonic
from urllib.parse import parse_qsl, urlsplit, urlunsplit
from uuid import uuid4

import httpx
from pydantic import ValidationError

from .deepseek import DeepSeekSettings, ProviderError
from .source_models import (
    Coordinate,
    NearbyQuery,
    PlaceHit,
    PlaceQuery,
    SearchHit,
    SearchQuery,
    SourceBatch,
    WalkingPath,
    WalkingQuery,
)


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def public_url(value: str) -> str:
    """Validate a display link; never resolve or request it here."""
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme not in ("https", "http")
        or not host
        or parsed.username
        or parsed.password
        or any(c.isspace() or ord(c) < 32 for c in value)
        or host == "localhost"
        or host.endswith((".localhost", ".local", ".internal"))
    ):
        raise ValueError("invalid_source_link")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if "." not in host:
            raise ValueError("invalid_source_link") from None
    else:
        if not address.is_global:
            raise ValueError("invalid_source_link")
    if parsed.port not in (None, 80, 443):
        raise ValueError("invalid_source_link")
    if any(
        (
            name.lower().replace("-", "_") in {"key", "api_key", "authorization"}
            or name.lower().endswith("token")
        )
        for name, _ in parse_qsl(parsed.query)
    ):
        raise ValueError("credential_in_source_link")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, parsed.query, ""))


def _json_request(
    url,
    *,
    timeout,
    secret,
    method="POST",
    payload=None,
    params=None,
    transport=None,
    error_codes=None,
):
    try:
        with httpx.Client(
            timeout=timeout, trust_env=False, follow_redirects=False, transport=transport
        ) as client:
            with client.stream(
                method,
                url,
                json=payload,
                params=params,
                headers={"Authorization": f"Bearer {secret}"} if method == "POST" else {},
            ) as response:
                if response.status_code != 200:
                    codes = {
                        401: "authentication_failed",
                        402: "insufficient_balance",
                        403: "permission_denied",
                        429: "rate_limited",
                    }
                    codes.update(error_codes or {})
                    code = codes.get(response.status_code, "upstream_error")
                    raise ProviderError(code)
                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > 512 * 1024:
                        raise ProviderError("source_response_too_large")
        data = json.loads(content)
        if not isinstance(data, dict):
            raise ProviderError("invalid_source_response")
        return data
    except httpx.TimeoutException:
        raise ProviderError("upstream_timeout") from None
    except httpx.HTTPError:
        raise ProviderError("upstream_connection_error") from None
    except (TypeError, ValueError):
        raise ProviderError("invalid_source_response") from None


def _batch(
    provider,
    operation,
    query,
    count,
    received,
    hits,
    issues,
    start,
    pagination="not_supported",
    next_page=None,
):
    status = "partial" if issues else "ok" if hits else "empty"
    return SourceBatch(
        run_id=str(uuid4()),
        provider=provider,
        operation=operation,
        retrieved_at=datetime.now(UTC),
        query_sha256=fingerprint(query),
        requested_count=count,
        received_count=received,
        status=status,
        hits=tuple(hits),
        issues=tuple(issues),
        next_page=next_page,
        pagination_status=pagination,
        elapsed_ms=int((monotonic() - start) * 1000),
    )


def search_web(query: SearchQuery, settings: DeepSeekSettings, *, transport=None) -> SourceBatch:
    start = monotonic()
    body = _json_request(
        "https://api.deepseek.com/v1/tools/search",
        timeout=settings.timeout_seconds,
        secret=settings.api_key,
        payload={"query": query.query, "count": query.count, "need_content": False},
        transport=transport,
    )
    records = body.get("results")
    if not isinstance(records, list):
        raise ProviderError("invalid_source_response")
    hits, issues, seen = [], [], set()
    if len(records) > query.count:
        issues.append("provider_exceeded_requested_count")
    for index, row in enumerate(records[: query.count]):
        try:
            url = public_url(row["url"])
            if url in seen:
                issues.append(f"duplicate_source:{index}")
                continue
            title = row["title"]
            snippet = row.get("snippet") or row.get("summary") or ""
            date = row.get("date")
            if (
                not isinstance(title, str)
                or not title.strip()
                or not isinstance(snippet, str)
                or (date is not None and not isinstance(date, str))
            ):
                raise ValueError("invalid_record")
            hits.append(
                SearchHit(
                    source_id="web_" + fingerprint(url)[:20],
                    url=url,
                    title=title[:300],
                    snippet=snippet[:1200],
                    published_at=date,
                )
            )
            seen.add(url)
        except (KeyError, TypeError, ValueError, AttributeError):
            issues.append(f"invalid_search_record:{index}")
    return _batch(
        "deepseek_search",
        "search",
        query.model_dump_json(),
        query.count,
        len(records),
        hits,
        issues,
        start,
    )


def amap_key_from_env():
    key = os.environ.get("AMAP_WEB_API_KEY", "")
    if not key.strip() or any(c.isspace() for c in key):
        raise ProviderError("missing_or_invalid_amap_key")
    return key


def _amap(path, params, key, transport, timeout=30):
    if not key.strip() or any(c.isspace() for c in key):
        raise ProviderError("missing_or_invalid_amap_key")
    body = _json_request(
        "https://restapi.amap.com/v3/" + path,
        timeout=timeout,
        secret=key,
        method="GET",
        params={**params, "key": key, "output": "JSON"},
        transport=transport,
    )
    if body.get("status") != "1":
        code = {
            "10001": "authentication_failed",
            "10003": "rate_limited",
            "10004": "rate_limited",
            "10007": "permission_denied",
            "10009": "permission_denied",
        }.get(body.get("infocode"), "amap_rejected")
        raise ProviderError(code)
    return body


def _coordinate(raw):
    if not isinstance(raw, str) or not re.fullmatch(
        r"-?\d+(?:\.\d{1,6})?,-?\d+(?:\.\d{1,6})?", raw
    ):
        raise ValueError("invalid_coordinate")
    lon, lat = raw.split(",")
    return Coordinate(longitude=float(lon), latitude=float(lat))


def _optional_text(value):
    if value is None or value == [] or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError("invalid_text")
    return value[:500]


def find_places(
    query: PlaceQuery | NearbyQuery, key: str, *, transport=None, timeout=30
) -> SourceBatch:
    start = monotonic()
    params = {
        "keywords": query.keywords,
        "offset": query.count,
        "page": query.page,
        "extensions": "base",
    }
    if isinstance(query, NearbyQuery):
        operation, path = "nearby", "place/around"
        params.update(
            location=query.center.as_amap(), radius=query.radius_meters, sortrule="distance"
        )
    else:
        operation, path = "places", "place/text"
        params.update(city=query.city, citylimit="true")
    body = _amap(path, params, key, transport, timeout)
    records = body.get("pois")
    if not isinstance(records, list):
        raise ProviderError("invalid_source_response")
    hits, issues, seen = [], [], set()
    if len(records) > query.count:
        issues.append("provider_exceeded_requested_count")
    for index, row in enumerate(records[: query.count]):
        try:
            poi_id, name = row["id"], row["name"]
            if not all(isinstance(v, str) and v.strip() for v in (poi_id, name)):
                raise ValueError("invalid_place")
            if poi_id in seen:
                issues.append(f"duplicate_place:{index}")
                continue
            location = row.get("location")
            coordinate = None
            if location not in (None, "", []):
                coordinate = _coordinate(location)
            hits.append(
                PlaceHit(
                    source_id="amap_" + fingerprint(poi_id)[:20],
                    poi_id=poi_id,
                    name=name[:300],
                    coordinate=coordinate,
                    address=_optional_text(row.get("address")),
                    adcode=_optional_text(row.get("adcode")),
                )
            )
            seen.add(poi_id)
        except (KeyError, TypeError, ValueError, AttributeError, ValidationError):
            issues.append(f"invalid_place_record:{index}")
    # 'count' can be unavailable/inconsistent: never invent the next page.
    total = body.get("count")
    if isinstance(total, str) and total.isdigit():
        more = int(total) > query.page * query.count
        page_status = "limit_reached" if more and query.page == 8 else "more" if more else "end"
    else:
        page_status = "unknown"
    return _batch(
        "amap",
        operation,
        query.model_dump_json(),
        query.count,
        len(records),
        hits,
        issues,
        start,
        page_status,
        query.page + 1 if page_status == "more" else None,
    )


def walking_route(query: WalkingQuery, key: str, *, transport=None, timeout=30) -> SourceBatch:
    start = monotonic()
    body = _amap(
        "direction/walking",
        {"origin": query.origin.as_amap(), "destination": query.destination.as_amap()},
        key,
        transport,
        timeout,
    )
    try:
        records = body["route"]["paths"]
        if not isinstance(records, list):
            raise ValueError("invalid_paths")
        hits = []
        for index, row in enumerate(records):
            distance, duration = row["distance"], row["duration"]
            if not all(isinstance(v, str) and v.isdigit() for v in (distance, duration)):
                raise ValueError("invalid_path")
            hits.append(
                WalkingPath(
                    source_id=f"route_{fingerprint(query.model_dump_json())[:16]}_{index}",
                    distance_meters=int(distance),
                    duration_seconds=int(duration),
                )
            )
    except (KeyError, TypeError, ValueError):
        raise ProviderError("invalid_source_response") from None
    return _batch("amap", "walking", query.model_dump_json(), 1, len(records), hits, [], start)
