"""Reproducible offline evaluation over frozen synthetic gold cases."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import tempfile
import time

from fastapi.testclient import TestClient

from app import create_app
from backend import fake_provider, instant

ROOT = Path(__file__).resolve().parent
CASES = ROOT / "fixtures/evaluation.jsonl"
NOW = instant(json.loads((ROOT / "fixtures/baseline.json").read_text())["as_of"])


def load_cases(path=CASES):
    cases = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    ids = [case["id"] for case in cases]
    if len(cases) != 60 or len(set(ids)) != 60:
        raise ValueError("Evaluation must contain exactly 60 uniquely identified cases")
    splits = Counter(case["split"] for case in cases)
    if splits != {"development": 40, "holdout": 20}:
        raise ValueError("Evaluation split must be 40 development / 20 holdout")
    family_splits = defaultdict(set)
    for case in cases:
        family_splits[case["family"]].add(case["split"])
    if any(len(values) != 1 for values in family_splits.values()):
        raise ValueError("A scenario family cannot cross evaluation splits")
    return cases


def execute(case, directory):
    app = create_app(Path(directory) / f"{case['id']}.sqlite3", demo_login=True,
                     provider=fake_provider, clock=lambda: NOW)
    with TestClient(app) as client:
        if case["actor"]:
            assert client.post("/api/demo/login", json={"actor_id": case["actor"]}).status_code == 200
        started = time.perf_counter()
        response = client.post("/api/analyses", json={"message": case["message"]})
        latency_ms = (time.perf_counter() - started) * 1000
    expected, body = case["expected"], response.json()
    actual = {"http": response.status_code}
    if response.status_code == 200:
        facts = {fact["field"]: fact["value"] for fact in body["verified_facts"]}
        actual.update(status=body["status"], shipment_id=facts.get("id"),
            orders=[order["id"] for order in body["affected_orders"]],
            action=body["recommended_actions"][0]["action"] if body["recommended_actions"] else None,
            citations=[citation["document_id"] for citation in body["citations"]])
    checks = {"http": actual["http"] == expected["http"]}
    for key in ("status", "shipment_id", "orders", "action", "citations"):
        if key in expected:
            checks[key] = actual.get(key) == expected[key]
    return {"id": case["id"], "split": case["split"], "family": case["family"],
            "passed": all(checks.values()), "checks": checks, "expected": expected,
            "actual": actual, "latency_ms": round(latency_ms, 3)}


def rate(results, check):
    selected = [result["checks"][check] for result in results if check in result["checks"]]
    return round(sum(selected) / len(selected), 4) if selected else None


def summarize(results):
    latencies = sorted(result["latency_ms"] for result in results)
    return {"cases": len(results), "passed": sum(result["passed"] for result in results),
        "case_accuracy": round(sum(result["passed"] for result in results) / len(results), 4),
        "http_accuracy": rate(results, "http"), "status_accuracy": rate(results, "status"),
        "action_accuracy": rate(results, "action"), "citation_exact_match": rate(results, "citations"),
        "affected_orders_exact_match": rate(results, "orders"),
        "false_authorizations": sum(result["actual"].get("status") == "ready_for_review"
            and result["expected"].get("status") != "ready_for_review" for result in results),
        "latency_ms": {"p50": round(latencies[(len(latencies) - 1) // 2], 3),
                       "p95": round(latencies[math.ceil(len(latencies) * .95) - 1], 3)}}


def run(cases=None):
    cases = cases or load_cases()
    with tempfile.TemporaryDirectory(prefix="opsdesk-eval-") as directory:
        results = [execute(case, directory) for case in cases]
    report = {"generated_at": datetime.now(timezone.utc).isoformat(),
        "fixture_as_of": NOW.isoformat(), "provider": "offline-deterministic",
        "synthetic": True, "paid_api_calls": 0,
        "method": "Fresh SQLite database per case; exact gold assertions; latency excludes app setup.",
        "overall": summarize(results),
        "splits": {split: summarize([r for r in results if r["split"] == split])
                   for split in ("development", "holdout")},
        "families": {family: summarize([r for r in results if r["family"] == family])
                     for family in sorted({r["family"] for r in results})},
        "results": results}
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/evaluation_offline.json")
    args = parser.parse_args()
    report = run()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"output": str(args.output), **report["overall"]}, ensure_ascii=False))
    raise SystemExit(0 if report["overall"]["passed"] == report["overall"]["cases"] else 1)


if __name__ == "__main__":
    main()
