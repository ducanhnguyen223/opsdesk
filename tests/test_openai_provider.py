import json
import unittest

import httpx

from openai_provider import ENDPOINT, OpenAIProvider


CONTEXT = {"shipment": {"id": "SHP-1042"}, "orders": [], "documents": [], "retrieved_chunks": []}
CHOICE = {"action": "notify_customer", "citation_ids": ["A-STANDARD-V2"],
          "draft_message": "Lô SHP-1042 đang giao chậm; vui lòng kiểm tra thông tin cập nhật."}


def completed(content=None):
    return {"status": "completed", "output": [{"type": "reasoning"},
        {"type": "message", "content": content if content is not None else
            [{"type": "output_text", "text": json.dumps(CHOICE)}]}]}


class OpenAIContractChecks(unittest.TestCase):
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

    def test_invalid_draft_fails_closed(self):
        for draft in (None, " ", "x" * 4001, 3):
            body = completed([{"type": "output_text", "text": json.dumps({**CHOICE, "draft_message": draft})}])
            with self.subTest(draft_type=type(draft).__name__), httpx.Client(
                    transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))) as client:
                with self.assertRaises(ValueError):
                    OpenAIProvider(api_key="test-key", model="test-model", client=client)("test", CONTEXT)
