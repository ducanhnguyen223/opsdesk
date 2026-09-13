import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app import create_app
from openai_provider import OpenAIProvider
from usage import UsageLedger


class UsageChecks(unittest.TestCase):
    def test_usage_api_is_authenticated_and_isolates_actor_and_tenant(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = UsageLedger(Path(directory) / "usage.sqlite3", max_requests=50, per_actor_requests=30)
            for tenant, actor, amount in (("A", "A-operator", 25), ("A", "A-viewer", 1), ("B", "B-operator", 1)):
                for i in range(amount):
                    call = ledger.reserve({"tenant_id": tenant, "id": actor}, actor+"-model", "v1")
                    if i == 0:
                        ledger.record_usage(call, {"usage": {"input_tokens": 9, "output_tokens": 2}})
                        ledger.finish(call, "completed")
            provider = OpenAIProvider(api_key="test", model="test", ledger=ledger)
            with TestClient(create_app(Path(directory) / "app.sqlite3", demo_login=True, provider=provider)) as client:
                self.assertEqual(client.get('/api/usage').status_code, 401)
                for actor, count in (("A-operator",25), ("A-viewer",1), ("B-operator",1)):
                    client.post('/api/demo/login', json={"actor_id":actor})
                    response=client.get('/api/usage')
                    self.assertEqual(response.status_code, 200)
                    summary=response.json()['summary']
                    self.assertEqual(summary['requests'],count)
                    self.assertEqual(summary['unknown_usage'],count-1)
                    self.assertEqual(summary['input_tokens'],9)
                    self.assertEqual(summary['remaining_requests'],30-count)
                    self.assertEqual(len(summary['items']),min(20,count))
                    self.assertTrue(all(item['model']==actor+'-model' for item in summary['items']))
                    self.assertTrue(all(item['started_at'].endswith('Z') for item in summary['items']))

    def test_offline_usage_does_not_fabricate_token_counts(self):
        with tempfile.TemporaryDirectory() as directory, TestClient(create_app(
                Path(directory)/'app.sqlite3', demo_login=True)) as client:
            client.post('/api/demo/login',json={"actor_id":"A-operator"})
            self.assertEqual(client.get('/api/usage').json(),
                             {"mode":"offline-deterministic","summary":None})

    def test_atomic_allowance_survives_restart_and_counts_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "usage.sqlite3"
            ledger = UsageLedger(path, max_requests=2, per_actor_requests=1)
            actor = {"tenant_id": "A", "id": "operator"}
            def reserve(_):
                try:
                    return ledger.reserve(actor, "test", "v1")
                except ConnectionError:
                    return None
            with ThreadPoolExecutor(max_workers=8) as pool:
                ids = [v for v in pool.map(reserve, range(8)) if v]
            self.assertEqual(len(ids), 1)
            ledger.finish(ids[0], "failed")
            restarted = UsageLedger(path, max_requests=2, per_actor_requests=1)
            with self.assertRaises(ConnectionError):
                restarted.reserve(actor, "test", "v1")
            restarted.reserve({"tenant_id": "B", "id": "operator"}, "test", "v1")
            with self.assertRaises(ConnectionError):
                restarted.reserve({"tenant_id": "C", "id": "operator"}, "test", "v1")

    def test_provider_records_usage_without_prompts_keys_or_response_text(self):
        for valid in (True, False):
            with self.subTest(valid=valid), tempfile.TemporaryDirectory() as directory:
                ledger = UsageLedger(Path(directory) / "usage.sqlite3", max_requests=1, per_actor_requests=1)
                context = {"actor": {"tenant_id": "A", "id": "operator"},
                           "shipment": {}, "orders": [], "documents": [], "retrieved_chunks": []}
                choice = {"action": "notify_customer", "citation_ids": [], "draft_message": "PRIVATE DRAFT"}
                body = {"status": "completed", "usage": {"input_tokens": 71, "output_tokens": 22},
                        "output": [{"type": "message", "content": [{"type": "output_text",
                            "text": json.dumps(choice) if valid else "invalid JSON"}]}]}
                requests = []
                def handle(request):
                    requests.append(request)
                    return httpx.Response(200, json=body)
                with httpx.Client(transport=httpx.MockTransport(handle)) as client:
                    provider = OpenAIProvider(api_key="PRIVATE KEY", model="test", client=client, ledger=ledger)
                    if valid:
                        self.assertEqual(provider("PRIVATE NOTICE", context), choice)
                    else:
                        with self.assertRaises(ValueError):
                            provider("PRIVATE NOTICE", context)
                    with self.assertRaises(ConnectionError):
                        provider("PRIVATE NOTICE", context)
                self.assertEqual(len(requests), 1)
                with closing(ledger.connect()) as db:
                    row = db.execute("SELECT status,input_tokens,output_tokens FROM provider_calls").fetchone()
                    self.assertEqual(row, ("completed" if valid else "failed", 71, 22))
                    self.assertNotIn("PRIVATE", "\n".join(db.iterdump()))

    def test_unknown_usage_and_invalid_actor_fail_safely(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = UsageLedger(Path(directory) / "usage.sqlite3", max_requests=1, per_actor_requests=1)
            with self.assertRaises(ValueError):
                ledger.reserve(None, "test", "v1")
            call = ledger.reserve({"tenant_id": "A", "id": "operator"}, "test", "v1")
            for usage in (None, {}, {"input_tokens": True, "output_tokens": 2},
                          {"input_tokens": -1, "output_tokens": 2}):
                ledger.record_usage(call, {"usage": usage})
            with closing(ledger.connect()) as db:
                self.assertEqual(db.execute("SELECT status,input_tokens,output_tokens FROM provider_calls").fetchone(),
                                 ("reserved", None, None))
