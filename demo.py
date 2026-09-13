"""Run a complete offline workflow through a real, temporary localhost server."""
import json
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx


def main():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="opsdesk-demo-") as directory:
        process = subprocess.Popen([sys.executable, str(Path(__file__).with_name("app.py")),
            "--port", str(port), "--db", str(Path(directory) / "demo.sqlite3")],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=5, trust_env=False) as client:
                for _ in range(100):
                    if process.poll() is not None:
                        raise RuntimeError(process.stderr.read())
                    try:
                        client.get("/health").raise_for_status()
                        break
                    except httpx.ConnectError:
                        time.sleep(0.1)
                else:
                    raise RuntimeError("Local demo server did not start")
                client.post("/api/demo/login", json={"actor_id": "A-operator"}).raise_for_status()
                response = client.post("/api/analyses", json={"message": "Lô SHP-1042 chậm hai ngày."})
                response.raise_for_status()
                proposal = response.json()
                assert proposal["status"] == "ready_for_review", proposal
                url = f"/api/analyses/{proposal['analysis_id']}/approve"
                body = {"proposal_revision": 1, "confirmed": True}
                headers = {"Idempotency-Key": "demo-approval"}
                first = client.post(url, json=body, headers=headers)
                replay = client.post(url, json=body, headers=headers)
                assert (first.status_code, replay.status_code) == (201, 200)
                assert first.json()["ticket_id"] == replay.json()["ticket_id"]
                assert client.get("/api/tickets/" + first.json()["ticket_id"]).status_code == 200
                assert client.get("/api/shipments/SHP-2042").status_code == 404
                assert client.get("/api/analyses").json()["items"][0]["analysis_id"] == proposal["analysis_id"]
                assert len(client.get("/api/tickets").json()["items"]) == 1
                document = client.post("/api/documents", json={"title": "Demo procedure",
                    "text": "Liên hệ hãng vận chuyển để xác minh.", "policy_scope": "no_policy",
                    "action": "contact_carrier", "allowed_roles": ["operator"],
                    "effective_from": "2026-01-01T00:00:00Z", "expected_version": 0})
                assert document.status_code == 201, document.text
                search = client.get("/api/documents/search", params={"q": "xác minh", "shipment_id": "SHP-1045"})
                assert search.status_code == 200 and search.json()[0]["document_id"] == document.json()["id"]
                print(json.dumps({"mode": "offline-deterministic", "analysis": proposal["status"],
                    "affected_orders": [o["id"] for o in proposal["affected_orders"]],
                    "citations": [c["document_id"] for c in proposal["citations"]],
                    "approval_http": first.status_code, "replay_http": replay.status_code,
                    "same_ticket": True, "foreign_shipment_http": 404,
                    "history_and_tickets": "passed", "document_create_and_search": "passed"}, indent=2))
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            process.stderr.close()


if __name__ == "__main__":
    main()
