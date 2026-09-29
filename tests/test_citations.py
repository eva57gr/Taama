"""Citation integrity: the rule bank refuses to load unless every excerpt is in its snapshot.

Run from the repo root: python tests/test_citations.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from models import Citation
from rulebank import CitationError, RuleBank


class CitationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rb = RuleBank()

    def test_every_rule_citation_is_verbatim(self):
        self.assertGreater(len(self.rb.rules), 0)
        empty = []
        for rule in self.rb.rules.values():
            if not rule.citations:
                empty.append(rule.id)
                continue
            for citation in rule.citations:
                self.rb.verify(citation)
        # NEEDS-REVIEW is the "no matching rule" outcome. It has no excerpt on purpose:
        # there is no regulatory sentence to quote, and the verdict stays amber.
        self.assertEqual(empty, ["NEEDS-REVIEW"])

    def test_concepts_resolve_to_real_rows(self):
        # Loading concepts checks indications, Schedule 4 rows and notified effects.
        self.assertGreater(len(self.rb.concepts), 0)
        self.assertIn("energy", self.rb.concepts)
        self.assertTrue(self.rb.notified("fsanz_notified_arepa"))

    def test_invented_excerpt_is_rejected(self):
        with self.assertRaises(CitationError):
            self.rb.verify(Citation("fsc_std_1_2_7", "nowhere", "this sentence is not in the snapshot"))


if __name__ == "__main__":
    unittest.main()
