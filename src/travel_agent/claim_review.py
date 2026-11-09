"""Bounded reservation-rule review. Textual support never certifies real-world facts."""

import json
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .models import Contract
from .page_fetch import READ_POLICIES
from .references import ReferenceInput, digest, import_reference, locate_quote

RULE_VERSION = "reservation-rule-v1.1"
PUBLISHER_PROFILES = {
    "nanjing_museum_visit_candidate": {
        "label": "南京博物院场馆站点候选",
        "basis": "candidate_only_no_independent_domain_attestation",
    },
    "nanjing_reservation_news_2024": {
        "label": "南京市政府门户历史新闻页",
        "basis": "publisher_self_disclosure_on_requested_page",
        "profile_evidence_url": "https://www.nanjing.gov.cn/njxx/202407/t20240704_4706024.html",
        "observed_publication_date": "2024-07-04",
    },
}


class FactClaim(Contract):
    subject: Annotated[str, Field(min_length=1, max_length=100)]
    predicate: Literal["reservation_required"]
    value: Annotated[bool, Field(strict=True)]
    source_id: Annotated[str, Field(min_length=1, max_length=200)]
    quote: Annotated[str, Field(min_length=2, max_length=300)]
    start_offset: Annotated[int, Field(strict=True, ge=0)] | None = None

    @model_validator(mode="after")
    def not_blank(self):
        if not self.subject.strip() or not self.quote.strip():
            raise ValueError("claim_text_empty")
        return self


@dataclass(frozen=True)
class EvidenceText:
    source_id: str
    text: str | None = field(repr=False)
    kind: str
    url: str | None = None


def assess_identity(evidence: EvidenceText, today: date):
    report = {
        "status": "unverified",
        "basis": "no_independent_origin_proof",
        "publisher_role": "unknown",
        "content_origin_verified": False,
    }
    if evidence.kind in ("user_text", "link_only"):
        report.update(basis="user_supplied_not_authenticated", publisher_role="user_supplied")
    elif evidence.kind == "search_snippet":
        report.update(basis="search_snippet_only", publisher_role="search_summary")
    elif evidence.kind == "provider_markdown":
        policy = next((p for p in READ_POLICIES if p.url == evidence.url), None)
        if policy:
            report.update(
                basis="reviewed_requested_url_only",
                publisher_role=policy.requested_site_category,
                profile_checked_on=policy.reviewed_on.isoformat(),
                profile_valid_until=policy.valid_until.isoformat(),
                profile_current=policy.reviewed_on <= today <= policy.valid_until,
                publisher_profile=dict(PUBLISHER_PROFILES[policy.key]),
            )
    return report


def _blocks(text):
    start = 0
    for separator in re.finditer(r"\r?\n[ \t]*\r?\n", text):
        if text[start : separator.start()].strip():
            yield start, separator.start(), text[start : separator.start()]
        start = separator.end()
    if text[start:].strip():
        yield start, len(text), text[start:]


DATE_ATOM = r"(?:\d{4}-\d{2}-\d{2}|\d{4}年\d{1,2}月\d{1,2}日)"
DATE_PREFIX = re.compile(rf"^({DATE_ATOM})(?:至|到)({DATE_ATOM})[，,:：](.+)$")


def _parse_date(value):
    if "年" in value:
        year, month, day = map(int, re.findall(r"\d+", value))
        return date(year, month, day)
    return date.fromisoformat(value)


def _parse_rule(block, subject):
    statement = block.strip()
    start, end, date_issue = None, None, None
    prefix = DATE_PREFIX.fullmatch(statement)
    if prefix:
        statement = prefix[3]
        try:
            start, end = _parse_date(prefix[1]), _parse_date(prefix[2])
            if end < start:
                raise ValueError("reversed_range")
        except ValueError:
            start, end, date_issue = None, None, "invalid_textual_range"
    # Whole paragraph, exact named subject, no dropped negation/condition/exception.
    forms = {
        "实行“全员预约机制”": True,
        '实行"全员预约机制"': True,
        "须预约": True,
        "须实名预约": True,
        "需要预约": True,
        "需要实名预约": True,
        "无需预约": False,
        "不需要预约": False,
    }
    for suffix, required in forms.items():
        if re.fullmatch(re.escape(subject + suffix) + r"[。.]?", statement):
            return {"value": required, "start": start, "end": end, "date_issue": date_issue}
    return None


def _date_report(rule, at_date):
    result = {
        "status": "unknown",
        "at_date": at_date.isoformat() if at_date else None,
        "basis": "explicit_range_in_same_source_block_only",
        "valid_from": rule["start"].isoformat() if rule and rule["start"] else None,
        "valid_until": rule["end"].isoformat() if rule and rule["end"] else None,
    }
    if rule and rule["date_issue"]:
        result["status"] = rule["date_issue"]
    elif at_date is None:
        result["status"] = "travel_date_missing"
    elif rule and rule["start"] and rule["end"]:
        result["status"] = (
            "within_textual_range"
            if rule["start"] <= at_date <= rule["end"]
            else "outside_textual_range"
        )
    return result


def _has_refutation(text, blocks, subject):
    refutation = re.search(
        r"(?:上句|下句|上述|以下|该(?:预约)?要求).{0,16}"
        r"(?:不实|谣言|误传|错误|已废止|已取消|已失效|历史政策|不再适用)",
        text,
    )
    named_refutation = any(
        subject in block and re.search(r"辟谣|谣言|误传|历史政策|已废止|已取消", block)
        for _, _, block in blocks
    )
    return bool(refutation or named_refutation)


def extract_reservation_rules(source: EvidenceText, subject: str, at_date=None, *, limit=20):
    """Extract declared rules with the single-claim grammar; never certify source truth."""
    result = {
        "rules": [],
        "limit_reached": False,
        "uninterpreted_reservation_blocks": 0,
        "eligible_body": source.kind in ("provider_markdown", "user_text") and bool(source.text),
    }
    if not result["eligible_body"]:
        return result
    blocks = list(_blocks(source.text))
    usable = not _has_refutation(source.text, blocks, subject)
    seen = {}
    for start, end, block in blocks:
        rule = _parse_rule(block, subject)
        if rule is None:
            result["uninterpreted_reservation_blocks"] += int("预约" in block)
            continue
        dates = _date_report(rule, at_date)
        key = (rule["value"], dates["valid_from"], dates["valid_until"], rule["date_issue"])
        if key in seen:
            seen[key]["occurrences"] += 1
            continue
        if len(seen) >= limit:
            result["limit_reached"] = True
            break
        item = {
            "value": rule["value"],
            "date": dates,
            "context_usable": usable,
            "context_span": {"start": start, "end": end},
            "context_sha256": digest(block),
            "occurrences": 1,
        }
        result["rules"].append(item)
        seen[key] = item
    return result


def review_claim(claim: FactClaim, evidence, *, at_date=None, expected_subject=None, today=None):
    today = today or date.today()
    source = next((item for item in evidence if item.source_id == claim.source_id), None)
    content_hash = digest(source.text) if source and source.text is not None else None
    report = {
        "claim_sha256": digest(claim.model_dump_json()),
        "review_sha256": digest(
            json.dumps(
                {
                    "claim": claim.model_dump(mode="json"),
                    "content_sha256": content_hash,
                    "kind": source.kind if source else None,
                    "url": source.url if source else None,
                    "at_date": at_date.isoformat() if at_date else None,
                    "today": today.isoformat(),
                    "expected_subject": expected_subject,
                    "rule_version": RULE_VERSION,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        ),
        "source_id": claim.source_id,
        "source_found": source is not None,
        "rule_version": RULE_VERSION,
        "status": "needs_review",
        "subject_binding": "not_bound_to_place"
        if expected_subject is None
        else "matched_name_only"
        if claim.subject == expected_subject
        else "needs_entity_resolution",
        "quote_check": {"status": "unknown_source", "span": None},
        "identity": {"status": "unverified", "content_origin_verified": False},
        "semantics": {"status": "insufficient", "reason": "unknown_source"},
        "date": _date_report(None, at_date),
        "facts_verified": False,
    }
    if source is None:
        report["status"] = "rejected_evidence"
        return report
    report["identity"] = assess_identity(source, today)
    report["evidence_kind"] = source.kind
    if source.text is None:
        report.update(status="rejected_evidence", quote_check={"status": "link_only", "span": None})
        report["semantics"]["reason"] = "link_only"
        return report
    report["content_sha256"] = content_hash
    match = locate_quote(source.text, claim.quote, claim.start_offset)
    report["quote_check"] = match
    if match["status"] != "matched":
        report["status"] = "rejected_evidence"
        report["semantics"]["reason"] = "quote_not_uniquely_located"
        return report
    if source.kind == "search_snippet":
        report["semantics"]["reason"] = "snippet_cannot_replace_body"
        return report
    if source.kind not in ("provider_markdown", "user_text"):
        report["semantics"]["reason"] = "unsupported_evidence_kind"
        return report
    blocks = list(_blocks(source.text))
    span = match["span"]
    selected = next(
        (
            (start, end, text)
            for start, end, text in blocks
            if start <= span["start"] and span["end"] <= end
        ),
        None,
    )
    if selected is None:
        report["semantics"]["reason"] = "quote_crosses_source_blocks"
        return report
    start, end, block = selected
    report["semantics"].update(
        context_span={"start": start, "end": end}, context_sha256=digest(block)
    )
    rule = _parse_rule(block, claim.subject)
    report["date"] = _date_report(rule, at_date)
    if rule is None:
        report["semantics"]["reason"] = "outside_supported_whole_block_grammar"
        return report
    if report["date"]["status"] == "outside_textual_range":
        report["status"] = "rejected_evidence"
    # Cross-paragraph refutations/history and conflicting explicit rules cannot be ignored.
    if _has_refutation(source.text, blocks, claim.subject):
        report["semantics"]["reason"] = "refutation_or_history_requires_review"
        return report
    for _, _, other_block in blocks:
        other = _parse_rule(other_block, claim.subject)
        if other and other["value"] != rule["value"]:
            if at_date and other["start"] and other["end"]:
                if not other["start"] <= at_date <= other["end"]:
                    continue
            report["semantics"]["reason"] = "conflicting_source_rules"
            return report
    relation = "supported" if rule["value"] == claim.value else "contradicted"
    report["semantics"].update(
        status=relation,
        reason="whole_block_reservation_rule",
        scope="reservation_policy_text_only",
        broader_context="not_exhaustively_interpreted",
    )
    if relation == "contradicted" or report["date"]["status"] == "outside_textual_range":
        report["status"] = "rejected_evidence"
    return report


def summarize_reviews(reviews):
    return {
        "rule_version": RULE_VERSION,
        "total": len(reviews),
        "needs_review": sum(item["status"] == "needs_review" for item in reviews),
        "rejected_evidence": sum(item["status"] == "rejected_evidence" for item in reviews),
        "verified_facts": 0,
        "additional_network_calls": 0,
        "records": [
            {
                key: item.get(key)
                for key in (
                    "claim_sha256",
                    "review_sha256",
                    "content_sha256",
                    "status",
                )
            }
            | {"source_id": item["source_id"] if item["source_found"] else None}
            for item in reviews
        ],
    }


class ClaimInspection(Contract):
    references: Annotated[tuple[ReferenceInput, ...], Field(min_length=1, max_length=5)]
    claims: Annotated[tuple[FactClaim, ...], Field(min_length=1, max_length=15)]
    at_date: date | None = None


def inspect_claims(data: ClaimInspection):
    # The comparison module reuses this module's grammar and contracts.
    from .evidence_compare import compare_claim_evidence, summarize_comparisons

    documents = tuple(import_reference(item) for item in data.references)
    if len({doc.source_id for doc in documents}) != len(documents):
        raise ValueError("duplicate_reference")
    evidence = tuple(
        EvidenceText(doc.source_id, doc.material.text, doc.acquisition, doc.material.url)
        for doc in documents
    )
    reviews = [review_claim(claim, evidence, at_date=data.at_date) for claim in data.claims]
    comparisons = compare_claim_evidence(data.claims, evidence, at_date=data.at_date)
    return {
        "reviews": reviews,
        "summary": summarize_reviews(reviews),
        "comparisons": comparisons,
        "cross_source_summary": summarize_comparisons(comparisons),
        "network_calls": 0,
    }
