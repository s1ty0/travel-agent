"""Private provenance index, without query text, snippets, keys, or provider response bodies."""

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from .source_models import PlaceHit, SearchHit, SourceBatch


def save_provenance(batch: SourceBatch, path: Path) -> None:
    metadata = batch.model_dump(mode="json", exclude={"hits"})
    sources = []
    for hit in batch.hits:
        item = {
            "source_id": hit.source_id,
            "evidence_kind": hit.evidence_kind,
            "record_sha256": hashlib.sha256(hit.model_dump_json().encode()).hexdigest(),
        }
        if isinstance(hit, SearchHit):
            item["locator"] = hit.url
        elif isinstance(hit, PlaceHit):
            item["locator"] = batch.provider + ":poi:" + hit.poi_id
        else:
            item["locator"] = hit.source_id
        sources.append(item)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("""CREATE TABLE IF NOT EXISTS source_runs (
            run_id TEXT PRIMARY KEY, metadata_json TEXT NOT NULL, sources_json TEXT NOT NULL)""")
        # Replaying an already saved run cannot overwrite its provenance.
        db.execute(
            "INSERT INTO source_runs VALUES (?, ?, ?)",
            (
                batch.run_id,
                json.dumps(metadata, ensure_ascii=False),
                json.dumps(sources, ensure_ascii=False),
            ),
        )
