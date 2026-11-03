"""Two-column PDF reading-order tests.

`get_text("text", sort=True)` sorts blocks purely by vertical position, which
on a two-column paper interleaves the left and right columns into the same
line.  These tests build a synthetic two-column PDF and assert that each
column is read to its end before the next one starts.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT, ROOT / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

try:
    import pymupdf
except ImportError:  # pragma: no cover - PyMuPDF is a hard dependency
    pymupdf = None

from mkea_extract.pdf import PDFLoader, _column_split_x, _page_text_columns

LEFT_LINES = [
    "LEFTA the assembly was carried out in acidic medium",
    "LEFTB using Pluronic P123 as the structure directing",
    "LEFTC agent and TEOS as the silica source at 35 C",
    "LEFTD followed by aging for twenty hours in a bottle",
]
RIGHT_LINES = [
    "RIGHTA the calcined material gave a type IV isotherm",
    "RIGHTB with a BET surface area of 820 m2/g and a",
    "RIGHTC narrow pore size distribution centred at 7.8 nm",
    "RIGHTD as measured on the adsorption branch by BJH",
]


def build_two_column_pdf(target: Path) -> Path:
    """Build a PDF whose two columns are genuinely separate text blocks.

    Each line is its own text box, and the right column is offset vertically:
    two boxes sharing the same baseline would be merged by PyMuPDF into a
    single full-width block, which is not how a real two-column paper is laid
    out and would make the fixture untestable.
    """
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((60, 60), "A Two Column Paper About Mesoporous Silica", fontsize=12)

    y = 100
    for line in LEFT_LINES:
        page.insert_textbox(pymupdf.Rect(60, y, 285, y + 40), line, fontsize=9)
        y += 46

    y = 118  # offset so the columns never share a baseline
    for line in RIGHT_LINES:
        page.insert_textbox(pymupdf.Rect(325, y, 550, y + 40), line, fontsize=9)
        y += 46

    doc.save(target)
    doc.close()
    return target


@unittest.skipIf(pymupdf is None, "PyMuPDF not available")
class TwoColumnLayoutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # Fixtures live in the session scratch area; the runtime reclaims it,
        # so the test itself never deletes files.  The pid suffix keeps
        # repeated runs from fighting over a locked file on Windows.
        cls.work = ROOT / ".box-agent-scratch" / f"pdf-columns-{os.getpid()}"
        cls.work.mkdir(parents=True, exist_ok=True)
        cls.pdf = build_two_column_pdf(cls.work / "paper_801.pdf")

    def test_gutter_is_detected(self):
        doc = pymupdf.open(self.pdf)
        try:
            page = doc[0]
            split = _column_split_x(page.rect.width, page.get_text("blocks"))
        finally:
            doc.close()
        self.assertIsNotNone(split, "两栏版式未被识别")
        # The gutter sits between the left column end and the right column start.
        self.assertGreater(split, 240)
        self.assertLess(split, 330)

    def test_naive_sort_interleaves_columns(self):
        """Document the bug this feature fixes."""
        doc = pymupdf.open(self.pdf)
        try:
            naive = " ".join(doc[0].get_text("text", sort=True).split())
        finally:
            doc.close()
        # The first right-column line appears before the last left-column line.
        self.assertLess(naive.find("RIGHTA"), naive.find("LEFTD"))

    def test_columns_are_read_in_order(self):
        doc = pymupdf.open(self.pdf)
        try:
            text = " ".join(_page_text_columns(doc[0]).split())
        finally:
            doc.close()

        positions = [text.find(tag) for tag in
                     ("LEFTA", "LEFTB", "LEFTC", "LEFTD",
                      "RIGHTA", "RIGHTB", "RIGHTC", "RIGHTD")]
        self.assertNotIn(-1, positions, "有行丢失")
        # Whole left column first, then the whole right column, each in order.
        self.assertEqual(positions, sorted(positions))

    def test_sentences_stay_intact(self):
        doc = pymupdf.open(self.pdf)
        try:
            text = " ".join(_page_text_columns(doc[0]).split())
        finally:
            doc.close()
        self.assertIn("BET surface area of 820 m2/g", text)
        self.assertIn("Pluronic P123 as the structure directing", text)

    def test_loader_prefers_column_aware_text(self):
        """The loader must not fall back to pdftotext's visual layout."""
        loader = PDFLoader(use_cache=False, ocr=False)
        paper = loader.load(self.pdf)
        flat = " ".join(paper.pages[0].split())
        self.assertLess(flat.find("LEFTD"), flat.find("RIGHTA"))

    def test_single_column_page_is_unchanged(self):
        single = self.work / "paper_802.pdf"
        doc = pymupdf.open()
        page = doc.new_page(width=595, height=842)
        y = 70
        for line in LEFT_LINES + RIGHT_LINES:
            page.insert_textbox(pymupdf.Rect(60, y, 530, y + 22), line, fontsize=9)
            y += 30
        doc.save(single)
        doc.close()

        doc = pymupdf.open(single)
        try:
            split = _column_split_x(doc[0].rect.width, doc[0].get_text("blocks"))
            text = " ".join(_page_text_columns(doc[0]).split())
        finally:
            doc.close()
        self.assertIsNone(split, "单栏页被误判为双栏")
        self.assertLess(text.find("LEFTA"), text.find("RIGHTD"))


if __name__ == "__main__":
    unittest.main()
