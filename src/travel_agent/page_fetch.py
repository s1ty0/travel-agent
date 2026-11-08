"""One bounded provider-side page read; reviewed URLs only, no truth certification."""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from time import monotonic
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import Field, model_validator

from .deepseek import DeepSeekSettings, ProviderError
from .models import Contract
from .references import digest, locate_quote
from .service_budget import PriceCard, ServiceBudget
from .sources import _json_request


@dataclass(frozen=True)
class ReadPolicy:
    key: str
    url: str
    requested_site_category: str
    reviewed_on: date = date(2026, 11, 8)
    valid_until: date = date(2026, 11, 15)


# A read scope, not a license to archive or proof of the returned document's origin.
# Exact URLs: no arbitrary path, query, subdomain, port, or credential forwarding.
READ_POLICIES = (
    ReadPolicy(
        "nanjing_museum_visit_candidate",
        "https://www.njmuseum.org.cn/zh/visitIndex",
        "venue_site_candidate",
    ),
    ReadPolicy(
        "nanjing_reservation_news_2024",
        "https://www.nanjing.gov.cn/njxx/202407/t20240704_4706024.html",
        "government_portal_news",
    ),
)


class PageQuote(Contract):
    quote: Annotated[str, Field(min_length=2, max_length=300)]
    start_offset: Annotated[int, Field(strict=True, ge=0)] | None = None

    @model_validator(mode="after")
    def not_blank(self):
        if not self.quote.strip():
            raise ValueError("quote_empty")
        return self


class ReviewedPageURL(Contract):
    url: Annotated[str, Field(min_length=1, max_length=2048)]

    @model_validator(mode="after")
    def reviewed_url_only(self):
        if self.url not in {policy.url for policy in READ_POLICIES}:
            raise ValueError("page_url_not_reviewed")
        return self


class PageReadInput(ReviewedPageURL):
    quotes: Annotated[tuple[PageQuote, ...], Field(max_length=10)] = ()


class FetchedPage(Contract):
    url: str
    title: Annotated[str, Field(max_length=300, repr=False)]
    content: Annotated[str, Field(min_length=1, max_length=80000, repr=False)]
    retrieved_at: datetime
    evidence_kind: Literal["provider_markdown"] = "provider_markdown"
    facts_verified: Literal[False] = False

    @model_validator(mode="after")
    def valid_page(self):
        if not self.content.strip() or self.retrieved_at.utcoffset() is None:
            raise ValueError("invalid_page_content_or_timestamp")
        return self

    def metadata(self):
        content_hash = digest(self.content)
        return {
            "source_id": "page_" + digest(self.url + "\n" + content_hash),
            "requested_url": self.url,
            "content_sha256": content_hash,
            "content_characters": len(self.content),
            "retrieved_at": self.retrieved_at.isoformat(),
            "evidence_kind": self.evidence_kind,
            "final_url": None,
            "redirect_chain": None,
            "content_origin_verified": False,
            "published_at": None,
            "date_applicability": "unknown",
            "semantic_support": "not_assessed",
            "facts_verified": False,
        }


@dataclass(frozen=True)
class PageReadOutcome:
    run_id: str
    status: str
    issue: str | None
    page: FetchedPage | None = field(repr=False)
    policy: ReadPolicy
    quotes: tuple[dict, ...]
    service_cost: dict
    call_count: int
    elapsed_ms: int
    price_checked_on: date
    price_valid_until: date

    def report(self, *, include_content=False):
        result = {
            "run_id": self.run_id,
            "status": self.status,
            "issue": self.issue,
            "provider": "deepseek_fetch",
            "page": self.page.metadata() if self.page else None,
            "read_policy": {
                "key": self.policy.key,
                "requested_url": self.policy.url,
                "requested_site_category": self.policy.requested_site_category,
                "reviewed_on": self.policy.reviewed_on.isoformat(),
                "valid_until": self.policy.valid_until.isoformat(),
                "identity_scope": "reviewed_requested_url_only",
                "retention": "metadata_only",
            },
            "quote_checks": list(self.quotes),
            "service_cost": self.service_cost,
            "call_count": self.call_count,
            "elapsed_ms": self.elapsed_ms,
            "price_checked_on": self.price_checked_on.isoformat(),
            "price_valid_until": self.price_valid_until.isoformat(),
            "facts_verified": False,
        }
        if include_content and self.page:
            result["page"]["title"] = self.page.title
            result["page"]["content"] = self.page.content
        return result


def read_page(
    query: PageReadInput,
    settings: DeepSeekSettings,
    *,
    ledger: ServiceBudget | None = None,
    prices: PriceCard | None = None,
    today: date | None = None,
    transport=None,
) -> PageReadOutcome:
    start = monotonic()
    run_id = str(uuid4())
    ledger = ledger if ledger is not None else ServiceBudget()
    prices = prices if prices is not None else PriceCard()
    today = today if today is not None else datetime.now(UTC).date()
    policy = next(policy for policy in READ_POLICIES if policy.url == query.url)
    page, issue, calls, checks = None, None, 0, ()
    status = "stopped"
    try:
        if not policy.reviewed_on <= today <= policy.valid_until:
            raise ProviderError("page_read_policy_expired")
        prices.validate_fetch(today)
        ledger.reserve(run_id, prices.fetch_micro_cny_per_call)
        calls = 1
        body = _json_request(
            "https://api.deepseek.com/v1/tools/fetch",
            timeout=settings.timeout_seconds,
            secret=settings.api_key,
            payload={"url": query.url},
            transport=transport,
            error_codes={
                400: "fetch_input_rejected",
                404: "page_content_unavailable",
                408: "upstream_timeout",
                413: "fetch_input_rejected",
                504: "upstream_timeout",
            },
        )
        title, content = body.get("title"), body.get("content")
        if not isinstance(title, str) or not isinstance(content, str):
            raise ProviderError("invalid_fetch_response")
        if not content.strip():
            raise ProviderError("page_content_unavailable")
        if len(title) > 300 or len(content) > 80000:
            raise ProviderError("page_content_too_large")
        try:
            title.encode("utf-8")
            content.encode("utf-8")
        except UnicodeError:
            raise ProviderError("invalid_fetch_response") from None
        page = FetchedPage(
            url=query.url, title=title, content=content, retrieved_at=datetime.now(UTC)
        )
        ledger.settle(run_id, prices.fetch_micro_cny_per_call)
        checks = tuple(
            {
                **locate_quote(content, item.quote, item.start_offset),
                "content_sha256": digest(content),
                "date_applicability": "unknown",
                "semantic_support": "not_assessed",
                "facts_verified": False,
            }
            for item in query.quotes
        )
        status = "acquired"
    except ProviderError as exc:
        issue = str(exc)
        if calls:
            # Failed/empty requests may not be billed. Keep the upper reservation:
            # the local client cannot reconcile account charges after ambiguous failures.
            ledger.unknown()
            status = "unavailable"
    return PageReadOutcome(
        run_id=run_id,
        status=status,
        issue=issue,
        page=page,
        policy=policy,
        quotes=checks,
        service_cost=ledger.summary(),
        call_count=calls,
        elapsed_ms=int((monotonic() - start) * 1000),
        price_checked_on=prices.checked_on,
        price_valid_until=prices.valid_until,
    )
