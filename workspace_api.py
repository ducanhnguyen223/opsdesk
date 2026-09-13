"""Authenticated workspace reads and versioned procedure editing for the web UI."""
import json
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Literal

from backend import Problem, authenticate, encode, fingerprint, record, scoped_rows
from retrieval import instant, search_procedures
from pdf_extract import extract_pdf, MAX_BASE64


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class PdfInput(Model):
    content_base64: str = Field(min_length=1, max_length=MAX_BASE64)


class PdfPreview(Model):
    text: str
    page_count: int
    empty_pages: list[int]
    warnings: list[str]
    sha256: str


class Identity(Model):
    id: str
    tenant_id: str
    role: str
    mode: str


class DocumentInput(Model):
    title: str = Field(min_length=1, max_length=160)
    text: str = Field(min_length=1, max_length=10000)
    policy_scope: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    action: Literal["notify_customer", "contact_carrier", "escalate_manager", "hold_for_manager", "notify_account_owner"]
    allowed_roles: list[Literal["viewer", "operator"]] = Field(min_length=1, max_length=2)
    effective_from: str
    effective_to: str | None = None
    expected_version: int = Field(ge=0)

    @model_validator(mode="after")
    def valid_dates(self):
        start = instant(self.effective_from)
        end = instant(self.effective_to) if self.effective_to is not None else None
        if start.tzinfo is None or (end and (end.tzinfo is None or end <= start)):
            raise ValueError("Use timezone-aware dates with end later than start")
        if len(set(self.allowed_roles)) != len(self.allowed_roles):
            raise ValueError("Duplicate role")
        return self


class Document(Model):
    id: str
    title: str = ""
    text: str
    tenant_id: str
    version: int
    kind: str
    policy_scope: str
    action: str | None
    allowed_roles: list[str]
    effective_from: str
    effective_to: str | None


class DocumentPage(Model):
    items: list[Document]
    next_cursor: str | None


class SearchHit(Model):
    document_id: str
    version: int
    chunk_id: str
    quote: str
    start: int
    end: int
    kind: str
    score: float


class HistoryItem(Model):
    analysis_id: str
    status: str
    revision: int
    shipment_id: str | None
    created_at: str | None


class HistoryPage(Model):
    items: list[HistoryItem]
    next_cursor: int | None


class TicketItem(Model):
    ticket_id: str
    analysis_id: str
    created_at: str
    status: str


class TicketPage(Model):
    items: list[TicketItem]
    next_cursor: int | None


def check_snapshot_access(db, actor, saved):
    """Saved excerpts must not bypass a later document permission revocation."""
    context = json.loads(saved["evidence"])
    for old in context.get("documents", []):
        current = record(db, "documents", old["id"], actor["tenant_id"])
        if actor["role"] not in current["allowed_roles"]:
            raise Problem(404, "Resource not found")


def write_document(store, token, doc_id, payload):
    with store.connect(write=True) as db:
        actor = authenticate(db, token)
        if actor["role"] != "operator":
            raise Problem(403, "Operator role required")
        old = record(db, "documents", doc_id, actor["tenant_id"]) if doc_id else None
        if old and actor["role"] not in old["allowed_roles"]:
            raise Problem(404, "Resource not found")
        if payload["expected_version"] != (old["version"] if old else 0):
            raise Problem(409, "Document version changed; reload before editing")
        doc = {k: v for k, v in payload.items() if k != "expected_version"}
        doc.update(id=doc_id or "DOC-" + str(uuid4()), tenant_id=actor["tenant_id"],
                   version=old["version"] + 1 if old else 1, kind="procedure")
        now = datetime.now(timezone.utc).isoformat()
        if old:
            db.execute("INSERT OR IGNORE INTO document_versions VALUES (?,?,?,?,?,?)",
                (actor["tenant_id"], old["id"], old["version"], encode(old), "legacy-snapshot", now))
        db.execute("INSERT INTO document_versions VALUES (?,?,?,?,?,?)",
                   (actor["tenant_id"], doc["id"], doc["version"], encode(doc), actor["id"], now))
        if old:
            db.execute("UPDATE records SET body=? WHERE kind='documents' AND id=? AND tenant=?",
                       (encode(doc), doc["id"], actor["tenant_id"]))
        else:
            db.execute("INSERT INTO records VALUES ('documents',?,?,?)",
                       (doc["id"], actor["tenant_id"], encode(doc)))
        return doc


def workspace_router(store, clock, mode):
    router = APIRouter(prefix="/api")

    @router.post("/documents/extract-pdf", response_model=PdfPreview)
    def pdf_preview(payload: PdfInput, request: Request):
        with store.connect() as db:
            actor = authenticate(db, request.cookies.get("opsdesk_session"))
            if actor["role"] != "operator":
                raise Problem(403, "Operator role required")
        result = extract_pdf(payload.content_base64)
        with store.connect() as db:
            if authenticate(db, request.cookies.get("opsdesk_session")) != actor:
                raise Problem(409, "Authorization changed during extraction")
        return result

    @router.get("/me", response_model=Identity)
    def me(request: Request):
        with store.connect() as db:
            actor = authenticate(db, request.cookies.get("opsdesk_session"))
            return {k: actor[k] for k in ("id", "tenant_id", "role")} | {"mode": mode}

    @router.post("/logout")
    def logout(request: Request, response: Response) -> dict[str, bool]:
        with store.connect(write=True) as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?",
                       (fingerprint(request.cookies.get("opsdesk_session")),))
        response.delete_cookie("opsdesk_session")
        return {"logged_out": True}

    @router.get("/analyses", response_model=HistoryPage)
    def history(request: Request, limit: int = Query(20, ge=1, le=100), before: int | None = Query(None, ge=1)):
        with store.connect() as db:
            actor = authenticate(db, request.cookies.get("opsdesk_session"))
            rows = db.execute("""SELECT rowid AS cursor,* FROM analyses WHERE tenant=? AND actor_id=?
                AND (? IS NULL OR rowid<?) ORDER BY rowid DESC LIMIT ?""",
                (actor["tenant_id"], actor["id"], before, before, limit + 1)).fetchall()
            items = []
            for row in rows[:limit]:
                try:
                    check_snapshot_access(db, actor, row)
                except Problem as exc:
                    if exc.status == 404:
                        continue
                    raise
                body = json.loads(row["body"])
                items.append({k: body[k] for k in ("analysis_id", "status", "revision")} |
                             {"shipment_id": row["shipment_id"], "created_at": body.get("created_at")})
            return {"items": items, "next_cursor": rows[limit - 1]["cursor"] if len(rows) > limit else None}

    @router.get("/tickets", response_model=TicketPage)
    def tickets(request: Request, limit: int = Query(20, ge=1, le=100), before: int | None = Query(None, ge=1)):
        with store.connect() as db:
            actor = authenticate(db, request.cookies.get("opsdesk_session"))
            rows = db.execute("""SELECT rowid AS cursor,body FROM tickets WHERE tenant=?
                AND (? IS NULL OR rowid<?) ORDER BY rowid DESC LIMIT ?""",
                (actor["tenant_id"], before, before, limit + 1)).fetchall()
            items = [{k: json.loads(row["body"])[k] for k in ("ticket_id", "analysis_id", "created_at", "status")}
                     for row in rows[:limit]]
            return {"items": items, "next_cursor": rows[limit - 1]["cursor"] if len(rows) > limit else None}

    @router.get("/documents", response_model=DocumentPage)
    def documents(request: Request, limit: int = Query(20, ge=1, le=100), after: str = ""):
        with store.connect() as db:
            actor = authenticate(db, request.cookies.get("opsdesk_session"))
            docs = [d for d in scoped_rows(db, "documents", actor["tenant_id"])
                    if actor["role"] in d["allowed_roles"] and d["id"] > after]
            return {"items": docs[:limit], "next_cursor": docs[limit - 1]["id"] if len(docs) > limit else None}

    @router.get("/documents/search", response_model=list[SearchHit])
    def search(request: Request, q: str = Query(min_length=1, max_length=8000),
               shipment_id: str = Query(min_length=1), limit: int = Query(6, ge=1, le=20)):
        with store.connect() as db:
            actor = authenticate(db, request.cookies.get("opsdesk_session"))
            shipment = record(db, "shipments", shipment_id, actor["tenant_id"])
            if not q.strip():
                raise Problem(422, "Search text is required")
            return search_procedures(q, scoped_rows(db, "documents", actor["tenant_id"]),
                tenant_id=actor["tenant_id"], role=actor["role"], policy_scope=shipment["policy_scope"],
                as_of=clock(), limit=limit)

    @router.get("/documents/{doc_id}", response_model=Document)
    def document(doc_id: str, request: Request):
        with store.connect() as db:
            actor = authenticate(db, request.cookies.get("opsdesk_session"))
            doc = record(db, "documents", doc_id, actor["tenant_id"])
            if actor["role"] not in doc["allowed_roles"]:
                raise Problem(404, "Resource not found")
            return doc

    @router.post("/documents", response_model=Document, status_code=201)
    def create_document(payload: DocumentInput, request: Request):
        return write_document(store, request.cookies.get("opsdesk_session"), None, payload.model_dump())

    @router.put("/documents/{doc_id}", response_model=Document)
    def update_document(doc_id: str, payload: DocumentInput, request: Request):
        return write_document(store, request.cookies.get("opsdesk_session"), doc_id, payload.model_dump())

    @router.get("/documents/{doc_id}/versions", response_model=list[Document])
    def versions(doc_id: str, request: Request):
        with store.connect() as db:
            actor = authenticate(db, request.cookies.get("opsdesk_session"))
            current = record(db, "documents", doc_id, actor["tenant_id"])
            if actor["role"] not in current["allowed_roles"]:
                raise Problem(404, "Resource not found")
            rows = db.execute("SELECT body FROM document_versions WHERE tenant=? AND document_id=? ORDER BY version DESC",
                              (actor["tenant_id"], doc_id)).fetchall()
            docs = [json.loads(row["body"]) for row in rows] or [current]
            return [d for d in docs if actor["role"] in d["allowed_roles"]]

    return router
