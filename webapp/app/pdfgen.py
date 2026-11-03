"""Turn the built-in demo corpus into real PDF files.

Rendering the demo text into actual PDFs (instead of injecting rows straight
into the database) means the demo exercises the identical code path as a user
upload: PyMuPDF parsing, page storage, extraction and evidence grounding.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pymupdf

MARGIN = 54
FONT_SIZE = 9.5
LINE_HEIGHT = 13.0
WRAP_COLS = 92


def render_demo_pdf(pages: list[str], target: Path) -> Path:
    """Write one PDF whose page N contains the Nth text block."""
    target.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    try:
        for block in pages:
            page = doc.new_page(width=595, height=842)  # A4
            y = MARGIN
            for raw_line in block.splitlines():
                if not raw_line.strip():
                    y += LINE_HEIGHT * 0.6
                    continue
                for line in textwrap.wrap(raw_line.strip(), WRAP_COLS) or [""]:
                    if y > 842 - MARGIN:
                        break
                    page.insert_text(
                        (MARGIN, y),
                        line,
                        fontsize=FONT_SIZE,
                        fontname="helv",
                    )
                    y += LINE_HEIGHT
        doc.save(target)
    finally:
        doc.close()
    return target
