"""Check G1 data contracts; these are NOT application acceptance tests."""
import json
import unittest
from datetime import datetime
from pathlib import Path

DATA = json.loads((Path(__file__).resolve().parents[1] / "fixtures/baseline.json").read_text())


def instant(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class FixtureContracts(unittest.TestCase):
    def test_counts_and_unique_ids(self):
        self.assertTrue(DATA["synthetic"])
        for table, count in {"tenants": 2, "actors": 4, "shipments": 12,
                             "orders": 24, "documents": 8, "cases": 20}.items():
            rows = DATA[table]
            self.assertEqual(len(rows), count, table)
            self.assertEqual(len({row["id"] for row in rows}), count, table)

    def test_order_and_identity_scope(self):
        tenants = {row["id"] for row in DATA["tenants"]}
        shipments = {row["id"]: row for row in DATA["shipments"]}
        for table in ("actors", "shipments", "orders", "documents"):
            for row in DATA[table]:
                self.assertIn(row["tenant_id"], tenants)
        for order in DATA["orders"]:
            shipment = shipments[order["shipment_id"]]
            self.assertEqual(order["tenant_id"], shipment["tenant_id"])
            self.assertEqual(order["customer_id"], shipment["customer_id"])

    def test_ready_labels_against_business_data(self):
        actors = {row["id"]: row for row in DATA["actors"]}
        docs = {row["id"]: row for row in DATA["documents"]}
        shipments = {row["id"]: row for row in DATA["shipments"]}
        now = instant(DATA["as_of"])
        for case in DATA["cases"]:
            expected = case["expected"]
            if expected.get("status") != "ready_for_review":
                continue
            actor = actors[case["actor"]]
            shipment = shipments[expected["shipment_id"]]
            self.assertEqual(shipment["tenant_id"], actor["tenant_id"], case["id"])
            self.assertEqual(shipment["status"], "delayed")
            actual_orders = sorted(o["id"] for o in DATA["orders"]
                                   if o["shipment_id"] == shipment["id"] and o["status"] == "active")
            self.assertEqual(actual_orders, expected["affected_order_ids"])
            self.assertEqual(expected["tickets_created"], 0)
            for citation in expected["citation_ids"]:
                doc = docs[citation]
                self.assertEqual(doc["tenant_id"], actor["tenant_id"])
                self.assertEqual(doc["policy_scope"], shipment["policy_scope"])
                self.assertEqual(doc["kind"], "procedure")
                self.assertIn(actor["role"], doc["allowed_roles"])
                self.assertLessEqual(instant(doc["effective_from"]), now)
                if doc["effective_to"]:
                    self.assertLess(now, instant(doc["effective_to"]))
                self.assertEqual(doc["action"], expected["action"])

    def test_expired_conflicting_and_untrusted_documents(self):
        docs = {row["id"]: row for row in DATA["documents"]}
        now = instant(DATA["as_of"])
        self.assertLessEqual(instant(docs["A-STANDARD-V1"]["effective_to"]), now)
        conflict = [docs[name] for name in ("A-CONFLICT-V1", "A-CONFLICT-V2")]
        self.assertNotEqual(conflict[0]["action"], conflict[1]["action"])
        self.assertEqual(conflict[0]["policy_scope"], conflict[1]["policy_scope"])
        for doc in conflict:
            self.assertLessEqual(instant(doc["effective_from"]), now)
            self.assertIsNone(doc["effective_to"])
        self.assertEqual(docs["A-UNTRUSTED-NOTE"]["kind"], "untrusted_note")
        self.assertIsNone(docs["A-UNTRUSTED-NOTE"]["action"])

    def test_scenario_coverage_and_setup(self):
        cases = {row["id"]: row for row in DATA["cases"]}
        actors = {row["id"] for row in DATA["actors"]}
        categories = {row["category"] for row in DATA["cases"]}
        self.assertTrue({"cross_tenant", "conflict", "missing_policy", "stale_notice",
                         "untrusted_document", "dependency_failure", "approval_permission",
                         "idempotency", "stale_approval", "key_conflict"} <= categories)
        for case in cases.values():
            self.assertEqual(case["split"], "development")
            self.assertTrue(case["reason"] and case["message"])
            self.assertTrue(case["actor"] is None or case["actor"] in actors)
            if "setup_case" in case:
                self.assertEqual(cases[case["setup_case"]]["expected"]["status"], "ready_for_review")
        self.assertEqual(cases["C08"]["expected"]["http"], cases["C07"]["expected"]["http"])
        self.assertEqual(cases["C18"]["expected"]["tickets_created"], 1)


if __name__ == "__main__":
    unittest.main()
