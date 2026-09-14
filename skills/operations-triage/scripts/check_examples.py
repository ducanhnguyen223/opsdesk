"""Verify that documented triage examples still match the MCP-visible evidence."""
import asyncio
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from mcp import Client  # noqa: E402
from backend import Store  # noqa: E402
from cases_api import seed_cases  # noqa: E402
from mcp_server import build_server  # noqa: E402


async def check():
    examples = json.loads((Path(__file__).parents[1] / "references/examples.json").read_text())
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "examples.sqlite3"
        store = Store(database)
        seed_cases(store, datetime.now(timezone.utc))
        for example in examples:
            async with Client(build_server(database, example["input"]["actor"])) as client:
                case = (await client.call_tool("get_case",
                    {"case_id": example["input"]["case_id"]})).structured_content
                found = (await client.call_tool("search_procedures", {
                    "shipment_id": case["shipment_id"], "query": example["input"]["query"]
                })).structured_content["items"]
            actual = {
                "shipment_id": case["shipment_id"],
                "shipment_status": case["shipment"]["status"],
                "active_order_ids": sorted(order["id"] for order in case["order_items"]
                                           if order["status"] == "active"),
                "procedure_ids": sorted(item["document_id"] for item in found),
                "actions": sorted({item["action"] for item in found}),
                "manual_review": len({item["action"] for item in found}) != 1,
            }
            if actual != example["expected"]:
                raise AssertionError(f"Example {example['input']['case_id']} changed: {actual}")
    print("operations-triage examples: OK")


if __name__ == "__main__":
    asyncio.run(check())
