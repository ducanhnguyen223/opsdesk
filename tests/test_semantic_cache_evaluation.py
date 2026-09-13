import unittest

from evaluate_semantic_cache import select_threshold, split_metrics


class SemanticCacheEvaluationChecks(unittest.TestCase):
    def test_threshold_uses_development_and_fails_closed_on_scope(self):
        rows = [
            {"split":"development","same_answer":True,"scope_match":True,"similarity":0.8},
            {"split":"development","same_answer":False,"scope_match":True,"similarity":0.7},
            {"split":"holdout","same_answer":False,"scope_match":True,"similarity":0.9},
            {"split":"holdout","same_answer":False,"scope_match":False,"similarity":1.0},
        ]
        threshold = select_threshold(rows)
        self.assertEqual(threshold, 0.8)
        self.assertEqual(split_metrics(rows, "holdout", threshold)["false_hits"], 1)
        self.assertEqual(split_metrics(rows, "holdout", threshold)["scope_blocked"], 1)


if __name__ == "__main__":
    unittest.main()
