"""Read-only local MCP access to an existing OpsDesk database."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from backend import Problem, Store, bound_actor, record, scoped_rows
from cases_api import list_case_records
from retrieval import search_procedures as search_scoped_procedures


READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                            idempotentHint=True, openWorldHint=False)


def build_server(db_path, actor_id):
    store = Store(db_path)
    server = MCPServer("OpsDesk", instructions=(
        "Read synthetic logistics cases and authorized procedures. "
        "These tools never approve tickets, change state, or contact anyone."))

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def list_cases(
        status: Literal["all", "active", "new", "investigating", "waiting_carrier",
                        "escalated", "resolved"] = "active",
        query: Annotated[str, Field(max_length=160)] = "",
        mine: bool = False,
        limit: Annotated[int, Field(ge=1, le=50)] = 20,
    ) -> dict[str, Any]:
        """List cases visible to the configured operator identity."""
        try:
            with store.connect() as db:
                current = bound_actor(db, actor_id)
                cases = scoped_rows(db, "cases", current["tenant_id"])
                return list_case_records(cases, current, datetime.now(timezone.utc).isoformat(),
                                         status=status, query=query, mine=mine, limit=limit)
        except Problem as exc:
            raise ToolError(exc.detail) from None

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def get_case(case_id: Annotated[str, Field(pattern=r"^EX-\d+$")]) -> dict[str, Any]:
        """Read one visible case with its shipment; output is capped to recent events."""
        try:
            with store.connect() as db:
                current = bound_actor(db, actor_id)
                case = record(db, "cases", case_id, current["tenant_id"])
                shipment = record(db, "shipments", case["shipment_id"], current["tenant_id"])
        except Problem as exc:
            raise ToolError(exc.detail) from None
        case["events"] = case.get("events", [])[-20:]
        return {**case, "shipment": shipment, "synthetic": True}

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def search_procedures(
        shipment_id: Annotated[str, Field(pattern=r"^SHP-\d+$")],
        query: Annotated[str, Field(min_length=1, max_length=8000)],
        limit: Annotated[int, Field(ge=1, le=20)] = 6,
    ) -> dict[str, Any]:
        """Search only procedures authorized for the configured identity and shipment scope."""
        try:
            with store.connect() as db:
                current = bound_actor(db, actor_id)
                shipment = record(db, "shipments", shipment_id, current["tenant_id"])
                hits = search_scoped_procedures(query, scoped_rows(db, "documents", current["tenant_id"]),
                    tenant_id=current["tenant_id"], role=current["role"],
                    policy_scope=shipment["policy_scope"], as_of=datetime.now(timezone.utc),
                    limit=limit)
        except Problem as exc:
            raise ToolError(exc.detail) from None
        return {"shipment_id": shipment_id, "items": hits, "synthetic": True}

    return server


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Read-only local OpsDesk MCP server")
    parser.add_argument("--db", default=str(Path(__file__).parent / "opsdesk.sqlite3"))
    parser.add_argument("--actor", required=True,
                        help="Operator identity fixed by the trusted MCP host configuration")
    args = parser.parse_args()
    if not Path(args.db).is_file():
        parser.error("database does not exist; start OpsDesk once before MCP")
    build_server(args.db, args.actor).run()
