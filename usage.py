"""Durable request allowances, not a dollar budget or billing statement."""
from contextlib import closing
import sqlite3
from uuid import uuid4


class UsageLedger:
    def __init__(self, path, *, max_requests, per_actor_requests):
        if any(type(v) is not int or v < 1 for v in (max_requests, per_actor_requests)):
            raise ValueError("Request allowances must be positive integers")
        self.path = str(path)
        self.max_requests, self.per_actor_requests = max_requests, per_actor_requests
        with closing(self.connect()) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS provider_calls (
                id TEXT PRIMARY KEY, tenant TEXT NOT NULL, actor TEXT NOT NULL,
                model TEXT NOT NULL, prompt_version TEXT NOT NULL,
                started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                finished_at TEXT, status TEXT NOT NULL,
                input_tokens INTEGER, output_tokens INTEGER)""")

    def connect(self):
        return sqlite3.connect(self.path, timeout=5)

    def reserve(self, actor, model, prompt_version):
        if not isinstance(actor, dict) or any(
                not isinstance(actor.get(k), str) or not actor[k] for k in ("tenant_id", "id")):
            raise ValueError("Authenticated actor required for paid calls")
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            total = db.execute("SELECT count(*) FROM provider_calls").fetchone()[0]
            own = db.execute("SELECT count(*) FROM provider_calls WHERE tenant=? AND actor=?",
                             (actor["tenant_id"], actor["id"])).fetchone()[0]
            if total >= self.max_requests or own >= self.per_actor_requests:
                raise ConnectionError("Provider request allowance exhausted")
            call_id = str(uuid4())
            db.execute("""INSERT INTO provider_calls
                (id,tenant,actor,model,prompt_version,status) VALUES (?,?,?,?,?,'reserved')""",
                (call_id, actor["tenant_id"], actor["id"], model, prompt_version))
            return call_id

    def record_usage(self, call_id, response):
        usage = response.get("usage") if isinstance(response, dict) else None
        values = [usage.get(k) for k in ("input_tokens", "output_tokens")] if isinstance(usage, dict) else []
        # Missing/invalid usage remains unknown, never zero estimated cost.
        if len(values) != 2 or any(type(v) is not int or v < 0 for v in values):
            return
        with closing(self.connect()) as db, db:
            db.execute("UPDATE provider_calls SET input_tokens=?,output_tokens=? WHERE id=?", (*values, call_id))

    def finish(self, call_id, status):
        if status not in {"completed", "failed"}:
            raise ValueError("Invalid call status")
        with closing(self.connect()) as db, db:
            db.execute("UPDATE provider_calls SET status=?,finished_at=CURRENT_TIMESTAMP WHERE id=?",
                       (status, call_id))

    def summary(self, actor):
        scope = (actor["tenant_id"], actor["id"])
        with closing(self.connect()) as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN")
            totals = dict(db.execute("""SELECT count(*) AS requests,
                coalesce(sum(status='failed'),0) AS failed,
                coalesce(sum(status='reserved'),0) AS reserved,
                coalesce(sum(input_tokens IS NULL OR output_tokens IS NULL),0) AS unknown_usage,
                sum(input_tokens) AS input_tokens, sum(output_tokens) AS output_tokens
                FROM provider_calls WHERE tenant=? AND actor=?""", scope).fetchone())
            items = [dict(row) for row in db.execute("""SELECT id,model,prompt_version,
                started_at,finished_at,status,input_tokens,output_tokens
                FROM provider_calls WHERE tenant=? AND actor=? ORDER BY rowid DESC LIMIT 20""", scope)]
        for item in items:
            for key in ("started_at", "finished_at"):
                if item[key]:
                    item[key] = item[key].replace(" ", "T") + "Z"
        return {**totals, "request_limit": self.per_actor_requests,
                "remaining_requests": max(0, self.per_actor_requests - totals["requests"]),
                "items": items}
