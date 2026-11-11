"""Vercel FastAPI entry point with the authenticated web planning surface only."""

from fastapi import FastAPI

from src.travel_agent.web_api import router

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(router)


@app.get("/health")
def health():
    return {"status": "ok"}
