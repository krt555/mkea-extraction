"""Context selection for long papers.

Most mesoporous-materials papers fit in a single request.  For long PDFs the
builder keeps the title/abstract, synthesis paragraphs and property paragraphs
(BET, pore size, XRD/TEM, tables) up to a character budget.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .pdf import PaperText

SYNTHESIS_TERMS = {
    "synthesis",
    "synthesized",
    "prepared",
    "preparation",
    "experimental",
    "dissolved",
    "stirred",
    "aged",
    "ageing",
    "aging",
    "calcined",
    "calcination",
    "hydrothermal",
    "autoclave",
    "teflon",
    "gel",
    "mixture",
    "solution",
    "surfactant",
    "copolymer",
    "pluronic",
    "teos",
    "silica source",
    "ph",
    "hcl",
    "naoh",
    "salt",
}
PROPERTY_TERMS = {
    "bet",
    "surface area",
    "pore size",
    "pore diameter",
    "pore volume",
    "adsorption",
    "desorption",
    "isotherm",
    "xrd",
    "saxs",
    "tem",
    "sem",
    "diameter",
    "mesopore",
    "table",
}


@dataclass
class PaperContext:
    text: str
    pages: list[int]
    chars: int
    truncated: bool

    def stats(self) -> dict[str, object]:
        return {
            "chars": self.chars,
            "pages_included": self.pages,
            "truncated": self.truncated,
        }


def build_context(paper: PaperText, *, max_chars: int = 200000) -> PaperContext:
    full = paper.full_text
    if len(full) <= max_chars:
        return PaperContext(
            text=_render_all(paper),
            pages=list(range(1, len(paper.pages) + 1)),
            chars=len(full),
            truncated=False,
        )

    blocks = _blocks(paper)
    scored: list[tuple[float, int, int]] = []
    for order, (page, text) in enumerate(blocks):
        score = _score(text, page, len(paper.pages))
        scored.append((score, order, page))
    selected: set[int] = set()
    budget = max_chars
    # Keep the opening blocks (title/abstract) even if scores are neutral.
    for order, (_, text) in enumerate(blocks[:4]):
        if len(text) + 20 <= budget:
            selected.add(order)
            budget -= len(text) + 20
    for _, order, _ in sorted(scored, reverse=True):
        if order in selected:
            continue
        length = len(blocks[order][1]) + 20
        if length > budget:
            continue
        selected.add(order)
        budget -= length
        if budget <= 2000:
            break

    rendered = _render_selected(paper, blocks, selected)
    return PaperContext(
        text=rendered,
        pages=sorted({page for order, (page, _) in enumerate(blocks) if order in selected}),
        chars=len(rendered),
        truncated=True,
    )


def _blocks(paper: PaperText) -> list[tuple[int, str]]:
    blocks: list[tuple[int, str]] = []
    for page_no, _ in enumerate(paper.pages, 1):
        for block in re.split(r"\n\s*\n", paper.page_with_tables(page_no)):
            text = block.strip()
            if len(text) >= 40:
                blocks.append((page_no, text))
    return blocks


def _score(text: str, page: int, n_pages: int) -> float:
    lowered = text.lower()
    score = 0.0
    if page == 1:
        score += 4.0
    if page == n_pages:
        score += 1.0
    score += sum(2.0 for term in SYNTHESIS_TERMS if term in lowered)
    score += sum(1.2 for term in PROPERTY_TERMS if term in lowered)
    if re.search(r"\b(table|figure)\s+\d", lowered):
        score += 1.5
    if re.search(r"\b(\d+(?:\.\d+)?)\s*(?:å|nm|m2/g|m²/g|wt%|w%)\b", lowered):
        score += 1.0
    return score


def _render_all(paper: PaperText) -> str:
    chunks = []
    for page_no, page in enumerate(paper.pages, 1):
        chunks.append(f"[[PAGE {page_no}]]\n{paper.page_with_tables(page_no).strip()}")
    return "\n\n".join(chunks)


def _render_selected(
    paper: PaperText, blocks: list[tuple[int, str]], selected: set[int]
) -> str:
    chunks: list[str] = []
    current_page = None
    for order, (page, text) in enumerate(blocks):
        if order not in selected:
            continue
        if page != current_page:
            chunks.append(f"[[PAGE {page}]]")
            current_page = page
        chunks.append(text)
    if not chunks:
        return _render_all(paper)
    return "\n\n".join(chunks)
