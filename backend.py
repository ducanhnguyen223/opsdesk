"""Offline business workflow. No network, real customer data or model calls."""
import hashlib
import json
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from retrieval import authorized_documents, instant, policy_citations, rank_chunks, validate_citations

FIXTURE = Path(__file__).parent / "fixtures/baseline.json"
ACTIONS = {"notify_customer", "contact_carrier", "escalate_manager",
           "hold_for_manager", "notify_account_owner"}


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fingerprint(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


class Problem(Exception):
    def __init__(self, status, detail):
        self.status, self.detail = status, detail


class Store:
    def __init__(self, path):
        self.path = str(path)
        with self.connect(write=True) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS records (
                kind TEXT NOT NULL, id TEXT NOT NULL, tenant TEXT NOT NULL,
                body TEXT NOT NULL, PRIMARY KEY(kind,id))""")
            db.execute("""CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY, actor_id TEXT NOT NULL,
                auth_revision INTEGER NOT NULL, expires REAL NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS analyses (
                id TEXT PRIMARY KEY, tenant TEXT NOT NULL, actor_id TEXT NOT NULL,
                body TEXT NOT NULL, evidence TEXT NOT NULL, shipment_id TEXT)""")
            db.execute("""CREATE TABLE IF NOT EXISTS tickets (
                id TEXT PRIMARY KEY, tenant TEXT NOT NULL, analysis_id TEXT NOT NULL,
                revision INTEGER NOT NULL, body TEXT NOT NULL,
                UNIQUE(tenant,analysis_id,revision),
                FOREIGN KEY(analysis_id) REFERENCES analyses(id))""")
            db.execute("""CREATE TABLE IF NOT EXISTS approvals (
                tenant TEXT NOT NULL, key TEXT NOT NULL, payload_hash TEXT NOT NULL,
                ticket_id TEXT NOT NULL REFERENCES tickets(id), PRIMARY KEY(tenant,key))""")
            db.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)")
            db.execute("""CREATE TABLE IF NOT EXISTS document_versions (
                tenant TEXT NOT NULL, document_id TEXT NOT NULL, version INTEGER NOT NULL,
                body TEXT NOT NULL, updated_by TEXT NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY(tenant,document_id,version))""")
            if not db.execute("SELECT 1 FROM metadata WHERE key='fixture_version'").fetchone():
                data = json.loads(FIXTURE.read_text())
                for kind in ("actors", "shipments", "orders", "documents"):
                    for row in data[kind]:
                        row = dict(row)
                        if kind == "actors":
                            row.update(auth_revision=1, active=True)
                        db.execute("INSERT INTO records VALUES (?,?,?,?)",
                                   (kind, row["id"], row["tenant_id"], encode(row)))
                db.execute("INSERT INTO metadata VALUES ('fixture_version',?)", (str(data["version"]),))

    @contextmanager
    def connect(self, write=False):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            # ponytail: SQLite serializes writers; use PostgreSQL for multi-host deployment.
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


def record(db, kind, resource_id, tenant):
    row = db.execute("SELECT body FROM records WHERE kind=? AND id=? AND tenant=?",
                     (kind, resource_id, tenant)).fetchone()
    if row is None:
        raise Problem(404, "Resource not found")
    return json.loads(row["body"])


def scoped_rows(db, kind, tenant):
    return [json.loads(r["body"]) for r in db.execute(
        "SELECT body FROM records WHERE kind=? AND tenant=? ORDER BY id", (kind, tenant))]


def bound_actor(db, actor_id):
    """Resolve the operator identity fixed by a trusted local process boundary."""
    row = db.execute("SELECT body FROM records WHERE kind='actors' AND id=?", (actor_id,)).fetchone()
    actor = json.loads(row["body"]) if row else None
    if not actor or not actor["active"]:
        raise Problem(401, "Configured actor is missing or inactive")
    return actor


def authenticate(db, token):
    row = db.execute("""SELECT r.body,s.auth_revision FROM sessions s
        JOIN records r ON r.kind='actors' AND r.id=s.actor_id
        WHERE s.token_hash=? AND s.expires>?""", (fingerprint(token), time.time())).fetchone()
    if row:
        actor = json.loads(row["body"])
        if actor["active"] and actor["auth_revision"] == row["auth_revision"]:
            return actor
    raise Problem(401, "Session missing, expired or revoked")


def login(store, actor_id, old_token=None):
    token = secrets.token_urlsafe(32)
    with store.connect(write=True) as db:
        row = db.execute("SELECT body FROM records WHERE kind='actors' AND id=?", (actor_id,)).fetchone()
        if not row or not (actor := json.loads(row["body"]))["active"]:
            raise Problem(401, "Invalid demo actor")
        db.execute("DELETE FROM sessions WHERE expires<=? OR token_hash=?",
                   (time.time(), fingerprint(old_token)))
        db.execute("INSERT INTO sessions VALUES (?,?,?,?)",
                   (fingerprint(token), actor_id, actor["auth_revision"], time.time() + 3600))
    return token


def evidence(db, actor, shipment_id, now):
    shipment = record(db, "shipments", shipment_id, actor["tenant_id"])
    orders = [o for o in scoped_rows(db, "orders", actor["tenant_id"])
              if o["shipment_id"] == shipment_id]
    documents = authorized_documents(scoped_rows(db, "documents", actor["tenant_id"]),
        tenant_id=actor["tenant_id"], role=actor["role"], policy_scope=shipment["policy_scope"], as_of=now)
    return {"shipment": shipment, "orders": orders, "documents": documents, "actor": actor}


def fake_provider(message, context):
    """Deterministic adapter, NOT an LLM or general natural-language understanding."""
    procedures = [d for d in context["documents"] if d["kind"] == "procedure"]
    return {"action": procedures[0]["action"], "citation_ids": [d["id"] for d in procedures]}


def analyse(store, token, message, provider, clock):
    result = dict(analysis_id=str(uuid4()), revision=1, status="needs_clarification",
                  created_at=datetime.now(timezone.utc).isoformat(),
                  clarification_questions=[], verified_facts=[], affected_orders=[],
                  recommended_actions=[], citations=[], draft_message=None,
                  warnings=["Model-assisted analysis; review before approval." if getattr(provider, "mode", None) == "remote-model"
                            else "Offline deterministic demo; not a live AI response."], trace_id=str(uuid4()))
    shipment_id, context = None, {}
    with store.connect() as db:
        actor = authenticate(db, token)
        ids = sorted(set(re.findall(r"\bSHP-\d+\b", message.upper())))
        if len(ids) != 1:
            result["clarification_questions"] = ["Vui lòng cung cấp đúng một mã lô hàng."]
        else:
            shipment_id = ids[0]
            context = evidence(db, actor, shipment_id, clock())
    if context:
        shipment = context["shipment"]
        result["verified_facts"] = [dict(field=key, value=shipment[key], source_type="shipment",
            source_id=shipment_id, source_revision=shipment["revision"])
            for key in ("id", "status", "promised_at", "estimated_at")]
        if shipment["status"] != "delayed" or re.search(
                r"không\s+(?:bị\s+)?chậm|chưa\s+(?:bị\s+)?chậm|đã giao|not delayed|delivered|on time",
                message, re.IGNORECASE):
            result["clarification_questions"] = ["Thông báo cần được đối chiếu với trạng thái đã xác minh; vui lòng xác nhận tình huống giao chậm."]
        else:
            result["affected_orders"] = [o for o in context["orders"] if o["status"] == "active"]
            procedures = [d for d in context["documents"] if d["kind"] == "procedure"]
            result["citations"] = policy_citations(message, context["documents"])
            validate_citations(result["citations"], context["documents"])
            result["status"] = "insufficient_evidence"
            if (not procedures or len({d["action"] for d in procedures}) != 1 or not result["affected_orders"]
                    or {c["document_id"] for c in result["citations"]} != {d["id"] for d in procedures}):
                result["warnings"].append("Missing or conflicting applicable procedures/orders; manual review required.")
            else:
                try:
                    # Pass a detached snapshot: provider code cannot mutate trusted evidence.
                    provider_context = json.loads(encode(context))
                    provider_context["retrieved_chunks"] = rank_chunks(message, context["documents"])[:12]
                    choice = provider(message, provider_context)
                    allowed_ids = {d["id"] for d in procedures}
                    if (not isinstance(choice, dict) or set(choice) not in (
                            {"action", "citation_ids"}, {"action", "citation_ids", "draft_message"})
                            or choice["action"] not in ACTIONS
                            or choice["action"] != procedures[0]["action"]
                            or not isinstance(choice["citation_ids"], list)
                            or not all(isinstance(v, str) for v in choice["citation_ids"])
                            or set(choice["citation_ids"]) != allowed_ids):
                        raise ValueError("Ungrounded provider output")
                    draft = choice.get("draft_message")
                    if "draft_message" in choice and (
                            not isinstance(draft, str) or not 1 <= len(draft.strip()) <= 4000
                            or set(re.findall(r"\bSHP-\d+\b", draft.upper())) != {shipment_id}
                            or any(ord(c) < 32 and c not in "\n\t\r" for c in draft)):
                        raise ValueError("Invalid provider draft")
                    result["recommended_actions"] = [dict(action=choice["action"],
                        reason="Theo quy trình hiện hành đã trích dẫn.", citation_ids=sorted(allowed_ids))]
                    result["draft_message"] = draft.strip() if draft is not None else (f"Bản nháp để duyệt: Lô {shipment_id} đang giao chậm theo dữ liệu hệ thống. "
                        "Chúng tôi đang kiểm tra sự cố và sẽ cập nhật khi có thông tin được xác nhận.")
                    if draft is not None:
                        result["warnings"].append("AI draft wording is not fact-verified; check every statement and commitment before use.")
                    result["status"] = "ready_for_review"
                except (TimeoutError, ConnectionError):
                    result["status"] = "dependency_unavailable"
                    result["warnings"].append("Provider unavailable; no action authorized.")
                except (ValueError, TypeError, KeyError):
                    result["warnings"].append("Provider output rejected: unsupported action or citations.")
    with store.connect(write=True) as db:
        current_actor = authenticate(db, token)
        if current_actor != actor:
            raise Problem(409, "Authorization changed; analyse again")
        if context and fingerprint(evidence(db, actor, shipment_id, clock())) != fingerprint(context):
            raise Problem(409, "Evidence changed while analysing; try again")
        db.execute("INSERT INTO analyses VALUES (?,?,?,?,?,?)", (result["analysis_id"],
            actor["tenant_id"], actor["id"], encode(result), encode(context), shipment_id))
    return result


def owned_analysis(db, actor, analysis_id):
    row = db.execute("SELECT * FROM analyses WHERE id=? AND tenant=? AND actor_id=?",
                     (analysis_id, actor["tenant_id"], actor["id"])).fetchone()
    if not row:
        raise Problem(404, "Resource not found")
    return row


def approve(store, token, analysis_id, payload, key, clock):
    with store.connect(write=True) as db:
        actor = authenticate(db, token)
        saved = owned_analysis(db, actor, analysis_id)
        if actor["role"] != "operator":
            raise Problem(403, "Operator role required")
        proposal = json.loads(saved["body"])
        payload_hash = fingerprint({"analysis_id": analysis_id, **payload})
        previous = db.execute("SELECT * FROM approvals WHERE tenant=? AND key=?",
                              (actor["tenant_id"], key)).fetchone()
        if previous and previous["payload_hash"] != payload_hash:
            raise Problem(409, "Idempotency key already used with different payload")
        if proposal["status"] != "ready_for_review" or proposal["revision"] != payload["proposal_revision"]:
            raise Problem(409, "Proposal is not ready or revision changed")
        try:
            fresh = evidence(db, actor, saved["shipment_id"], clock())
        except Problem as exc:
            if exc.status == 404:
                raise Problem(409, "Evidence no longer available; analyse again") from exc
            raise
        if fingerprint(fresh) != fingerprint(json.loads(saved["evidence"])):
            raise Problem(409, "Evidence changed; analyse again")
        ticket_row = db.execute("SELECT body FROM tickets WHERE tenant=? AND analysis_id=? AND revision=?",
            (actor["tenant_id"], analysis_id, proposal["revision"])).fetchone()
        if ticket_row:
            ticket, status = json.loads(ticket_row["body"]), 200
        else:
            ticket = dict(ticket_id=str(uuid4()), tenant_id=actor["tenant_id"],
                analysis_id=analysis_id, proposal_revision=proposal["revision"],
                created_by=actor["id"], created_at=datetime.now(timezone.utc).isoformat(),
                status="open", actions=proposal["recommended_actions"])
            db.execute("INSERT INTO tickets VALUES (?,?,?,?,?)", (ticket["ticket_id"],
                actor["tenant_id"], analysis_id, proposal["revision"], encode(ticket)))
            status = 201
        if not previous:
            db.execute("INSERT INTO approvals VALUES (?,?,?,?)",
                       (actor["tenant_id"], key, payload_hash, ticket["ticket_id"]))
        return status, ticket
