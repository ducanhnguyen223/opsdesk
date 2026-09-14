"""Local-only demo API. Start with python app.py; never expose demo login publicly."""
import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from backend import (Store, Problem, analyse, approve, authenticate, evidence,
                     fake_provider, login, owned_analysis, record)
import json
from workspace_api import workspace_router, check_snapshot_access
from cases_api import cases_router, seed_cases


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class LoginRequest(StrictModel):
    actor_id: str = Field(min_length=1, max_length=64)


class AnalysisRequest(StrictModel):
    message: str = Field(min_length=1, max_length=8000)


class ApprovalRequest(StrictModel):
    proposal_revision: int = Field(ge=1)
    confirmed: bool

    @field_validator("confirmed")
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError("Explicit confirmation required")
        return value


class Order(StrictModel):
    id: str
    tenant_id: str
    shipment_id: str
    customer_id: str
    status: str
    revision: int


class Shipment(StrictModel):
    id: str
    tenant_id: str
    customer_id: str
    policy_scope: str
    status: str
    revision: int
    promised_at: str
    estimated_at: str


class Fact(StrictModel):
    field: str
    value: str | int
    source_type: str
    source_id: str
    source_revision: int


class Citation(StrictModel):
    document_id: str
    version: int
    chunk_id: str
    quote: str


class Action(StrictModel):
    action: Literal["notify_customer", "contact_carrier", "escalate_manager", "hold_for_manager", "notify_account_owner"]
    reason: str
    citation_ids: list[str]


class Analysis(StrictModel):
    analysis_id: str
    created_at: str | None = None
    revision: int
    status: Literal["needs_clarification", "insufficient_evidence", "ready_for_review", "dependency_unavailable"]
    clarification_questions: list[str]
    verified_facts: list[Fact]
    affected_orders: list[Order]
    recommended_actions: list[Action]
    citations: list[Citation]
    draft_message: str | None
    warnings: list[str]
    trace_id: str


class Ticket(StrictModel):
    ticket_id: str
    tenant_id: str
    analysis_id: str
    proposal_revision: int
    created_by: str
    created_at: str
    status: str
    actions: list[Action]


def create_app(db_path, *, demo_login=False, provider=fake_provider, clock=None):
    store = Store(db_path)
    clock = clock or (lambda: datetime.now(timezone.utc))
    if demo_login:
        seed_cases(store, clock())
    api = FastAPI(title="OpsDesk — offline demo", version="0.2.0")
    api.state.store = store
    web_dir = Path(__file__).parent / "web"
    if web_dir.is_dir():
        api.mount("/static", StaticFiles(directory=web_dir), name="static")

    @api.get("/", include_in_schema=False)
    def index():
        if (web_dir / "index.html").is_file():
            return FileResponse(web_dir / "index.html")
        return {"name": "OpsDesk API", "health": "/health", "docs": "/docs"}
    mode = "openai" if getattr(provider, "mode", None) == "openai" else "offline-deterministic"
    api.include_router(workspace_router(store, clock, mode))
    api.include_router(cases_router(store, clock))
    api.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"])

    @api.middleware("http")
    async def local_boundary(request, call_next):
        if request.client is None or request.client.host not in {"127.0.0.1", "::1", "testclient"}:
            return JSONResponse({"detail": "Local demo only"}, status_code=403)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and origin != str(request.base_url).rstrip("/"):
                return JSONResponse({"detail": "Cross-origin writes forbidden"}, status_code=403)
            if request.headers.get("content-type", "").split(";")[0] != "application/json":
                return JSONResponse({"detail": "JSON request required"}, status_code=415)
            # Bound streamed bodies too; do not trust Content-Length alone.
            max_body = 4 * 1024 * 1024 + 256 if request.url.path == "/api/documents/extract-pdf" else 40000
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > max_body:
                    return JSONResponse({"detail": "Request too large"}, status_code=413)
            request._body = bytes(body)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        if request.url.path == "/" or request.url.path.startswith("/static/"):
            response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        return response

    @api.exception_handler(Problem)
    async def problem_handler(request, exc):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status)

    @api.exception_handler(sqlite3.OperationalError)
    async def unavailable_handler(request, exc):
        return JSONResponse({"detail": "Database unavailable; retry later"}, status_code=503)

    @api.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "mode": mode}

    @api.get("/api/usage")
    def usage(request: Request):
        with store.connect() as db:
            actor = authenticate(db, request.cookies.get("opsdesk_session"))
        ledger = getattr(provider, "ledger", None)
        summary = ledger.summary(actor) if ledger else None
        with store.connect() as db:
            if authenticate(db, request.cookies.get("opsdesk_session")) != actor:
                raise Problem(409, "Authorization changed; reload usage")
        return {"mode": mode, "summary": summary}

    @api.post("/api/demo/login")
    def demo(payload: LoginRequest, request: Request, response: Response) -> dict[str, str]:
        if not demo_login:
            raise HTTPException(404, "Demo login disabled")
        token = login(store, payload.actor_id, request.cookies.get("opsdesk_session"))
        response.set_cookie("opsdesk_session", token, httponly=True, samesite="strict", max_age=3600)
        return {"mode": "offline-demo", "actor_id": payload.actor_id}

    @api.post("/api/analyses", response_model=Analysis)
    def analysis(payload: AnalysisRequest, request: Request):
        return analyse(store, request.cookies.get("opsdesk_session"), payload.message, provider, clock)

    @api.get("/api/shipments/{shipment_id}", response_model=Shipment)
    def shipment(shipment_id: str, request: Request):
        with store.connect() as db:
            actor = authenticate(db, request.cookies.get("opsdesk_session"))
            return record(db, "shipments", shipment_id, actor["tenant_id"])

    @api.get("/api/shipments/{shipment_id}/orders", response_model=list[Order])
    def orders(shipment_id: str, request: Request):
        with store.connect() as db:
            actor = authenticate(db, request.cookies.get("opsdesk_session"))
            context = evidence(db, actor, shipment_id, clock())
            return [o for o in context["orders"] if o["status"] == "active"
                    and context["shipment"]["status"] == "delayed"]

    @api.get("/api/analyses/{analysis_id}", response_model=Analysis)
    def saved_analysis(analysis_id: str, request: Request):
        with store.connect() as db:
            actor = authenticate(db, request.cookies.get("opsdesk_session"))
            saved = owned_analysis(db, actor, analysis_id)
            check_snapshot_access(db, actor, saved)
            return json.loads(saved["body"])

    @api.post("/api/analyses/{analysis_id}/approve", response_model=Ticket)
    def approval(analysis_id: str, payload: ApprovalRequest, request: Request, response: Response):
        key = request.headers.get("idempotency-key", "")
        if not key or len(key) > 128 or not all(33 <= ord(c) <= 126 for c in key):
            raise HTTPException(422, "Idempotency-Key must be 1–128 visible ASCII characters")
        status, ticket = approve(store, request.cookies.get("opsdesk_session"), analysis_id,
                                 payload.model_dump(), key, clock)
        response.status_code = status
        return ticket

    @api.get("/api/tickets/{ticket_id}", response_model=Ticket)
    def ticket(ticket_id: str, request: Request):
        with store.connect() as db:
            actor = authenticate(db, request.cookies.get("opsdesk_session"))
            row = db.execute("SELECT body FROM tickets WHERE id=? AND tenant=?",
                             (ticket_id, actor["tenant_id"])).fetchone()
            if not row:
                raise Problem(404, "Resource not found")
            return json.loads(row["body"])

    return api


if __name__ == "__main__":
    import uvicorn
    parser = argparse.ArgumentParser(description="Local-only OpsDesk offline demo")
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--db", default=str(Path(__file__).parent / "opsdesk.sqlite3"))
    args = parser.parse_args()
    uvicorn.run(create_app(args.db, demo_login=True), host="127.0.0.1", port=args.port,
                proxy_headers=False, access_log=False)
