import json
from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path

import httpx

from openai_provider import ENDPOINT, ExactCache, OpenAIProvider
from usage import UsageLedger


CONTEXT = {"shipment": {"id": "SHP-1042"}, "orders": [], "documents": [], "retrieved_chunks": []}
CHOICE = {"action": "notify_customer", "citation_ids": ["A-STANDARD-V2"],
          "draft_message": "Lô SHP-1042 đang giao chậm; vui lòng kiểm tra thông tin cập nhật."}


def completed(content=None):
    return {"status": "completed", "output": [{"type": "reasoning"},
        {"type": "message", "content": content if content is not None else
            [{"type": "output_text", "text": json.dumps(CHOICE)}]}]}


class OpenAIContractChecks(unittest.TestCase):
    def test_cost_ceiling_blocks_before_network(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = UsageLedger(Path(directory) / "usage.sqlite3", max_requests=2,
                per_actor_requests=2, budget_usd="0.000001", input_usd_per_million=1,
                output_usd_per_million=1)
            requests = []
            with httpx.Client(transport=httpx.MockTransport(
                    lambda request: requests.append(request) or httpx.Response(200, json=completed()))) as client:
                provider = OpenAIProvider(api_key="test", model="model-a", client=client, ledger=ledger)
                context = {**CONTEXT, "actor": {"tenant_id": "A", "id": "A-operator"}}
                with self.assertRaisesRegex(ConnectionError, "cost ceiling"):
                    provider("SHP-1042", context)
            self.assertEqual(requests, [])
            with closing(ledger.connect()) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM provider_calls").fetchone()[0], 0)

    def test_exact_cache_persists_and_scopes_actor_and_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cache.sqlite3"
            requests = []
            def handle(request):
                requests.append(request)
                return httpx.Response(200, json=completed())
            context = {**CONTEXT, "actor": {"tenant_id": "A", "id": "A-operator",
                "role": "operator", "auth_revision": 1}}
            with httpx.Client(transport=httpx.MockTransport(handle)) as client:
                provider = OpenAIProvider(api_key="test", model="model-a", client=client,
                                          cache=ExactCache(path))
                self.assertEqual(provider("PRIVATE NOTICE SHP-1042", context), CHOICE)
                self.assertEqual(provider("PRIVATE NOTICE SHP-1042", context), CHOICE)
                restarted = OpenAIProvider(api_key="test", model="model-a", client=client,
                                           cache=ExactCache(path))
                self.assertEqual(restarted("PRIVATE NOTICE SHP-1042", context), CHOICE)
                changed_actor = {**context, "actor": {**context["actor"], "auth_revision": 2}}
                restarted("PRIVATE NOTICE SHP-1042", changed_actor)
                changed_evidence = {**context, "shipment": {"id": "SHP-1042", "revision": 2}}
                restarted("PRIVATE NOTICE SHP-1042", changed_evidence)
            self.assertEqual(len(requests), 3)
            with closing(sqlite3.connect(path)) as db:
                dump = "\n".join(db.iterdump())
            self.assertNotIn("PRIVATE NOTICE", dump)
            self.assertNotIn("test", dump)

    def test_exact_cache_blocks_duplicate_inflight_and_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = ExactCache(Path(directory) / "cache.sqlite3")
            key = "a" * 64
            self.assertEqual(cache.claim(key), (None, True))
            with self.assertRaisesRegex(ConnectionError, "already in progress"):
                cache.claim(key)
            with closing(sqlite3.connect(cache.path)) as db, db:
                db.execute("UPDATE provider_cache SET claimed_at=0 WHERE key=?", (key,))
                db.commit()
            self.assertEqual(cache.claim(key), (None, True))
            cache.store(key, CHOICE)
            self.assertEqual(cache.claim(key), (CHOICE, False))
            with closing(sqlite3.connect(cache.path)) as db, db:
                db.execute("UPDATE provider_cache SET body='not json' WHERE key=?", (key,))
                db.commit()
            with self.assertRaisesRegex(ValueError, "corrupt"):
                cache.claim(key)

    def test_official_endpoint_strict_schema_and_no_storage(self):
        requests = []

        def handle(request):
            requests.append(request)
            return httpx.Response(200, json=completed())

        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            provider = OpenAIProvider(api_key="test-not-a-real-key", model="test-model", client=client)
            self.assertEqual(provider("SHP-1042", CONTEXT), CHOICE)
        self.assertEqual(len(requests), 1)
        request = requests[0]
        self.assertEqual(str(request.url), ENDPOINT)
        body = json.loads(request.content)
        self.assertFalse(body["store"])
        self.assertTrue(body["text"]["format"]["strict"])
        self.assertNotIn("tools", body)
        self.assertNotIn("test-not-a-real-key", request.content.decode())
        self.assertEqual(body["model"], "test-model")

    def test_custom_responses_endpoint(self):
        requests = []
        with httpx.Client(transport=httpx.MockTransport(
                lambda request: requests.append(request) or httpx.Response(200, json=completed()))) as client:
            provider = OpenAIProvider(api_key="test", model="model-a",
                                      base_url="https://api.example.com/v1", client=client)
            provider("SHP-1042", CONTEXT)
        self.assertEqual(str(requests[0].url), "https://api.example.com/v1/responses")

    def test_prompt_json_mode_accepts_fenced_json(self):
        body = completed([{"type": "output_text", "text": "```json\n" + json.dumps(CHOICE) + "\n```"}])
        requests = []
        with httpx.Client(transport=httpx.MockTransport(
                lambda request: requests.append(request) or httpx.Response(200, json=body))) as client:
            provider = OpenAIProvider(api_key="test", model="model-a",
                                      structured_output=False, client=client)
            self.assertEqual(provider("SHP-1042", CONTEXT), CHOICE)
        self.assertNotIn("text", json.loads(requests[0].content))

    def test_refusal_incomplete_and_malformed_fail_closed(self):
        bodies = [{"status": "incomplete", "output": []},
                  completed([{"type": "refusal", "refusal": "No"}]),
                  completed([{"type": "output_text", "text": "not json"}]),
                  completed([]), completed([{"type": "output_text", "text": "{}"}]),
                  {"status": "completed", "output": [None]},
                  {"status": "completed", "output": [{"type": "message", "content": None}]}]
        for body in bodies:
            with self.subTest(body=body), httpx.Client(transport=httpx.MockTransport(
                    lambda _: httpx.Response(200, json=body))) as client:
                provider = OpenAIProvider(api_key="test-key", model="test-model", client=client)
                with self.assertRaises(ValueError):
                    provider("test", CONTEXT)

    def test_errors_do_not_retry_redirect_or_expose_error_body(self):
        for status in (301, 401, 429, 500):
            requests = []

            def handle(request):
                requests.append(request)
                return httpx.Response(status, text="SENSITIVE_ERROR_BODY",
                                      headers={"Location": "https://evil.example"})

            with self.subTest(status=status), httpx.Client(transport=httpx.MockTransport(handle)) as client:
                with self.assertRaises(ConnectionError) as caught:
                    OpenAIProvider(api_key="test-key", model="test-model", client=client)("test", CONTEXT)
                self.assertNotIn("SENSITIVE", str(caught.exception))
                self.assertEqual(len(requests), 1)

    def test_timeout_and_missing_configuration(self):
        def timeout(request):
            raise httpx.ReadTimeout("test", request=request)

        with httpx.Client(transport=httpx.MockTransport(timeout)) as client:
            with self.assertRaises(TimeoutError):
                OpenAIProvider(api_key="test-key", model="test-model", client=client)("test", CONTEXT)
        for key, model in (("", "test"), ("test", "")):
            with self.assertRaises(ValueError):
                OpenAIProvider(api_key=key, model=model)
        with self.assertRaisesRegex(ValueError, "ledger"):
            OpenAIProvider(api_key="test-key", model="test-model")
        with tempfile.TemporaryDirectory() as directory, self.assertRaisesRegex(ValueError, "cost-aware"):
            OpenAIProvider(api_key="test-key", model="test-model", ledger=UsageLedger(
                Path(directory) / "usage.sqlite3", max_requests=1, per_actor_requests=1))

    def test_invalid_draft_fails_closed(self):
        for draft in (None, " ", "x" * 4001, 3):
            body = completed([{"type": "output_text", "text": json.dumps({**CHOICE, "draft_message": draft})}])
            with self.subTest(draft_type=type(draft).__name__), httpx.Client(
                    transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))) as client:
                with self.assertRaises(ValueError):
                    OpenAIProvider(api_key="test-key", model="test-model", client=client)("test", CONTEXT)
