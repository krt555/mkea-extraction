"""Tests for deterministic agent tools."""

from __future__ import annotations

from pathlib import Path
from unittest import TestCase

from mkea_extract.pdf import PaperTable, PaperText
from mkea_extract.tools import ToolBox, ToolError, safe_eval


def sample_paper() -> PaperText:
    return PaperText(
        paper_id=1,
        path=Path("papers/paper_001.pdf"),
        pages=[
            "Synthesis: Pluronic P123 was dissolved in water.\n\nThe BET surface area was 720 m2/g.",
            "The pore size was 4.3 nm and the sample was calcined at 550 °C.",
        ],
        tables=[
            PaperTable(
                page=1,
                index=1,
                rows=[["Sample", "BET"], ["S1", "720"], ["S2", "680"]],
            )
        ],
    )


class ToolTests(TestCase):
    def test_pages_and_tables(self):
        box = ToolBox(sample_paper())
        pages = box.list_pages()
        self.assertEqual([item["page"] for item in pages], [1, 2])
        self.assertEqual(pages[0]["tables"], ["1.1"])
        tables = box.list_tables()
        self.assertEqual(tables[0]["table_id"], "1.1")
        self.assertEqual(tables[0]["rows"], 3)
        table = box.read_table("1.1")
        self.assertIn("| S1 | 720 |", table["text"])
        with self.assertRaises(ToolError):
            box.read_table("9.9")

    def test_search_and_read_page(self):
        box = ToolBox(sample_paper())
        hits = box.search_pages("surface area", top_k=2)
        self.assertTrue(hits)
        self.assertEqual(hits[0]["page"], 1)
        page = box.read_page(2)
        self.assertIn("pore size", page["text"])
        self.assertIn("[[TABLE 1.1]]", box.read_page(1)["text"])
        with self.assertRaises(ToolError):
            box.read_page(99)

    def test_verify_evidence(self):
        box = ToolBox(sample_paper())
        result = box.verify_evidence(
            records=[
                {
                    "label": "S1",
                    "evidence": {
                        "surface_area": [{"page": 1, "quote": "BET surface area was 720 m2/g"}],
                        "surfactant": [{"page": 2, "quote": "this quote is not on page two"}],
                    },
                }
            ]
        )
        self.assertFalse(result["ok"])
        self.assertEqual(len(result["ungrounded"]), 1)
        self.assertEqual(result["ungrounded"][0]["field"], "surfactant")

    def test_calculator(self):
        value = safe_eval("(8.54 / 141.39) * 100")
        self.assertAlmostEqual(value, 6.04, places=2)
        self.assertAlmostEqual(safe_eval("sqrt(9) + pi"), 6.141593, places=5)
        with self.assertRaises(ToolError):
            safe_eval("__import__('os').system('echo bad')")
        with self.assertRaises(ToolError):
            safe_eval("2 ** 10000")
