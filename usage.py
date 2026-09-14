"""Durable request allowances, not a dollar budget or billing statement."""
from contextlib import closing
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR
import sqlite3
from uuid import uuid4


class UsageLedger:
    def __init__(self, path, *, max_requests, per_actor_requests, budget_usd=None,
                 input_usd_per_million=None, output_usd_per_million=None):
        if any(type(v) is not int or v < 1 for v in (max_requests, per_actor_requests)):
            raise ValueError("Request allowances must be positive integers")
        pricing = (budget_usd, input_usd_per_million, output_usd_per_million)
        if any(value is not None for value in pricing) != all(value is not None for value in pricing):
            raise ValueError("Budget and both token rates must be configured together")
        self.path = str(path)
        self.max_requests, self.per_actor_requests = max_requests, per_actor_requests
        self.cost_enabled = all(value is not None for value in pricing)
        if self.cost_enabled:
            try:
                values = [Decimal(str(value)) for value in pricing]
            except (InvalidOperation, ValueError):
                raise ValueError("Budget and token rates must be finite positive numbers") from None
            if not values[0].is_finite() or values[0] <= 0 or any(
                    not value.is_finite() or value < 0 for value in values[1:]):
                raise ValueError("Budget must be positive and token rates non-negative")
            self.budget_nano_usd = int((values[0] * 1_000_000_000).to_integral_value(ROUND_FLOOR))
            self.input_nano_per_token = int((values[1] * 1000).to_integral_value(ROUND_CEILING))
            self.output_nano_per_token = int((values[2] * 1000).to_integral_value(ROUND_CEILING))
            if self.budget_nano_usd < 1:
                raise ValueError("Budget is too small to enforce")
        else:
            self.budget_nano_usd = self.input_nano_per_token = self.output_nano_per_token = 0
        with closing(self.connect()) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS provider_calls (
                id TEXT PRIMARY KEY, tenant TEXT NOT NULL, actor TEXT NOT NULL,
                model TEXT NOT NULL, prompt_version TEXT NOT NULL,
                started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                finished_at TEXT, status TEXT NOT NULL,
                input_tokens INTEGER, output_tokens INTEGER,
                reserved_nano_usd INTEGER NOT NULL DEFAULT 0,
                actual_nano_usd INTEGER)""")
            columns = {row[1] for row in db.execute("PRAGMA table_info(provider_calls)")}
            if "reserved_nano_usd" not in columns:
                db.execute("ALTER TABLE provider_calls ADD COLUMN reserved_nano_usd INTEGER NOT NULL DEFAULT 0")
            if "actual_nano_usd" not in columns:
                db.execute("ALTER TABLE provider_calls ADD COLUMN actual_nano_usd INTEGER")

    def connect(self):
        return sqlite3.connect(self.path, timeout=5)

    def reserve(self, actor, model, prompt_version, *, input_upper_tokens=None,
                output_upper_tokens=None):
        if not isinstance(actor, dict) or any(
                not isinstance(actor.get(k), str) or not actor[k] for k in ("tenant_id", "id")):
            raise ValueError("Authenticated actor required for paid calls")
        reservation = 0
        if self.cost_enabled:
            if any(type(v) is not int or v < 0 for v in
                   (input_upper_tokens, output_upper_tokens)):
                raise ValueError("Conservative token bounds required for cost reservation")
            if input_upper_tokens + output_upper_tokens == 0:
                raise ValueError("Cost reservation cannot be empty")
            reservation = (input_upper_tokens * self.input_nano_per_token
                           + output_upper_tokens * self.output_nano_per_token)
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            total = db.execute("SELECT count(*) FROM provider_calls").fetchone()[0]
            own = db.execute("SELECT count(*) FROM provider_calls WHERE tenant=? AND actor=?",
                             (actor["tenant_id"], actor["id"])).fetchone()[0]
            if total >= self.max_requests or own >= self.per_actor_requests:
                raise ConnectionError("Provider request allowance exhausted")
            accounted = db.execute("SELECT coalesce(sum(coalesce(actual_nano_usd,reserved_nano_usd)),0) FROM provider_calls").fetchone()[0]
            if self.cost_enabled and accounted + reservation > self.budget_nano_usd:
                raise ConnectionError("Provider cost ceiling exhausted")
            call_id = str(uuid4())
            db.execute("""INSERT INTO provider_calls
                (id,tenant,actor,model,prompt_version,status,reserved_nano_usd)
                VALUES (?,?,?,?,?,'reserved',?)""",
                (call_id, actor["tenant_id"], actor["id"], model, prompt_version, reservation))
            return call_id

    def record_usage(self, call_id, response):
        usage = response.get("usage") if isinstance(response, dict) else None
        values = [usage.get(k) for k in ("input_tokens", "output_tokens")] if isinstance(usage, dict) else []
        # Missing/invalid usage remains unknown, never zero estimated cost.
        if len(values) != 2 or any(type(v) is not int or v < 0 for v in values):
            return
        actual = (values[0] * self.input_nano_per_token
                  + values[1] * self.output_nano_per_token) if self.cost_enabled else None
        with closing(self.connect()) as db, db:
            db.execute("UPDATE provider_calls SET input_tokens=?,output_tokens=?,actual_nano_usd=? WHERE id=?",
                       (*values, actual, call_id))

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
                sum(input_tokens) AS input_tokens, sum(output_tokens) AS output_tokens,
                coalesce(sum(coalesce(actual_nano_usd,reserved_nano_usd)),0) AS accounted_nano_usd
                FROM provider_calls WHERE tenant=? AND actor=?""", scope).fetchone())
            items = [dict(row) for row in db.execute("""SELECT id,model,prompt_version,
                started_at,finished_at,status,input_tokens,output_tokens
                FROM provider_calls WHERE tenant=? AND actor=? ORDER BY rowid DESC LIMIT 20""", scope)]
        for item in items:
            for key in ("started_at", "finished_at"):
                if item[key]:
                    item[key] = item[key].replace(" ", "T") + "Z"
        accounted = totals.pop("accounted_nano_usd")
        return {**totals, "request_limit": self.per_actor_requests,
                "remaining_requests": max(0, self.per_actor_requests - totals["requests"]),
                "pricing_configured": self.cost_enabled,
                "accounted_cost_usd": round(accounted / 1_000_000_000, 9) if self.cost_enabled else None,
                "items": items}
