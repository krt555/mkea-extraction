"""Tools available to the extraction agent.

Tools are deliberately narrow and deterministic.  The agent decides what to
inspect; the tools decide how PDF pages, tables and arithmetic are accessed.
"""

from __future__ import annotations

import ast
import json
import math
import operator
import re
from typing import Any, Callable

from .pdf import PaperText, ground_quote


class ToolError(RuntimeError):
    pass


class ToolBox:
    def __init__(self, paper: PaperText):
        self.paper = paper
        self._dispatch: dict[str, Callable[..., Any]] = {
            "list_pages": self.list_pages,
            "search_pages": self.search_pages,
            "read_page": self.read_page,
            "list_tables": self.list_tables,
            "read_table": self.read_table,
            "calculate": self.calculate,
            "verify_evidence": self.verify_evidence,
        }

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        function = self._dispatch.get(name)
        if function is None:
            raise ToolError(f"unknown tool: {name}")
        try:
            result = function(**(arguments or {}))
        except TypeError as exc:
            raise ToolError(f"invalid arguments for {name}: {exc}") from exc
        return {"tool": name, "result": result}

    def list_pages(self) -> list[dict[str, Any]]:
        pages = []
        for number, text in enumerate(self.paper.pages, 1):
            heading = next((line.strip() for line in text.splitlines() if line.strip()), "")
            pages.append(
                {
                    "page": number,
                    "heading": heading[:160],
                    "chars": len(text),
                    "tables": [table.table_id for table in self.paper.tables_for_page(number)],
                }
            )
        return pages

    def search_pages(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        query_tokens = _tokens(query)
        if not query_tokens:
            raise ToolError("search query is empty")
        scored: list[tuple[float, int, str]] = []
        for page_number, text in enumerate(self.paper.pages_with_tables, 1):
            for paragraph in re.split(r"\n\s*\n", text):
                paragraph = paragraph.strip()
                if len(paragraph) < 20:
                    continue
                haystack = _tokens(paragraph)
                if not haystack:
                    continue
                overlap = len(query_tokens & haystack)
                if not overlap:
                    continue
                score = overlap / max(len(query_tokens), 1)
                scored.append((score, page_number, paragraph[:1200]))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [
            {"page": page, "score": round(score, 3), "text": text}
            for score, page, text in scored[: max(1, min(int(top_k), 12))]
        ]

    def read_page(self, page: int, max_chars: int = 12000) -> dict[str, Any]:
        try:
            page = int(page)
        except (TypeError, ValueError) as exc:
            raise ToolError("page must be an integer") from exc
        if not 1 <= page <= len(self.paper.pages):
            raise ToolError(f"page out of range: {page}")
        text = self.paper.page_with_tables(page)
        truncated = len(text) > max_chars
        return {
            "page": page,
            "text": text[:max_chars],
            "truncated": truncated,
            "tables": [table.table_id for table in self.paper.tables_for_page(page)],
        }

    def list_tables(self) -> list[dict[str, Any]]:
        tables = []
        for table in self.paper.tables:
            first = table.rows[0] if table.rows else []
            tables.append(
                {
                    "table_id": table.table_id,
                    "page": table.page,
                    "rows": table.n_rows,
                    "cols": table.n_cols,
                    "header": first,
                }
            )
        return tables

    def read_table(self, table_id: str, max_rows: int = 80) -> dict[str, Any]:
        table = self.paper.find_table(table_id)
        if table is None:
            raise ToolError(f"table not found: {table_id}")
        return {
            "table_id": table.table_id,
            "page": table.page,
            "rows": table.n_rows,
            "text": table.text(max_rows=max_rows),
        }

    def calculate(self, expression: str) -> dict[str, Any]:
        value = safe_eval(expression)
        return {"expression": expression, "value": value}

    def verify_evidence(
        self,
        records: list[dict[str, Any]] | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Check proposed quotes against the selected page text.

        The agent can call this before `submit`; post-processing still runs the
        same check as a final guard.
        """

        checks: list[tuple[str, str, int | None, str]] = []
        for record in records or []:
            if not isinstance(record, dict):
                continue
            label = str(record.get("label") or record.get("record_id") or "record")
            for field, entries in (record.get("evidence") or {}).items():
                for entry in entries if isinstance(entries, list) else [entries]:
                    if isinstance(entry, dict):
                        checks.append(
                            (label, str(field), _as_int(entry.get("page")), str(entry.get("quote") or ""))
                        )
        for field, entries in (evidence or {}).items():
            for entry in entries if isinstance(entries, list) else [entries]:
                if isinstance(entry, dict):
                    checks.append(
                        ("record", str(field), _as_int(entry.get("page")), str(entry.get("quote") or ""))
                    )

        ungrounded = []
        for label, field, page, quote in checks[:200]:
            grounded, matches = ground_quote(self.paper.pages_with_tables, quote, page=page)
            if not grounded:
                ungrounded.append(
                    {"record": label, "field": field, "page": page, "quote": quote[:200]}
                )
            elif page is not None and page not in matches:
                ungrounded.append(
                    {
                        "record": label,
                        "field": field,
                        "page": page,
                        "matched_pages": matches,
                        "quote": quote[:200],
                    }
                )
        return {"checked": len(checks), "ok": not ungrounded, "ungrounded": ungrounded}


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


_ALLOWED_BINARY = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
}
_ALLOWED_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_ALLOWED_NAMES = {
    "pi": math.pi,
    "e": math.e,
    "sqrt": math.sqrt,
    "log": math.log,
    "log10": math.log10,
    "exp": math.exp,
    "abs": abs,
    "round": round,
}


def safe_eval(expression: str) -> float:
    if not expression or len(expression) > 500:
        raise ToolError("expression is empty or too long")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ToolError(f"invalid expression: {expression}") from exc

    def evaluate(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINARY:
            left = evaluate(node.left)
            right = evaluate(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > 100:
                raise ToolError("exponent is too large")
            return float(_ALLOWED_BINARY[type(node.op)](left, right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_UNARY:
            return float(_ALLOWED_UNARY[type(node.op)](evaluate(node.operand)))
        if isinstance(node, ast.Name) and node.id in _ALLOWED_NAMES:
            value = _ALLOWED_NAMES[node.id]
            if callable(value):
                raise ToolError(f"function requires an argument: {node.id}")
            return float(value)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in _ALLOWED_NAMES
            and callable(_ALLOWED_NAMES[node.func.id])
            and not node.keywords
        ):
            args = [evaluate(argument) for argument in node.args]
            return float(_ALLOWED_NAMES[node.func.id](*args))
        raise ToolError(f"unsupported expression element: {type(node).__name__}")

    return round(evaluate(tree), 6)


def _tokens(text: str) -> set[str]:
    text = text.lower()
    latin = set(re.findall(r"[a-z0-9]+(?:[.\-][a-z0-9]+)*", text))
    han = "".join(re.findall(r"[\u4e00-\u9fff]", text))
    grams = set(han)
    grams.update(han[index : index + 2] for index in range(max(0, len(han) - 1)))
    return latin | grams


def tool_json(result: dict[str, Any], *, limit: int = 16000) -> str:
    text = json.dumps(result, ensure_ascii=False)
    if len(text) > limit:
        text = text[:limit] + "\n...[truncated]"
    return text
