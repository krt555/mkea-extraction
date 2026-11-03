"""PDF text and table extraction, OCR fallback, and quote grounding.

PyMuPDF is the primary parser: it preserves layout reasonably well and can
recover bordered/whitespace tables that plain text extraction loses.  The
loader still falls back to ``pdftotext``, ``pdftoppm`` and ``tesseract`` when a
PDF is scanned or PyMuPDF is unavailable.  Text, tables and OCR results are
cached under ``data/cache/extraction``.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

try:  # optional at import time; a clear error is raised only when needed
    import pymupdf
except ImportError:  # pragma: no cover - exercised only without the optional dep
    pymupdf = None

if pymupdf is not None:
    pymupdf.no_recommend_layout()

PARSER_VERSION = "pymupdf-3-columns"


class PDFError(RuntimeError):
    """Raised when a PDF cannot be read or contains no usable text."""


@dataclass
class PaperTable:
    page: int
    index: int
    rows: list[list[str]] = field(default_factory=list)

    @property
    def table_id(self) -> str:
        return f"{self.page}.{self.index}"

    @property
    def n_rows(self) -> int:
        return len(self.rows)

    @property
    def n_cols(self) -> int:
        return max((len(row) for row in self.rows), default=0)

    def text(self, *, max_rows: int = 60) -> str:
        rows = self.rows[:max_rows]
        if not rows:
            return ""
        width = max(len(row) for row in rows)
        lines = []
        for row in rows:
            cells = [str(cell or "").replace("|", "/") for cell in row]
            cells += [""] * (width - len(cells))
            lines.append("| " + " | ".join(cells) + " |")
        return "\n".join(lines)

    def as_dict(self) -> dict[str, object]:
        return {"page": self.page, "index": self.index, "rows": self.rows}

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "PaperTable":
        return cls(
            page=int(payload["page"]),
            index=int(payload["index"]),
            rows=[[str(cell or "") for cell in row] for row in payload.get("rows", [])],
        )


@dataclass
class PaperText:
    paper_id: int
    path: Path
    pages: list[str] = field(default_factory=list)
    ocr_pages: list[int] = field(default_factory=list)
    tables: list[PaperTable] = field(default_factory=list)

    @property
    def paper_key(self) -> str:
        return f"paper_{self.paper_id:03d}"

    @property
    def full_text(self) -> str:
        return "\n\n".join(self.page_with_tables(page) for page in range(1, len(self.pages) + 1))

    def page_text(self, page: int) -> str:
        if 1 <= page <= len(self.pages):
            return self.pages[page - 1]
        return ""

    def page_with_tables(self, page: int) -> str:
        text = self.page_text(page)
        page_tables = [table for table in self.tables if table.page == page]
        if not page_tables:
            return text
        blocks = [text.rstrip()] if text.strip() else []
        blocks.extend(f"[[TABLE {table.table_id}]]\n{table.text()}" for table in page_tables)
        return "\n\n".join(blocks)

    @property
    def pages_with_tables(self) -> list[str]:
        return [
            self.page_with_tables(page) for page in range(1, len(self.pages) + 1)
        ]

    def tables_for_page(self, page: int) -> list[PaperTable]:
        return [table for table in self.tables if table.page == page]

    def find_table(self, table_id: str | int) -> PaperTable | None:
        wanted = str(table_id)
        return next((table for table in self.tables if table.table_id == wanted), None)

    def stats(self) -> dict[str, object]:
        return {
            "paper_id": self.paper_id,
            "pdf_path": str(self.path),
            "pages": len(self.pages),
            "chars": len(self.full_text),
            "tables": len(self.tables),
            "ocr_pages": self.ocr_pages,
        }


def paper_id_from_path(path: str | Path) -> int:
    match = re.search(r"paper[_-]?(\d+)", Path(path).stem, flags=re.I)
    if not match:
        raise PDFError(f"cannot infer paper id from filename: {Path(path).name}")
    return int(match.group(1))


def _looks_scanned(pages: list[str]) -> bool:
    return sum(len(page.strip()) for page in pages) < 200


class PDFLoader:
    def __init__(
        self,
        cache_dir: str | Path | None = Path("data/cache/extraction/pdf_text"),
        *,
        use_cache: bool = True,
        ocr: bool = True,
    ):
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.use_cache = use_cache
        self.ocr = ocr
        if self.cache_dir and self.use_cache:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    def load(self, path: str | Path) -> PaperText:
        path = Path(path)
        if not path.exists():
            raise PDFError(f"PDF not found: {path}")
        paper_id = paper_id_from_path(path)
        cache_path = self._cache_path(path, paper_id)
        if cache_path and cache_path.exists():
            try:
                payload = json.loads(cache_path.read_text(encoding="utf-8"))
                stat = path.stat()
                if (
                    payload.get("parser") == PARSER_VERSION
                    and payload.get("mtime") == stat.st_mtime
                    and payload.get("size") == stat.st_size
                ):
                    return PaperText(
                        paper_id=paper_id,
                        path=path,
                        pages=payload["pages"],
                        ocr_pages=payload.get("ocr_pages", []),
                        tables=[PaperTable.from_dict(item) for item in payload.get("tables", [])],
                    )
            except (OSError, KeyError, json.JSONDecodeError, TypeError, ValueError):
                pass

        pages: list[str] = []
        tables: list[PaperTable] = []
        pymupdf_pages: list[str] = []
        pymupdf_tables: list[PaperTable] = []
        if pymupdf is not None:
            try:
                pymupdf_pages, pymupdf_tables = _pymupdf_extract(path)
            except Exception as exc:  # PyMuPDF can fail on odd/corrupt files
                _ = exc
        pdftext_pages = _pdftotext_safe(path)
        # PyMuPDF 是首选：它给出块坐标，我们据此按栏重排，双栏论文才不会错乱。
        # `pdftotext -layout` 会按视觉布局把左右栏并排输出，同一行混进两栏文字，
        # 因此只在 PyMuPDF 明显抽不出内容时才用它兜底，不再按字符总量比较
        # （布局版的空格填充会让字符数虚高，反而总是胜出）。
        if pymupdf_pages and _text_size(pymupdf_pages) >= 2000:
            pages, tables = pymupdf_pages, pymupdf_tables
        elif pymupdf_pages and _text_size(pymupdf_pages) >= 0.6 * _text_size(pdftext_pages):
            pages, tables = pymupdf_pages, pymupdf_tables
        elif pdftext_pages:
            pages, tables = pdftext_pages, []
        elif pymupdf_pages:
            pages, tables = pymupdf_pages, pymupdf_tables
        else:
            pages = _pdftotext(path)
            tables = []

        ocr_pages: list[int] = []
        if self.ocr and _looks_scanned(pages):
            pages = _ocr_pdf(path)
            tables = []
            ocr_pages = list(range(1, len(pages) + 1))
        elif self.ocr:
            pages, ocr_pages = _fill_empty_pages(path, pages)

        if not pages or _looks_scanned(pages):
            raise PDFError(
                f"no usable text in {path}; install poppler/tesseract or pass --no-ocr to inspect"
            )
        result = PaperText(
            paper_id=paper_id, path=path, pages=pages, ocr_pages=ocr_pages, tables=tables
        )
        if cache_path:
            stat = path.stat()
            cache_path.write_text(
                json.dumps(
                    {
                        "pdf": str(path.resolve()),
                        "parser": PARSER_VERSION,
                        "mtime": stat.st_mtime,
                        "size": stat.st_size,
                        "pages": pages,
                        "ocr_pages": ocr_pages,
                        "tables": [table.as_dict() for table in tables],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        return result

    def _cache_path(self, path: Path, paper_id: int) -> Path | None:
        if not self.cache_dir or not self.use_cache:
            return None
        key = hashlib.sha256(
            f"{path.resolve()}:{int(self.ocr)}:{PARSER_VERSION}".encode()
        ).hexdigest()[:16]
        return self.cache_dir / f"{paper_id:03d}-{key}.json"


def _column_split_x(page_width: float, blocks: list[tuple]) -> float | None:
    """Find the vertical gutter of a two-column layout.

    ``get_text("text", sort=True)`` sorts purely by vertical position, so on a
    two-column paper it interleaves the left and right columns into the same
    line.  Detecting the gutter lets us read each column to its end first,
    which is how a human reads the page.

    Returns the gutter x coordinate, or None for single-column pages.
    """
    text_blocks = [b for b in blocks if len(b) > 6 and b[6] == 0 and str(b[4]).strip()]
    if len(text_blocks) < 6:
        return None

    # Probe the middle of the page for an x that almost no block crosses.
    best: tuple[int, float, float] | None = None
    for step in range(35, 66):
        x = page_width * step / 100
        crossing = sum(1 for b in text_blocks if b[0] < x - 2 and b[2] > x + 2)
        candidate = (crossing, abs(step - 50) / 100, x)
        if best is None or candidate < best:
            best = candidate
    if best is None:
        return None

    crossing, _, split = best
    # Tolerate a few genuinely full-width blocks (titles, wide tables/figures).
    if crossing > max(1, int(len(text_blocks) * 0.12)):
        return None
    left = sum(1 for b in text_blocks if b[2] <= split)
    right = sum(1 for b in text_blocks if b[0] >= split)
    if left < 2 or right < 2:
        return None
    return split


def _page_text_columns(page) -> str:
    """Extract page text in reading order, column by column."""
    try:
        blocks = page.get_text("blocks")
    except Exception:
        return page.get_text("text", sort=True)

    split = _column_split_x(page.rect.width, blocks)
    if split is None:
        return page.get_text("text", sort=True)

    text_blocks = [b for b in blocks if len(b) > 6 and b[6] == 0 and str(b[4]).strip()]
    spanning: list[tuple] = []
    left: list[tuple] = []
    right: list[tuple] = []
    for block in text_blocks:
        if block[2] <= split:
            left.append(block)
        elif block[0] >= split:
            right.append(block)
        else:
            spanning.append(block)

    def order(block: tuple) -> tuple[float, float]:
        return (round(block[1], 1), round(block[0], 1))

    ordered = sorted(spanning, key=order) + sorted(left, key=order) + sorted(right, key=order)
    return "\n\n".join(str(block[4]).strip() for block in ordered)


def _pymupdf_extract(path: Path) -> tuple[list[str], list[PaperTable]]:
    doc = pymupdf.open(path)  # type: ignore[union-attr]
    pages: list[str] = []
    tables: list[PaperTable] = []
    try:
        for page_number, page in enumerate(doc, 1):
            pages.append(_page_text_columns(page))
            try:
                found = page.find_tables()
            except Exception:
                found = None
            if not found:
                continue
            for index, table in enumerate(found.tables, 1):
                try:
                    rows = _clean_rows(table.extract())
                except Exception:
                    continue
                if _useful_table(rows):
                    tables.append(PaperTable(page=page_number, index=index, rows=rows))
    finally:
        doc.close()
    return pages, tables


def _clean_rows(rows: list[list[object]] | None) -> list[list[str]]:
    cleaned: list[list[str]] = []
    for row in rows or []:
        cells = []
        for cell in row:
            text = re.sub(r"\s+", " ", str(cell or "")).strip()
            cells.append(text)
        if any(cells):
            cleaned.append(cells)
    # Drop all-empty columns.
    if cleaned:
        width = max(len(row) for row in cleaned)
        keep = [
            index
            for index in range(width)
            if any(index < len(row) and row[index] for row in cleaned)
        ]
        cleaned = [[row[index] if index < len(row) else "" for index in keep] for row in cleaned]
    return cleaned


def _useful_table(rows: list[list[str]]) -> bool:
    if len(rows) < 2:
        return False
    width = max(len(row) for row in rows)
    if width < 2:
        return False
    non_empty = sum(1 for row in rows for cell in row if cell)
    return non_empty >= 4


def _pdftotext(path: Path) -> list[str]:
    try:
        proc = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"],
            capture_output=True,
            check=True,
            timeout=180,
        )
    except FileNotFoundError as exc:
        raise PDFError("pdftotext not found; install poppler (brew install poppler)") from exc
    except (subprocess.SubprocessError, OSError) as exc:
        raise PDFError(f"pdftotext failed for {path}: {exc}") from exc
    # Decode explicitly: some poppler builds (notably on Windows) emit bytes
    # that are not valid UTF-8, which would make text=True raise inside the
    # subprocess reader thread and leave stdout as None.
    stdout = proc.stdout or b""
    if isinstance(stdout, bytes):
        stdout = stdout.decode("utf-8", errors="replace")
    return stdout.split("\f")


def _pdftotext_safe(path: Path) -> list[str]:
    try:
        return _pdftotext(path)
    except PDFError:
        return []
    except Exception:  # never let a broken external tool abort PDF loading
        return []


def _text_size(pages: list[str]) -> int:
    return sum(len(page.strip()) for page in pages)


def _fill_empty_pages(path: Path, pages: list[str]) -> tuple[list[str], list[int]]:
    ocr_pages: list[int] = []
    filled = list(pages)
    for index, page in enumerate(filled, 1):
        if len(page.strip()) >= 40:
            continue
        text = _ocr_page(path, index)
        if text.strip():
            filled[index - 1] = text
            ocr_pages.append(index)
    return filled, ocr_pages


def _ocr_pdf(path: Path) -> list[str]:
    if not shutil.which("pdftoppm") or not shutil.which("tesseract"):
        raise PDFError(
            f"{path} looks scanned and OCR is unavailable; install poppler and tesseract"
        )
    with tempfile.TemporaryDirectory(prefix="mkea-ocr-") as tmp:
        prefix = Path(tmp) / "page"
        try:
            subprocess.run(
                ["pdftoppm", "-r", "200", "-png", str(path), str(prefix)],
                check=True,
                capture_output=True,
                timeout=600,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            raise PDFError(f"pdftoppm failed for {path}: {exc}") from exc
        pages: list[str] = []
        images = sorted(
            Path(tmp).glob("page-*.png"),
            key=lambda item: int(re.search(r"(\d+)", item.stem).group(1))
            if re.search(r"(\d+)", item.stem)
            else 0,
        )
        for image in images:
            pages.append(_tesseract(image))
        if not pages:
            raise PDFError(f"OCR produced no pages for {path}")
        return pages


def _ocr_page(path: Path, page: int) -> str:
    if not shutil.which("pdftoppm") or not shutil.which("tesseract"):
        return ""
    with tempfile.TemporaryDirectory(prefix="mkea-ocr-") as tmp:
        prefix = Path(tmp) / "page"
        try:
            subprocess.run(
                [
                    "pdftoppm",
                    "-f",
                    str(page),
                    "-l",
                    str(page),
                    "-r",
                    "200",
                    "-png",
                    "-singlefile",
                    str(path),
                    str(prefix),
                ],
                check=True,
                capture_output=True,
                timeout=180,
            )
        except (subprocess.SubprocessError, OSError):
            return ""
        image = Path(f"{prefix}.png")
        return _tesseract(image) if image.exists() else ""


def _tesseract(image: Path) -> str:
    try:
        proc = subprocess.run(
            ["tesseract", str(image), "stdout", "-l", "eng"],
            capture_output=True,
            check=True,
            text=True,
            timeout=180,
        )
    except (subprocess.SubprocessError, OSError):
        return ""
    return proc.stdout


def _normalise_quote(text: str) -> str:
    text = re.sub(r"-\s*\n\s*", "", text)
    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub(r"\[[0-9, \-]+\]", "", text)
    text = re.sub(r"\.{3,}|…", " ", text)
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def ground_quote(pages: list[str], quote: str, page: int | None = None) -> tuple[bool, list[int]]:
    """Return whether *quote* occurs in *pages* and which pages contain it."""

    if not quote or not pages:
        return False, []
    needle = _normalise_quote(quote)
    if len(needle) < 8:
        return False, []
    tokens = needle.split()
    matches: list[int] = []
    for index, text in enumerate(pages, 1):
        haystack = _normalise_quote(text)
        if needle in haystack:
            matches.append(index)
        elif len(tokens) >= 3 and _shingle_coverage(tokens, haystack.split()) >= 0.75:
            matches.append(index)
    if page is not None and page in matches:
        return True, [page]
    return bool(matches), matches


def _shingle_coverage(needle: list[str], haystack: list[str]) -> float:
    if not needle or not haystack:
        return 0.0
    k = 2 if len(needle) <= 8 else 3
    if len(needle) < k:
        return 0.0
    q = {tuple(needle[i : i + k]) for i in range(len(needle) - k + 1)}
    p = {tuple(haystack[i : i + k]) for i in range(len(haystack) - k + 1)}
    return len(q & p) / len(q) if q else 0.0
