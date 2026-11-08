"""Local read-only API. There are deliberately no booking or payment routes."""

from fastapi import FastAPI, HTTPException

from .budget import check_budget
from .models import IntakeInput, LedgerInput, TripRequest, ValidationReport
from .parsing import intake
from .references import EvidenceInspection, import_reference, inspection_report

app = FastAPI(title="travel-agent (M1 offline)", version="0.1.0")


@app.get("/health")
def health():
    return {"status": "ok", "stage": "M1", "live_providers": False}


@app.post("/v1/intake", response_model=TripRequest)
def parse_request(data: IntakeInput):
    try:
        return intake(data)
    except ValueError:
        # Do not echo the full user input or validation context into public responses.
        raise HTTPException(422, "输入无法确定或存在冲突，请检查预算、目的地及范围。") from None


@app.post("/v1/budget/check", response_model=ValidationReport)
def validate_budget(data: LedgerInput):
    return check_budget(data)


@app.post("/v1/references/inspect")
def inspect_references(data: EvidenceInspection):
    # Stateless and offline: no model use or snapshot saving, even if input flags opt in.
    documents = tuple(import_reference(item) for item in data.references)
    if len({doc.source_id for doc in documents}) != len(documents):
        raise HTTPException(422, "资料重复，请检查来源。")
    return inspection_report(documents, data.quotes)
