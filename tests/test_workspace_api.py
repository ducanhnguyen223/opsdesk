import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from fastapi.testclient import TestClient
from test_backend import analyse, approve, demo, mutate

PAYLOAD = {"title": "Quy trình xác minh", "text": "Liên hệ hãng vận chuyển để xác minh sự cố.",
    "policy_scope": "no_policy", "action": "contact_carrier", "allowed_roles": ["operator", "viewer"],
    "effective_from": "2026-01-01T00:00:00Z", "effective_to": None, "expected_version": 0}


class WorkspaceChecks(unittest.TestCase):
    def test_session_identity_and_logout_revocation(self):
        with demo() as (client, _):
            identity = client.get("/api/me").json()
            self.assertEqual(identity["tenant_id"], "A")
            self.assertEqual(set(identity), {"id", "tenant_id", "role", "mode"})
            cookie = client.cookies.get("opsdesk_session")
            self.assertTrue(client.post("/api/logout", json={}).json()["logged_out"])
            self.assertEqual(client.get("/api/me").status_code, 401)
            client.cookies.set("opsdesk_session", cookie)
            self.assertEqual(client.get("/api/me").status_code, 401)

    def test_history_pagination_and_scope(self):
        with demo() as (client, _):
            ids = [analyse(client).json()["analysis_id"] for _ in range(3)]
            first = client.get("/api/analyses?limit=2").json()
            self.assertEqual([i["analysis_id"] for i in first["items"]], list(reversed(ids[1:])))
            second = client.get(f"/api/analyses?limit=2&before={first['next_cursor']}").json()
            self.assertEqual([i["analysis_id"] for i in second["items"]], ids[:1])
            self.assertIsNone(second["next_cursor"])
            self.assertTrue(first["items"][0]["created_at"])
            for actor in ("A-viewer", "B-operator"):
                client.post("/api/demo/login", json={"actor_id": actor})
                self.assertEqual(client.get("/api/analyses").json()["items"], [])
            self.assertEqual(client.get("/api/analyses?limit=101").status_code, 422)

    def test_saved_analysis_and_history_respect_revoked_document(self):
        with demo() as (client, store):
            proposal = analyse(client).json()
            mutate(store, "documents", "A-STANDARD-V2", allowed_roles=[])
            self.assertEqual(client.get("/api/analyses/" + proposal["analysis_id"]).status_code, 404)
            self.assertEqual(client.get("/api/analyses").json()["items"], [])

    def test_ticket_list_is_tenant_scoped(self):
        with demo() as (client, _):
            ticket = approve(client, analyse(client).json()).json()
            self.assertEqual(client.get("/api/tickets").json()["items"][0]["ticket_id"], ticket["ticket_id"])
            client.post("/api/demo/login", json={"actor_id": "A-viewer"})
            self.assertEqual(len(client.get("/api/tickets").json()["items"]), 1)
            client.post("/api/demo/login", json={"actor_id": "B-operator"})
            self.assertEqual(client.get("/api/tickets").json()["items"], [])

    def test_document_lifecycle_search_and_stale_approval(self):
        with demo() as (client, _):
            self.assertEqual(analyse(client, "SHP-1045 chậm").json()["status"], "insufficient_evidence")
            created = client.post("/api/documents", json=PAYLOAD)
            self.assertEqual(created.status_code, 201, created.text)
            doc = created.json()
            self.assertEqual(doc["tenant_id"], "A")
            url = "/api/documents/" + doc["id"]
            hits = client.get("/api/documents/search", params={"q": "van chuyen", "shipment_id": "SHP-1045"}).json()
            self.assertEqual(hits[0]["document_id"], doc["id"])
            proposal = analyse(client, "SHP-1045 chậm").json()
            self.assertEqual(proposal["status"], "ready_for_review")
            updated = dict(PAYLOAD, text="Quy trình mới: xác minh lại với hãng vận chuyển.", expected_version=1)
            self.assertEqual(client.put(url, json=updated).json()["version"], 2)
            self.assertEqual(client.put(url, json=updated).status_code, 409)
            self.assertEqual(approve(client, proposal).status_code, 409)
            versions = client.get(url + "/versions").json()
            self.assertEqual([v["version"] for v in versions], [2, 1])
            self.assertEqual(versions[1]["text"], PAYLOAD["text"])
            self.assertEqual(client.get(url).json()["text"], updated["text"])

    def test_document_permission_and_validation(self):
        with demo() as (client, _):
            for patch in ({"tenant_id": "B"}, {"text": " "}, {"allowed_roles": ["admin"]},
                          {"effective_from": "2026-01-01"}, {"effective_to": ""},
                          {"effective_to": "2025-01-01T00:00:00Z"}):
                self.assertEqual(client.post("/api/documents", json=PAYLOAD | patch).status_code, 422)
            self.assertEqual(client.put("/api/documents/B-STANDARD-V1", json=PAYLOAD).status_code, 404)
            restricted = client.post("/api/documents", json=PAYLOAD | {"allowed_roles": ["operator"]}).json()
            client.post("/api/demo/login", json={"actor_id": "A-viewer"})
            self.assertEqual(client.post("/api/documents", json=PAYLOAD).status_code, 403)
            self.assertEqual(client.get("/api/documents/" + restricted["id"]).status_code, 404)
            listed = client.get("/api/documents").json()["items"]
            self.assertNotIn(restricted["id"], [d["id"] for d in listed])
            self.assertTrue(all(d["tenant_id"] == "A" for d in listed))
            self.assertEqual(client.get("/api/documents/search", params={"q": "chậm", "shipment_id": "SHP-2042"}).status_code, 404)

    def test_concurrent_document_edits_preserve_one_winner(self):
        with demo() as (client, _):
            created = client.post("/api/documents", json=PAYLOAD).json()
            barrier = Barrier(2)

            def update(index):
                with TestClient(client.app) as other:
                    other.cookies.update(client.cookies)
                    barrier.wait(timeout=10)
                    return other.put("/api/documents/" + created["id"],
                                     json=PAYLOAD | {"title": f"Edit {index}", "expected_version": 1})

            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(pool.map(update, range(2)))
            self.assertEqual(sorted(r.status_code for r in responses), [200, 409])
            self.assertEqual(len(client.get("/api/documents/" + created["id"] + "/versions").json()), 2)

    def test_all_new_reads_require_session(self):
        with demo(actor=None) as (client, _):
            for path in ("/api/me", "/api/analyses", "/api/tickets", "/api/documents",
                         "/api/documents/A-STANDARD-V2", "/api/documents/A-STANDARD-V2/versions",
                         "/api/documents/search?q=test&shipment_id=SHP-1042"):
                self.assertEqual(client.get(path).status_code, 401, path)
