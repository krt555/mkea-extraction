"""Tests for context selection and LLM response parsing."""

from __future__ import annotations

from pathlib import Path
from unittest import TestCase

from mkea_extract.context import build_context
from mkea_extract.llm import extract_json
from mkea_extract.pdf import PaperText


class ContextTests(TestCase):
    def test_short_paper_is_complete(self):
        paper = PaperText(paper_id=1, path=Path("papers/paper_001.pdf"), pages=["title", "body"])
        context = build_context(paper, max_chars=1000)
        self.assertFalse(context.truncated)
        self.assertIn("[[PAGE 1]]", context.text)
        self.assertIn("[[PAGE 2]]", context.text)

    def test_long_paper_keeps_synthesis(self):
        filler = "\n\n".join(f"Filler paragraph {i}. " + "x" * 120 for i in range(20))
        paper = PaperText(
            paper_id=1,
            path=Path("papers/paper_001.pdf"),
            pages=[
                "Title and abstract.\n\n" + filler,
                "Experimental: TEOS and Pluronic P123 were stirred for 24 h.",
                "Characterization: " + "z" * 2000,
            ],
        )
        context = build_context(paper, max_chars=1200)
        self.assertTrue(context.truncated)
        self.assertIn("TEOS", context.text)


class LLMTests(TestCase):
    def test_fenced_json(self):
        data = extract_json('```json\n{"records": []}\n```')
        self.assertEqual(data, {"records": []})

    def test_json_with_leading_text(self):
        data = extract_json('Here is the answer:\n{"ok": true}')
        self.assertEqual(data, {"ok": True})

    def test_invalid(self):
        self.assertIsNone(extract_json("not json"))

    def test_top_level_array(self):
        data = extract_json('[{"fields": {"salt": null}}]')
        self.assertEqual(data, {"records": [{"fields": {"salt": None}}]})
