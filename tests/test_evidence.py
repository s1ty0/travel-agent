from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from travel_agent.models import EvidenceClaim, SourceRecord


def test_evidence_preserves_unknowns_and_timezone():
    claim = EvidenceClaim(claim="营业时间待核实", source_ids=())
    assert claim.status == "unverified"
    source = SourceRecord(
        source_id="user-1",
        locator="用户粘贴内容",
        content_kind="user_import",
        retrieved_at=datetime(2026, 11, 8, tzinfo=UTC),
    )
    assert source.model_dump(mode="json")["retrieved_at"].endswith("Z")
    with pytest.raises(ValidationError):
        EvidenceClaim(claim="营业时间已核实", source_ids=(), status="verified")
    with pytest.raises(ValidationError):
        SourceRecord(
            source_id="x", locator="x", content_kind="original", retrieved_at=datetime(2026, 11, 8)
        )
