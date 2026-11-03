"""CLI for the extraction agent."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .agent import AgentError, extract_paper_agent
from .anchored import extract_paper_anchored
from .config import ConfigError, LLMConfig
from .hybrid import extract_paper_hybrid
from .llm import LLMError, OpenAICompatibleLLM
from .pdf import PDFError, PDFLoader
from .pipeline import (
    extract_paper,
    merge_usage,
    write_manifest,
    write_predictions,
)
from .validation import validate_records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mkea-extract",
        description="MKEA extraction agent: PDF -> JSONL records for mkea-bench.",
    )
    parser.add_argument("--version", action="version", version=f"mkea-extract {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="verify the LLM endpoint, key and model")
    _add_llm_args(check)
    check.set_defaults(func=_cmd_check)

    ex = sub.add_parser("extract", help="extract records from one or more PDFs")
    ex.add_argument("pdfs", nargs="*", help="PDF paths (default: all papers in --pdf-dir)")
    ex.add_argument("--pdf-dir", default="papers")
    ex.add_argument("--out", default="outputs/predictions.jsonl")
    ex.add_argument("--manifest", default=None, help="run metadata (default: alongside --out)")
    ex.add_argument("--limit", type=int)
    ex.add_argument("--paper-id", action="append", type=int, default=[])
    ex.add_argument("--resume", action="store_true", help="skip papers already in the manifest")
    ex.add_argument("--dry-run", action="store_true", help="parse/select context without calling the LLM")
    ex.add_argument("--no-cache", action="store_true", help="ignore cached PDF text")
    ex.add_argument("--no-ocr", action="store_true", help="disable OCR fallback for scanned PDFs")
    ex.add_argument("--max-chars", type=int, default=200000, help="context character budget per paper")
    ex.add_argument("--mode", choices=["anchored", "hybrid", "agent", "oneshot"], default=None, help="extraction mode (default: anchored)")
    ex.add_argument("--agent", dest="agent", action=argparse.BooleanOptionalAction, default=None, help="deprecated: --agent = agent mode, --no-agent = oneshot")
    ex.add_argument("--agent-steps", type=int, default=10, help="maximum inventory/agent steps per paper")
    ex.add_argument("--fail-fast", action="store_true", help="stop at the first paper error")
    ex.add_argument("--strict", action="store_true", help="exit 1 on validation errors")
    ex.add_argument("--verbose", action="store_true")
    _add_llm_args(ex)
    ex.set_defaults(func=_cmd_extract)

    val = sub.add_parser("validate", help="validate a predictions JSONL file")
    val.add_argument("--pred", required=True)
    val.set_defaults(func=_cmd_validate)
    return args_dispatch(parser, argv)


def args_dispatch(parser: argparse.ArgumentParser, argv: list[str] | None) -> int:
    args = parser.parse_args(argv)
    return args.func(args)


def _add_llm_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--model", help="model id (default: MKEA_LLM_MODEL or deepseek-v4.1-flash)")
    parser.add_argument("--base-url", help="OpenAI-compatible base URL (default: MKEA_LLM_BASE_URL)")
    parser.add_argument("--api-key-env", default="MKEA_LLM_API_KEY")


def _cmd_check(args: argparse.Namespace) -> int:
    try:
        config = LLMConfig.from_env(
            api_key_env=args.api_key_env, model=args.model, base_url=args.base_url
        )
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(config.redacted(), ensure_ascii=False, indent=2))
    try:
        response = OpenAICompatibleLLM(config).check()
    except LLMError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"ok: {response.model} ({response.latency_s:.2f}s)")
    print(json.dumps(response.usage, ensure_ascii=False, indent=2))
    return 0


def _cmd_extract(args: argparse.Namespace) -> int:
    paths = _resolve_paths(args)
    if not paths:
        print("error: no PDFs selected", file=sys.stderr)
        return 2
    loader = PDFLoader(use_cache=not args.no_cache, ocr=not args.no_ocr)

    if args.dry_run:
        failures = 0
        for path in paths:
            try:
                paper = loader.load(path)
                from .context import build_context

                context = build_context(paper, max_chars=args.max_chars)
                print(json.dumps({**paper.stats(), **context.stats()}, ensure_ascii=False))
            except PDFError as exc:
                failures += 1
                print(f"error: {exc}", file=sys.stderr)
        return 1 if failures else 0

    try:
        config = LLMConfig.from_env(
            api_key_env=args.api_key_env, model=args.model, base_url=args.base_url
        )
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    llm = OpenAICompatibleLLM(config)

    out_path = Path(args.out)
    manifest_path = Path(args.manifest) if args.manifest else out_path.with_suffix(".manifest.json")
    records, manifest = _load_resume(args.resume, out_path, manifest_path)
    done = {
        key
        for key, value in manifest.get("papers", {}).items()
        if isinstance(value, dict) and value.get("status") == "ok"
    }
    usage_items: list[dict[str, Any]] = []

    for position, path in enumerate(paths, 1):
        try:
            paper = loader.load(path)
        except PDFError as exc:
            print(f"[{position}/{len(paths)}] {path.name}: {exc}", file=sys.stderr)
            manifest.setdefault("papers", {})[path.stem] = {"status": "pdf_error", "error": str(exc)}
            if args.fail_fast:
                break
            continue
        if paper.paper_key in done:
            if args.verbose:
                print(f"[{position}/{len(paths)}] {paper.paper_key}: skipped (resume)", file=sys.stderr)
            continue
        try:
            if args.mode:
                mode = args.mode
            elif args.agent is False:
                mode = "oneshot"
            elif args.agent is True:
                mode = "agent"
            else:
                mode = "anchored"
            if mode == "agent":
                try:
                    run = extract_paper_agent(paper, llm, max_steps=args.agent_steps)
                except AgentError as exc:
                    print(
                        f"[{position}/{len(paths)}] {paper.paper_key}: agent fallback: {exc}",
                        file=sys.stderr,
                    )
                    run = extract_paper(paper, llm, max_chars=args.max_chars)
                    run.issues.insert(0, f"agent fallback: {exc}")
                    run.mode = "agent-fallback"
            elif mode == "hybrid":
                run = extract_paper_hybrid(
                    paper,
                    llm,
                    max_steps=args.agent_steps,
                    max_chars=args.max_chars,
                )
            elif mode == "anchored":
                run = extract_paper_anchored(
                    paper,
                    llm,
                    max_steps=args.agent_steps,
                    max_chars=args.max_chars,
                )
            else:
                run = extract_paper(paper, llm, max_chars=args.max_chars)
        except LLMError as exc:
            print(f"[{position}/{len(paths)}] {paper.paper_key}: {exc}", file=sys.stderr)
            manifest.setdefault("papers", {})[paper.paper_key] = {
                "status": "llm_error",
                "error": str(exc),
            }
            if args.fail_fast:
                break
            continue

        records.extend(run.records)
        usage_items.append(run.usage)
        manifest.setdefault("papers", {})[paper.paper_key] = {
            "status": "ok",
            "records": len(run.records),
            "context": run.context,
            "usage": run.usage,
            "issues": run.issues,
            "mode": run.mode,
            "steps": run.steps,
            "tool_calls": run.tool_calls,
        }
        _flush(out_path, records, manifest, manifest_path, config)
        print(
            f"[{position}/{len(paths)}] {paper.paper_key}: {len(run.records)} records, "
            f"{run.context['chars']} chars, {run.elapsed_s:.1f}s, {run.mode}"
            + (f", {len(run.issues)} issues" if run.issues else ""),
            file=sys.stderr,
        )

    validation = validate_records(records)
    manifest.update(
        {
            "tool": "mkea-extract",
            "version": __version__,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "model": config.model,
            "base_url": config.base_url,
            "n_records": len(records),
            "usage": merge_usage(usage_items) if usage_items else manifest.get("usage", {}),
            "validation": validation,
        }
    )
    _flush(out_path, records, manifest, manifest_path, config, final=True)
    print(
        f"wrote {len(records)} records -> {out_path}\n"
        f"manifest -> {manifest_path}\n"
        f"validation: {'ok' if validation['ok'] else 'errors'} "
        f"({validation['records']} records, {validation['papers']} papers, "
        f"evidence coverage {validation['evidence_coverage']:.1%})"
    )
    if validation["errors"]:
        for error in validation["errors"][:20]:
            print(f"  error: {error}", file=sys.stderr)
    if args.strict and not validation["ok"]:
        return 1
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    records = _read_jsonl(Path(args.pred))
    report = validate_records(records)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


def _resolve_paths(args: argparse.Namespace) -> list[Path]:
    if args.pdfs:
        paths = [Path(item) for item in args.pdfs]
    else:
        paths = sorted(Path(args.pdf_dir).glob("paper_*.pdf"))
    if args.paper_id:
        wanted = set(args.paper_id)
        paths = [path for path in paths if _paper_id(path) in wanted]
    if args.limit is not None:
        paths = paths[: args.limit]
    return paths


def _paper_id(path: Path) -> int | None:
    digits = "".join(ch for ch in path.stem if ch.isdigit())
    return int(digits) if digits else None


def _load_resume(
    resume: bool, out_path: Path, manifest_path: Path
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not resume or not out_path.exists():
        return [], {}
    records = _read_jsonl(out_path)
    manifest: dict[str, Any] = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            manifest = {}
    if not manifest:
        manifest = {
            "papers": {
                str(record["paper_key"]): {"status": "ok"}
                for record in records
            }
        }
    return records, manifest


def _flush(
    out_path: Path,
    records: list[dict[str, Any]],
    manifest: dict[str, Any],
    manifest_path: Path,
    config: LLMConfig,
    *,
    final: bool = False,
) -> None:
    manifest.setdefault(
        "config",
        {
            "model": config.model,
            "base_url": config.base_url,
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
        },
    )
    write_predictions(records, out_path)
    write_manifest(manifest, manifest_path)
    _ = final


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records
