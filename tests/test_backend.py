"""HTTP acceptance against isolated SQLite files; no paid API calls."""
import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

from fastapi.testclient import TestClient
from app import create_app
from backend import FIXTURE, encode, fake_provider, instant

DATA = json.loads(FIXTURE.read_text())
CASES = {c["id"]: c for c in DATA["cases"]}
NOW = instant(DATA["as_of"])


def timeout_provider(message, context):
    raise TimeoutError("injected")


@contextmanager
def demo(actor="A-operator", provider=fake_provider):
    with tempfile.TemporaryDirectory(prefix="opsdesk-test-") as directory:
        app = create_app(Path(directory) / "test.sqlite3", demo_login=True,
                         provider=provider, clock=lambda: NOW)
        with TestClient(app) as client:
            if actor:
                assert client.post("/api/demo/login", json={"actor_id": actor}).status_code == 200
            yield client, app.state.store


def analyse(client, message="SHP-1042 chậm."):
    return client.post("/api/analyses", json={"message": message})


def approve(client, proposal, key="key-1", revision=1):
    return client.post(f"/api/analyses/{proposal['analysis_id']}/approve",
                       json={"proposal_revision": revision, "confirmed": True},
                       headers={"Idempotency-Key": key})


def mutate(store, kind, resource_id, **changes):
    with store.connect(write=True) as db:
        body = json.loads(db.execute("SELECT body FROM records WHERE kind=? AND id=?",
                                     (kind, resource_id)).fetchone()[0])
        body.update(changes)
        db.execute("UPDATE records SET body=? WHERE kind=? AND id=?", (encode(body), kind, resource_id))


def ticket_count(store):
    with store.connect() as db:
        return db.execute("SELECT count(*) FROM tickets").fetchone()[0]


class BaselineAcceptance(unittest.TestCase):
    pass


def scenario(case):
    def test(self):
        provider = timeout_provider if case.get("fault") else fake_provider
        with demo(case["actor"], provider) as (client, store):
            expected = case["expected"]
            if "operation" not in case:
                response = analyse(client, case["message"])
                self.assertEqual(response.status_code, expected["http"], response.text)
                if response.status_code == 200:
                    result = response.json()
                    self.assertEqual(result["status"], expected["status"])
                    if result["status"] != "ready_for_review":
                        self.assertEqual(result["recommended_actions"], [])
                        self.assertIsNone(result["draft_message"])
                    if "affected_order_ids" in expected:
                        self.assertEqual([o["id"] for o in result["affected_orders"]], expected["affected_order_ids"])
                        self.assertEqual(result["recommended_actions"][0]["action"], expected["action"])
                        self.assertIn(expected["shipment_id"], [f["source_id"] for f in result["verified_facts"]])
                    citations = [c["document_id"] for c in result["citations"]]
                    if "citation_ids" in expected or "conflicting_citation_ids" in expected:
                        self.assertEqual(citations, expected.get("citation_ids", expected.get("conflicting_citation_ids")))
                    for forbidden in expected.get("forbidden_citation_ids", []):
                        self.assertNotIn(forbidden, citations)
                    if case["actor"].startswith("A-"):
                        self.assertNotIn('"tenant_id":"B"', encode(result))
                    self.assertEqual(client.get("/api/analyses/" + result["analysis_id"]).json(), result)
            else:
                setup = analyse(client, CASES[case["setup_case"]]["message"])
                self.assertEqual(setup.status_code, 200)
                proposal = setup.json()
                if "mutation" in case:
                    mutation = dict(case["mutation"])
                    mutate(store, "shipments", mutation.pop("shipment_id"), **mutation)
                first = approve(client, proposal, case["idempotency_key"])
                if "http_sequence" in expected:
                    second = approve(client, proposal, case["idempotency_key"],
                                     revision=2 if case["operation"] == "approve_changed_payload" else 1)
                    self.assertEqual([first.status_code, second.status_code], expected["http_sequence"])
                    if expected.get("same_ticket_id"):
                        self.assertEqual(first.json()["ticket_id"], second.json()["ticket_id"])
                else:
                    self.assertEqual(first.status_code, expected["http"], first.text)
            self.assertEqual(ticket_count(store), expected["tickets_created"])
    return test


for case in DATA["cases"]:
    setattr(BaselineAcceptance, "test_" + case["id"] + "_" + case["category"], scenario(case))


class RegressionChecks(unittest.TestCase):
    def test_provider_draft_is_persisted_but_never_sent(self):
        text = "Lô SHP-1042 đang giao chậm. Vui lòng xem thông tin được xác nhận."
        def provider(message, context):
            return {**fake_provider(message, context), "draft_message": text}
        with demo(provider=provider) as (client, store):
            result = analyse(client).json()
            self.assertEqual(result["status"], "ready_for_review")
            self.assertEqual(result["draft_message"], text)
            self.assertTrue(any("not fact-verified" in w for w in result["warnings"]))
            self.assertEqual(client.get("/api/analyses/" + result["analysis_id"]).json(), result)
            self.assertEqual(ticket_count(store), 0)

    def test_invalid_provider_draft_cannot_authorize_ticket(self):
        for draft in (None, "", "x" * 4001, "SHP-2042", "SHP-1042 SHP-2042", "SHP-1042\x00"):
            def provider(message, context):
                return {**fake_provider(message, context), "draft_message": draft}
            with self.subTest(draft_type=type(draft).__name__), demo(provider=provider) as (client, store):
                result = analyse(client).json()
                self.assertEqual(result["status"], "insufficient_evidence")
                self.assertEqual(result["recommended_actions"], [])
                self.assertIsNone(result["draft_message"])
                self.assertEqual(approve(client, result).status_code, 409)

    def test_empty_policy_does_not_authorize_action(self):
        with demo() as (client, store):
            mutate(store, "documents", "A-STANDARD-V2", text=" ")
            result = analyse(client).json()
            self.assertEqual(result["status"], "insufficient_evidence")
            self.assertEqual(approve(client, result).status_code, 409)

    def test_evidence_change_during_provider_call_is_rejected(self):
        store_ref = []

        def changing_provider(message, context):
            mutate(store_ref[0], "documents", "A-STANDARD-V2", allowed_roles=[])
            return fake_provider(message, context)

        with demo(provider=changing_provider) as (client, store):
            store_ref.append(store)
            self.assertEqual(analyse(client).status_code, 409)
            with store.connect() as db:
                self.assertEqual(db.execute("SELECT count(*) FROM analyses").fetchone()[0], 0)

    def test_atomic_rollback_when_idempotency_write_fails(self):
        with demo() as (client, store):
            proposal = analyse(client).json()
            with store.connect(write=True) as db:
                db.execute("""CREATE TRIGGER fail_approval BEFORE INSERT ON approvals
                    BEGIN SELECT RAISE(ABORT, 'test fault'); END""")
            with self.assertRaises(sqlite3.IntegrityError):
                approve(client, proposal)
            self.assertEqual(ticket_count(store), 0)
            with store.connect(write=True) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM approvals").fetchone()[0], 0)
                db.execute("DROP TRIGGER fail_approval")
            self.assertEqual(approve(client, proposal).status_code, 201)

    def test_database_outage_is_retryable(self):
        with demo() as (client, store):
            with patch.object(store, "connect", side_effect=sqlite3.OperationalError("test fault")):
                response = analyse(client)
            self.assertEqual(response.status_code, 503)
            self.assertEqual(ticket_count(store), 0)

    def test_concurrent_approvals_same_and_different_keys(self):
        for same_key in (True, False):
            with self.subTest(same_key=same_key), demo() as (client, store):
                proposal = analyse(client).json()
                barrier = Barrier(6)

                def submit(index):
                    with TestClient(client.app) as other:
                        other.cookies.update(client.cookies)
                        barrier.wait(timeout=10)
                        return approve(other, proposal, "same" if same_key else f"key-{index}")

                with ThreadPoolExecutor(max_workers=6) as pool:
                    responses = list(pool.map(submit, range(6)))
                self.assertEqual(sorted(r.status_code for r in responses), [200] * 5 + [201])
                self.assertEqual(len({r.json()["ticket_id"] for r in responses}), 1)
                self.assertEqual(ticket_count(store), 1)

    def test_evidence_changes_block_approval_and_replay(self):
        changes = [("orders", "ORD-1042-1", {"revision": 2}),
                   ("orders", "ORD-1042-2", {"status": "active"}),
                   ("documents", "A-STANDARD-V2", {"text": "Changed without bumping version"}),
                   ("documents", "A-STANDARD-V2", {"allowed_roles": ["viewer"]}),
                   ("documents", "A-STANDARD-V2", {"effective_to": DATA["as_of"]})]
        for kind, resource_id, patch in changes:
            for already_approved in (False, True):
                with self.subTest(kind=kind, patch=patch, replay=already_approved), demo() as (client, store):
                    proposal = analyse(client).json()
                    if already_approved:
                        self.assertEqual(approve(client, proposal).status_code, 201)
                    mutate(store, kind, resource_id, **patch)
                    self.assertEqual(approve(client, proposal).status_code, 409)
                    self.assertEqual(ticket_count(store), int(already_approved))

    def test_current_role_and_session_revocation(self):
        for patch, expected in [({"role": "viewer"}, 403), ({"active": False}, 401), ({"auth_revision": 2}, 401)]:
            with self.subTest(patch=patch), demo() as (client, store):
                proposal = analyse(client).json()
                approve(client, proposal)
                mutate(store, "actors", "A-operator", **patch)
                self.assertEqual(approve(client, proposal).status_code, expected)
                self.assertEqual(ticket_count(store), 1)

    def test_scoped_reads_and_ownership(self):
        with demo() as (client, store):
            proposal = analyse(client).json()
            ticket = approve(client, proposal).json()
            self.assertEqual(client.get("/api/tickets/" + ticket["ticket_id"]).status_code, 200)
            self.assertEqual(len(client.get("/api/shipments/SHP-1042/orders").json()), 1)
            for actor in ("A-viewer", "B-operator"):
                client.post("/api/demo/login", json={"actor_id": actor})
                self.assertEqual(client.get("/api/analyses/" + proposal["analysis_id"]).status_code, 404)
                self.assertEqual(approve(client, proposal).status_code, 404)
            for path in ("/api/shipments/SHP-1042", "/api/shipments/SHP-1042/orders",
                         "/api/tickets/" + ticket["ticket_id"]):
                self.assertEqual(client.get(path).status_code, 404)

    def test_hostile_note_is_in_provider_context_but_not_authority(self):
        observed = []

        def spy(message, context):
            observed.extend(context["documents"])
            return fake_provider(message, context)

        with demo(provider=spy) as (client, store):
            result = analyse(client).json()
            self.assertIn("A-UNTRUSTED-NOTE", [d["id"] for d in observed])
            self.assertTrue(all(d["tenant_id"] == "A" for d in observed))
            self.assertEqual(result["status"], "ready_for_review")
            self.assertNotIn("A-UNTRUSTED-NOTE", [c["document_id"] for c in result["citations"]])

    def test_ungrounded_provider_fails_closed(self):
        for output in ({"action": "send_money", "citation_ids": ["A-STANDARD-V2"]},
                       {"action": "notify_customer", "citation_ids": ["B-STANDARD-V1"]}, None):
            with self.subTest(output=output), demo(provider=lambda *_: output) as (client, store):
                result = analyse(client).json()
                self.assertEqual(result["status"], "insufficient_evidence")
                self.assertEqual(approve(client, result).status_code, 409)
                self.assertEqual(ticket_count(store), 0)

    def test_request_validation_and_browser_boundaries(self):
        with demo() as (client, store):
            for payload in ({"message": " "}, {"message": "a" * 8001}, {"message": 123},
                            {"message": "SHP-1042", "tenant_id": "B"}, {"message": "SHP-1042", "fault": "timeout"}):
                self.assertEqual(client.post("/api/analyses", json=payload).status_code, 422)
            proposal = analyse(client).json()
            for payload in ({"proposal_revision": True, "confirmed": True},
                            {"proposal_revision": 1, "confirmed": 1},
                            {"proposal_revision": 1, "confirmed": False}):
                self.assertEqual(client.post(f"/api/analyses/{proposal['analysis_id']}/approve",
                    json=payload, headers={"Idempotency-Key": "k"}).status_code, 422)
            self.assertEqual(client.post("/api/analyses", json={"message": "SHP-1042"},
                headers={"Origin": "https://evil.example"}).status_code, 403)
            self.assertEqual(client.get("/health", headers={"Host": "evil.example"}).status_code, 400)
            self.assertEqual(client.post("/api/analyses", content="{}", headers={"Content-Type": "text/plain"}).status_code, 415)
            self.assertEqual(client.post("/api/analyses", json={"message": "a" * 40001}).status_code, 413)
            self.assertEqual(ticket_count(store), 0)

    def test_negated_delay_requires_clarification(self):
        with demo() as (client, _):
            for message in ("SHP-1042 không chậm", "SHP-1042 is not delayed", "SHP-1042 đã giao"):
                self.assertEqual(analyse(client, message).json()["status"], "needs_clarification")

    def test_restart_keeps_ticket_and_does_not_reseed(self):
        with demo() as (client, store):
            proposal = analyse(client).json()
            first = approve(client, proposal).json()
            new_app = create_app(store.path, demo_login=False, clock=lambda: NOW)
            with TestClient(new_app) as restarted:
                self.assertEqual(restarted.post("/api/demo/login", json={"actor_id": "A-operator"}).status_code, 404)
                restarted.cookies.update(client.cookies)
                self.assertEqual(approve(restarted, proposal).json()["ticket_id"], first["ticket_id"])
                self.assertEqual(ticket_count(store), 1)


if __name__ == "__main__":
    unittest.main()
