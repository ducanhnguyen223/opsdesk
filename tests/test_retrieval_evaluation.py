import unittest

from evaluate_retrieval import rrf


class RetrievalEvaluationChecks(unittest.TestCase):
    def test_rrf_is_deterministic_and_combines_both_rankings(self):
        self.assertEqual(rrf(["lexical", "shared"], ["semantic", "shared"]),
                         ["shared", "lexical", "semantic"])


if __name__ == "__main__":
    unittest.main()
