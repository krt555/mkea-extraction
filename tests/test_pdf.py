"""Tests for PDF text helpers and quote grounding."""

from __future__ import annotations

from pathlib import Path
from unittest import TestCase

from mkea_extract.pdf import PaperTable, PaperText, ground_quote, paper_id_from_path


class PDFTests(TestCase):
    def test_paper_id(self):
        self.assertEqual(paper_id_from_path("papers/paper_001.pdf"), 1)
        self.assertEqual(paper_id_from_path("paper_100.PDF"), 100)

    def test_ground_quote(self):
        pages = [
            "The synthesis used Pluronic P123 (1.92 g) and 11.24 g of NaI.",
            "The BET surface area was 780 m2/g.",
        ]
        grounded, matched = ground_quote(pages, "Pluronic P123 (1.92 g)")
        self.assertTrue(grounded)
        self.assertEqual(matched, [1])
        grounded, matched = ground_quote(pages, "BET surface area was 780 m2/g", page=2)
        self.assertTrue(grounded)
        self.assertEqual(matched, [2])
        self.assertFalse(ground_quote(pages, "not present")[0])

    def test_multiline_hyphenation(self):
        pages = ["A Plu-\nronic P123 sample."]
        grounded, _ = ground_quote(pages, "A Pluronic P123 sample")
        self.assertTrue(grounded)

    def test_paper_text_contract(self):
        paper = PaperText(paper_id=7, path=Path("papers/paper_007.pdf"), pages=["a", "b"])
        self.assertEqual(paper.paper_key, "paper_007")
        self.assertEqual(paper.page_text(2), "b")

    def test_tables_are_attached_to_pages(self):
        table = PaperTable(
            page=1,
            index=1,
            rows=[["Sample", "BET"], ["S1", "720"]],
        )
        paper = PaperText(
            paper_id=7,
            path=Path("papers/paper_007.pdf"),
            pages=["Synthesis text.", "Characterization text."],
            tables=[table],
        )
        self.assertIn("[[TABLE 1.1]]", paper.page_with_tables(1))
        self.assertIn("| S1 | 720 |", paper.full_text)
        self.assertEqual(paper.find_table("1.1"), table)
        self.assertEqual(paper.find_table("1.2"), None)
        self.assertEqual(paper.tables_for_page(1), [table])
