import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from mcp import Client

from backend import Store, encode, record
from cases_api import seed_cases
from mcp_server import build_server


class MCPChecks(unittest.IsolatedAsyncioTestCase):
    async def test_read_only_tools_are_scoped_bounded_and_recheck_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "demo.sqlite3"
            store = Store(path)
            seed_cases(store, datetime.now(timezone.utc))
            async with Client(build_server(path, "A-operator")) as client:
                tools = (await client.list_tools()).tools
                self.assertEqual({tool.name for tool in tools},
                                 {"list_cases", "get_case", "search_procedures"})
                self.assertTrue(all(tool.annotations.read_only_hint for tool in tools))
                self.assertTrue(all("actor" not in tool.input_schema["properties"]
                                    and "db" not in tool.input_schema["properties"] for tool in tools))

                listed = await client.call_tool("list_cases", {"limit": 2})
                self.assertFalse(listed.is_error)
                self.assertEqual(len(listed.structured_content["items"]), 2)
                self.assertTrue(all(item["tenant_id"] == "A"
                                    for item in listed.structured_content["items"]))
                case = await client.call_tool("get_case", {"case_id": "EX-9000"})
                self.assertFalse(case.is_error)
                self.assertEqual(case.structured_content["shipment"]["tenant_id"], "A")
                self.assertLessEqual(len(case.structured_content["events"]), 20)
                search = await client.call_tool("search_procedures",
                    {"shipment_id": "SHP-9000", "query": "giao chậm"})
                self.assertFalse(search.is_error)
                self.assertTrue(all(item["document_id"].startswith("A-")
                                    for item in search.structured_content["items"]))
                self.assertTrue((await client.call_tool(
                    "get_case", {"case_id": "EX-9100"})).is_error)
                self.assertTrue((await client.call_tool(
                    "search_procedures", {"shipment_id": "SHP-9100", "query": "giao chậm"})).is_error)
                self.assertTrue((await client.call_tool(
                    "list_cases", {"limit": 51})).is_error)

                with store.connect(write=True) as db:
                    actor = record(db, "actors", "A-operator", "A")
                    actor["active"] = False
                    db.execute("UPDATE records SET body=? WHERE kind='actors' AND id=?",
                               (encode(actor), actor["id"]))
                revoked = await client.call_tool("list_cases", {})
                self.assertTrue(revoked.is_error)
                self.assertIn("inactive", revoked.content[0].text)
