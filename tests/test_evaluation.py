import unittest

from evaluate import load_cases, run


class EvaluationChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = load_cases()
        cls.report = run(cls.cases)

    def test_frozen_size_split_and_family_isolation(self):
        self.assertEqual(len(self.cases), 60)
        self.assertEqual(sum(c["split"] == "development" for c in self.cases), 40)
        self.assertEqual(sum(c["split"] == "holdout" for c in self.cases), 20)
        for family in {c["family"] for c in self.cases}:
            self.assertEqual(len({c["split"] for c in self.cases if c["family"] == family}), 1)

    def test_all_gold_assertions_and_no_false_authorizations(self):
        self.assertEqual(self.report["overall"]["passed"], 60,
                         [r["id"] for r in self.report["results"] if not r["passed"]])
        self.assertEqual(self.report["overall"]["false_authorizations"], 0)
        self.assertEqual(self.report["splits"]["holdout"]["case_accuracy"], 1)

    def test_report_is_explicitly_offline_and_synthetic(self):
        self.assertEqual(self.report["provider"], "offline-deterministic")
        self.assertTrue(self.report["synthetic"])
        self.assertEqual(self.report["paid_api_calls"], 0)


if __name__ == "__main__":
    unittest.main()
