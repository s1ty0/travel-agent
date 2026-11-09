import json
from datetime import date

import pytest

from travel_agent.claim_review import EvidenceText, FactClaim
from travel_agent.evidence_compare import (
    MAX_RULES_PER_SOURCE,
    MAX_SOURCES,
    compare_claim_evidence,
    summarize_comparisons,
)

SUBJECT = "合成测试馆"
DAY = date(2026, 11, 10)


def claim(source_id="a", value=True, subject=SUBJECT):
    return FactClaim(
        subject=subject,
        predicate="reservation_required",
        value=value,
        source_id=source_id,
        quote="预约",
    )


def source(source_id, value=True, *, period=None, text=None, url=None, kind="user_text"):
    statement = SUBJECT + ("须预约。" if value else "无需预约。")
    if period:
        statement = period + "，" + statement
    return EvidenceText(
        source_id,
        statement if text is None else text,
        kind,
        url or f"https://{source_id}.example/visit",
    )


def compare(evidence, *, at_date=DAY, claims=None, expected_subject=SUBJECT):
    return compare_claim_evidence(
        claims or (claim(),), evidence, at_date=at_date, expected_subject=expected_subject
    )[0]


@pytest.mark.smoke
def test_available_unselected_source_can_reveal_same_day_conflict():
    evidence = (
        source("a", period="2026-11-01至2026-11-30"),
        source("b", False, period="2026-11-01至2026-11-30"),
    )
    result = compare(evidence)
    assert result["status"] == "conflict"
    assert result["sources_considered"] == result["active_rule_count"] == 2
    assert result["requires_review"] is True
    assert result["independent_confirmations"] == 0
    assert result["facts_verified"] is result["full_semantic_verification"] is False
    assert compare(evidence, claims=(claim("b", False),)) == result


@pytest.mark.regression
@pytest.mark.parametrize(
    ("first", "second", "at_date", "status"),
    [
        (None, None, DAY, "potential_conflict"),
        ("2026-11-01至2026-11-30", None, DAY, "potential_conflict"),
        ("2026-11-01至2026-11-30", "2026-11-01至2026-11-30", DAY, "conflict"),
        ("2026-11-01至2026-11-30", "2026-11-01至2026-11-30", None, "potential_conflict"),
        ("2026-10-01至2026-10-31", "2026-11-01至2026-11-30", DAY, "no_detected_conflict"),
        ("2026-10-01至2026-10-31", "2026-11-01至2026-11-30", None, "different_textual_periods"),
        ("2026-10-01至2026-10-31", "2026-10-01至2026-10-31", DAY, "insufficient"),
        ("2026-11-30至2026-11-01", "2026-11-01至2026-11-30", DAY, "no_detected_conflict"),
    ],
)
def test_dates_distinguish_current_potential_and_different_periods(first, second, at_date, status):
    result = compare(
        (source("a", period=first), source("b", False, period=second)), at_date=at_date
    )
    assert result["status"] == status
    assert result["facts_verified"] is False


def test_repetition_and_declared_origin_form_candidate_groups_not_extra_confirmations():
    evidence = (
        source("a", url="https://first.example/a"),
        source("b", url="https://second.example/b"),
        source("c", text=SUBJECT + "需要预约。", url="https://second.example/c"),
    )
    result = compare(evidence)
    assert len(result["candidate_clusters"]) == 1
    assert result["duplicate_text_versions"] == 1
    assert result["unique_text_count"] == 2
    assert set(result["candidate_clusters"][0]["relations"]) == {
        "same_normalized_text",
        "same_declared_host",
    }
    assert result["independence_status"] == "not_established"


def test_same_url_revisions_still_show_conflict_instead_of_overwriting_a_version():
    url = "https://same.example/page"
    evidence = (source("a", url=url), source("b", False, url=url))
    result = compare(evidence)
    assert result["status"] == "potential_conflict"
    assert len(result["source_reports"]) == 2
    assert "same_declared_url" in result["candidate_clusters"][0]["relations"]
    assert len(result["candidate_clusters"]) == 1


def test_majority_of_duplicate_rules_does_not_resolve_an_opposing_rule():
    evidence = tuple(source(f"a{i}") for i in range(5)) + (source("b", False),)
    result = compare(evidence)
    assert result["status"] == "potential_conflict"
    assert result["duplicate_text_versions"] == 4
    assert result["independent_confirmations"] == 0


@pytest.mark.parametrize("kind", ["search_snippet", "link_only", "other"])
def test_non_body_sources_do_not_satisfy_comparison(kind):
    item = EvidenceText("a", None, "link_only") if kind == "link_only" else source("a", kind=kind)
    result = compare((item,))
    assert result["status"] == "insufficient"
    assert result["source_reports"][0]["eligible_body"] is False


def test_refuted_or_qualified_text_is_not_a_decisive_rule():
    for text in (SUBJECT + "须预约，节假日另行通知。", SUBJECT + "须预约。\n\n上述是谣言。"):
        result = compare((source("a", text=text),))
        assert result["status"] == "insufficient"
        assert result["active_rule_count"] == 0


def test_source_and_rule_limits_cannot_report_complete_agreement():
    result = compare(tuple(source(str(i)) for i in range(MAX_SOURCES + 1)))
    assert result["sources_considered"] == MAX_SOURCES
    assert result["status"] == "incomplete" and result["limit_reached"]
    text = (
        "\n\n".join(
            f"2026-01-{i:02d}至2026-01-{i:02d}，{SUBJECT}须预约。"
            for i in range(1, MAX_RULES_PER_SOURCE + 1)
        )
        + "\n\n"
        + SUBJECT
        + "无需预约。"
    )
    limited = compare((source("a", text=text),), at_date=None)
    assert limited["status"] == "incomplete"
    assert len(limited["source_reports"][0]["rules"]) == MAX_RULES_PER_SOURCE
    assert limited["complete_within_bounds"] is False


def test_identical_entries_collapse_but_reused_identifier_with_different_content_is_visible():
    a = source("a")
    single = compare((a,))
    assert compare((a, a)) == single
    reused = compare((a, source("a", text=SUBJECT + "需要预约。")))
    assert reused["source_id_collision"] and reused["status"] == "incomplete"
    assert reused["source_versions_provided"] == 2
    summary = summarize_comparisons((reused,))
    assert summary["incomplete"] == 1


def test_repeated_statements_in_one_body_are_one_rule_with_occurrence_count():
    result = compare((source("a", text="\n\n".join([SUBJECT + "须预约。"] * 10)),))
    rules = result["source_reports"][0]["rules"]
    assert len(rules) == 1 and rules[0]["occurrences"] == 10
    assert result["active_rule_count"] == 1


def test_comparison_fingerprints_are_order_stable_and_bind_date_and_all_source_versions():
    evidence = (source("a"), source("b", False))
    result = compare(evidence)
    assert result == compare(tuple(reversed(evidence)))
    assert result == compare(evidence, claims=(claim(), claim("b", False)))
    assert result["comparison_sha256"] != compare(evidence, at_date=None)["comparison_sha256"]
    changed = (source("a"), source("b", text=SUBJECT + "需要预约。"))
    assert result["comparison_sha256"] != compare(changed)["comparison_sha256"]
    unresolved = compare(evidence, expected_subject="同名分馆")
    assert unresolved["subject_binding"] == "needs_entity_resolution"
    summary = summarize_comparisons((result,))
    assert SUBJECT not in json.dumps(summary, ensure_ascii=False)
    assert summary["verified_facts"] == summary["additional_network_calls"] == 0


def test_available_sources_are_compared_offline_and_snapshot_replay_is_stable(
    monkeypatch, tmp_path
):
    import httpx
    from fastapi.testclient import TestClient

    from travel_agent.api import app
    from travel_agent.references import (
        ReferenceInput,
        import_reference,
        load_snapshot,
        save_snapshot,
    )

    def forbidden(*args, **kwargs):
        pytest.fail("comparison must not call any provider")

    monkeypatch.setattr(httpx, "Client", forbidden)
    documents = tuple(
        import_reference(
            ReferenceInput(title=f"合成材料{i}", text=SUBJECT + suffix, allow_snapshot=True)
        )
        for i, suffix in enumerate(("须预约。", "无需预约。"))
    )
    snapshot_id = save_snapshot(documents, tmp_path)
    restored = load_snapshot(snapshot_id, tmp_path)
    client = TestClient(app)
    responses = []
    for bundle in (documents, restored):
        payload = {
            "references": [document.material.model_dump(mode="json") for document in bundle],
            "claims": [claim(bundle[0].source_id).model_dump(mode="json")],
            "at_date": DAY.isoformat(),
        }
        response = client.post("/v1/claims/inspect", json=payload)
        assert response.status_code == 200
        responses.append(response.json())
    assert responses[0]["comparisons"] == responses[1]["comparisons"]
    assert responses[0]["comparisons"][0]["status"] == "potential_conflict"
    assert (
        responses[0]["network_calls"] == responses[0]["cross_source_summary"]["verified_facts"] == 0
    )


def test_other_available_text_does_not_repair_a_fabricated_citation():
    from travel_agent.claim_review import ClaimInspection, inspect_claims
    from travel_agent.references import ReferenceInput

    material = ReferenceInput(title="合成正文", text=SUBJECT + "须预约。")
    result = inspect_claims(
        ClaimInspection(
            references=(material,),
            claims=(claim("not-read"),),
            at_date=DAY,
        )
    )
    assert result["summary"]["rejected_evidence"] == 1
    assert result["comparisons"][0]["status"] == "no_detected_conflict"
    assert result["summary"]["verified_facts"] == 0
