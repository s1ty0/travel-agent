"""Bounded comparison of available reservation evidence; no voting or factual certification."""

import json
from itertools import combinations
from urllib.parse import urlsplit

from .claim_review import RULE_VERSION, extract_reservation_rules
from .references import digest

COMPARISON_VERSION = "reservation-comparison-v1"
MAX_SOURCES = 32
MAX_RULES_PER_SOURCE = 20


def _hash(value):
    return digest(json.dumps(value, ensure_ascii=False, sort_keys=True))


def _source_keys(source):
    content = digest(source.text) if source.text is not None else None
    normalized = " ".join(source.text.split()) if source.text else ""
    locator, host = None, None
    if source.url:
        try:
            parts = urlsplit(source.url)
            if parts.scheme in ("https", "http") and parts.hostname:
                host = parts.hostname.lower().rstrip(".")
                # This groups declared links only; no DNS, fetching, or publisher authentication.
                locator = (parts.scheme, parts.netloc.lower(), parts.path, parts.query)
        except ValueError:
            pass
    return {
        "version": _hash((source.source_id, content, source.kind, source.url)),
        "content_sha256": content,
        "normalized_content": digest(normalized) if normalized else None,
        "locator": _hash(locator) if locator else None,
        "host": digest(host) if host else None,
    }


def _relations(first, second):
    return [
        label
        for key, label in (
            ("normalized_content", "same_normalized_text"),
            ("locator", "same_declared_url"),
            ("host", "same_declared_host"),
        )
        if first[key] is not None and first[key] == second[key]
    ]


def _clusters(entries):
    parents = list(range(len(entries)))

    def root(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    for left, right in combinations(range(len(entries)), 2):
        if _relations(entries[left][1], entries[right][1]):
            parents[root(right)] = root(left)
    groups = {}
    for index in range(len(entries)):
        groups.setdefault(root(index), []).append(index)
    result = []
    for indices in groups.values():
        versions = sorted(entries[index][1]["version"] for index in indices)
        result.append(
            {
                "cluster_sha256": _hash(versions),
                "source_versions": versions,
                "source_ids": sorted({entries[index][0].source_id for index in indices}),
                "relations": sorted(
                    {
                        relation
                        for left, right in combinations(indices, 2)
                        for relation in _relations(entries[left][1], entries[right][1])
                    }
                ),
            }
        )
    return sorted(result, key=lambda item: item["cluster_sha256"])


def _different_periods(first, second):
    a, b = first["date"], second["date"]
    return bool(
        a["valid_from"]
        and a["valid_until"]
        and b["valid_from"]
        and b["valid_until"]
        and a["status"] != "invalid_textual_range"
        and b["status"] != "invalid_textual_range"
        and (a["valid_until"] < b["valid_from"] or b["valid_until"] < a["valid_from"])
    )


def compare_claim_evidence(claims, evidence, *, at_date=None, expected_subject=None):
    """Compare all available bodies for each claimed subject, not just chosen citations."""
    if not claims:
        return []
    entries = {}
    source_versions = {}
    for source in evidence:
        keys = _source_keys(source)
        entries.setdefault(keys["version"], (source, keys))
        source_versions.setdefault(source.source_id, set()).add(keys["version"])
    all_entries = sorted(entries.values(), key=lambda item: item[1]["version"])
    bounded = all_entries[:MAX_SOURCES]
    identity_collision = any(len(versions) > 1 for versions in source_versions.values())
    clusters = _clusters(bounded)
    text_versions = [
        keys["normalized_content"] for _, keys in bounded if keys["normalized_content"]
    ]
    result = []
    for subject, predicate in sorted({(claim.subject, claim.predicate) for claim in claims}):
        limit_reached = len(all_entries) > MAX_SOURCES
        observations, source_reports = [], []
        for source, keys in bounded:
            extracted = extract_reservation_rules(
                source, subject, at_date, limit=MAX_RULES_PER_SOURCE
            )
            limit_reached |= extracted["limit_reached"]
            observations.extend(
                {
                    **rule,
                    "source_id": source.source_id,
                    "source_version": keys["version"],
                }
                for rule in extracted["rules"]
            )
            source_reports.append(
                {
                    "source_id": source.source_id,
                    "source_version": keys["version"],
                    "content_sha256": keys["content_sha256"],
                    "evidence_kind": source.kind,
                    **extracted,
                }
            )
        usable = [
            item
            for item in observations
            if item["context_usable"]
            and item["date"]["status"] not in ("invalid_textual_range", "outside_textual_range")
        ]
        opposing = [(a, b) for a, b in combinations(usable, 2) if a["value"] != b["value"]]
        current_conflict = any(
            a["date"]["status"] == b["date"]["status"] == "within_textual_range"
            for a, b in opposing
        )
        same_period_opposition = any(not _different_periods(a, b) for a, b in opposing)
        if current_conflict:
            status = "conflict"
        elif same_period_opposition:
            status = "potential_conflict"
        elif limit_reached or identity_collision:
            status = "incomplete"
        elif opposing:
            status = "different_textual_periods"
        elif usable:
            status = "no_detected_conflict"
        else:
            status = "insufficient"
        result.append(
            {
                "comparison_sha256": _hash(
                    {
                        "version": COMPARISON_VERSION,
                        "grammar": RULE_VERSION,
                        "subject": subject,
                        "predicate": predicate,
                        "at_date": at_date.isoformat() if at_date else None,
                        "expected_subject": expected_subject,
                        "sources": [keys["version"] for _, keys in all_entries],
                        "limits": (MAX_SOURCES, MAX_RULES_PER_SOURCE),
                    }
                ),
                "version": COMPARISON_VERSION,
                "grammar_version": RULE_VERSION,
                "subject": subject,
                "predicate": predicate,
                "at_date": at_date.isoformat() if at_date else None,
                "subject_binding": "not_bound_to_place"
                if expected_subject is None
                else "matched_name_only"
                if expected_subject == subject
                else "needs_entity_resolution",
                "status": status,
                "requires_review": True,
                "sources_considered": len(bounded),
                "source_versions_provided": len(all_entries),
                "candidate_clusters": clusters,
                "unique_text_count": len(set(text_versions)),
                "duplicate_text_versions": len(text_versions) - len(set(text_versions)),
                "source_reports": source_reports,
                "active_rule_count": len(usable),
                "out_of_range_rules": sum(
                    item["date"]["status"] == "outside_textual_range" for item in observations
                ),
                "limit_reached": limit_reached,
                "limits": {
                    "max_source_versions": MAX_SOURCES,
                    "max_rules_per_source": MAX_RULES_PER_SOURCE,
                },
                "source_id_collision": identity_collision,
                "complete_within_bounds": not limit_reached and not identity_collision,
                "full_semantic_verification": False,
                "independence_status": "not_established",
                "independent_confirmations": 0,
                "facts_verified": False,
            }
        )
    return result


def summarize_comparisons(comparisons):
    return {
        "version": COMPARISON_VERSION,
        "total": len(comparisons),
        "conflicts": sum(item["status"] == "conflict" for item in comparisons),
        "potential_conflicts": sum(item["status"] == "potential_conflict" for item in comparisons),
        "incomplete": sum(not item["complete_within_bounds"] for item in comparisons),
        "verified_facts": 0,
        "additional_network_calls": 0,
        "records": [
            {"comparison_sha256": item["comparison_sha256"], "status": item["status"]}
            for item in comparisons
        ],
    }
