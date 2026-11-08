"""User-supplied reference text, exact span checks, and permission-bound offline snapshots."""

import hashlib
import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .models import Contract
from .sources import public_url


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ReferenceInput(Contract):
    title: Annotated[str, Field(min_length=1, max_length=200)]
    url: Annotated[str, Field(max_length=2048)] | None = None
    text: Annotated[str, Field(min_length=1, max_length=8000)] | None = None
    text_kind: Literal["user_text", "search_snippet"] = "user_text"
    valid_from: date | None = None
    valid_until: date | None = None
    allow_model_use: Annotated[bool, Field(strict=True)] = False
    allow_snapshot: Annotated[bool, Field(strict=True)] = False

    @model_validator(mode="after")
    def valid_reference(self):
        if not self.title.strip() or (self.text is not None and not self.text.strip()):
            raise ValueError("reference_text_empty")
        if self.text is None and self.url is None:
            raise ValueError("reference_needs_text_or_url")
        if self.url is not None and self.url != public_url(self.url):
            raise ValueError("reference_url_must_be_public_without_fragment")
        if self.valid_from and self.valid_until and self.valid_until < self.valid_from:
            raise ValueError("reference_date_range_reversed")
        return self


class ReferenceDocument(Contract):
    source_id: str
    content_sha256: str | None
    imported_at: datetime
    material: ReferenceInput

    @model_validator(mode="after")
    def validate_hashes(self):
        expected_id = "doc_" + digest(self.material.model_dump_json())
        expected_hash = digest(self.material.text) if self.material.text is not None else None
        if self.source_id != expected_id or self.content_sha256 != expected_hash:
            raise ValueError("reference_digest_mismatch")
        if self.imported_at.utcoffset() is None:
            raise ValueError("reference_timestamp_needs_timezone")
        return self

    @property
    def acquisition(self):
        return self.material.text_kind if self.material.text is not None else "link_only"

    def summary(self):
        return {
            "source_id": self.source_id,
            "title": self.material.title,
            "url": self.material.url,
            "content_sha256": self.content_sha256,
            "imported_at": self.imported_at.isoformat(),
            "acquisition": self.acquisition,
            "valid_from": self.material.valid_from.isoformat()
            if self.material.valid_from
            else None,
            "valid_until": self.material.valid_until.isoformat()
            if self.material.valid_until
            else None,
            "facts_verified": False,
        }


def import_reference(material: ReferenceInput) -> ReferenceDocument:
    return ReferenceDocument(
        source_id="doc_" + digest(material.model_dump_json()),
        content_sha256=digest(material.text) if material.text is not None else None,
        imported_at=datetime.now(UTC),
        material=material,
    )


class QuoteRequest(Contract):
    source_id: Annotated[str, Field(min_length=1, max_length=200)]
    quote: Annotated[str, Field(min_length=2, max_length=300)]
    at_date: date | None = None
    start_offset: Annotated[int, Field(strict=True, ge=0)] | None = None

    @model_validator(mode="after")
    def not_whitespace(self):
        if not self.quote.strip():
            raise ValueError("quote_empty")
        return self


def check_quote(documents: tuple[ReferenceDocument, ...], request: QuoteRequest):
    """Match exact text, not semantics. Offsets are Unicode code points, end-exclusive."""
    doc = next((d for d in documents if d.source_id == request.source_id), None)
    report = {
        "source_id": request.source_id,
        "status": "unknown_source",
        "span": None,
        "date_applicability": "unknown",
        "facts_verified": False,
    }
    if doc is None:
        return report
    report["content_sha256"] = doc.content_sha256
    text = doc.material.text
    if text is None:
        report["status"] = "link_only"
        return report
    if request.start_offset is not None:
        matches = (
            [request.start_offset] if text.startswith(request.quote, request.start_offset) else []
        )
    else:
        matches = []
        cursor = 0
        while (index := text.find(request.quote, cursor)) >= 0:
            matches.append(index)
            cursor = index + 1
    report["status"] = "missing" if not matches else "ambiguous" if len(matches) > 1 else "matched"
    if len(matches) == 1:
        start = matches[0]
        report["span"] = {
            "start": start,
            "end": start + len(request.quote),
            "unit": "unicode_code_points",
            "end_exclusive": True,
            "line": text.count("\n", 0, start) + 1,
        }
    if request.at_date is not None:
        start, end = doc.material.valid_from, doc.material.valid_until
        if (start and request.at_date < start) or (end and request.at_date > end):
            report["date_applicability"] = "outside_declared_range"
        elif start and end:
            report["date_applicability"] = "within_declared_range"
    report["evidence_kind"] = doc.acquisition
    return report


class EvidenceInspection(Contract):
    references: Annotated[tuple[ReferenceInput, ...], Field(min_length=1, max_length=5)]
    quotes: Annotated[tuple[QuoteRequest, ...], Field(max_length=30)] = ()


class QuoteInspection(Contract):
    quotes: Annotated[tuple[QuoteRequest, ...], Field(max_length=30)] = ()


class Snapshot(Contract):
    schema_version: Literal[1] = 1
    documents: Annotated[tuple[ReferenceDocument, ...], Field(min_length=1, max_length=5)]

    @model_validator(mode="after")
    def content_permission(self):
        if any(not doc.material.allow_snapshot for doc in self.documents):
            raise ValueError("snapshot_permission_required")
        if len({doc.source_id for doc in self.documents}) != len(self.documents):
            raise ValueError("duplicate_reference")
        return self


def snapshot_path(root: Path, snapshot_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{64}", snapshot_id):
        raise ValueError("invalid_snapshot_id")
    resolved_root = root.resolve()
    path = resolved_root / (snapshot_id + ".json")
    if path.is_symlink() or path.resolve().parent != resolved_root:
        raise ValueError("invalid_snapshot_path")
    return path


def save_snapshot(documents: tuple[ReferenceDocument, ...], root: Path) -> str:
    body = Snapshot(documents=documents).model_dump_json()
    snapshot_id = digest(body)
    path = snapshot_path(root, snapshot_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    # No overwrite and no implicit merge of content/permissions.
    with path.open("x", encoding="utf-8", newline="\n") as output:
        output.write(body)
    return snapshot_id


def load_snapshot(snapshot_id: str, root: Path) -> tuple[ReferenceDocument, ...]:
    path = snapshot_path(root, snapshot_id)
    with path.open("rb") as source:
        raw = source.read(256 * 1024 + 1)
    if len(raw) > 256 * 1024:
        raise ValueError("snapshot_too_large")
    body = raw.decode("utf-8")
    if digest(body) != snapshot_id:
        raise ValueError("snapshot_integrity_failed")
    return Snapshot.model_validate_json(body).documents


def delete_snapshot(snapshot_id: str, root: Path):
    snapshot_path(root, snapshot_id).unlink()


def inspection_report(documents, requests):
    return {
        "references": [doc.summary() for doc in documents],
        "quotes": [check_quote(documents, request) for request in requests],
        "network_calls": 0,
        "facts_verified": False,
    }
