"""MesoScope FastAPI application.

Endpoints are grouped by the four product modules:
  papers / jobs   -> library and extraction tasks
  records         -> review workbench (read, edit, export)
  search          -> cross-paper structured query
  ask             -> citation-grounded QA
"""

from __future__ import annotations

import csv
import io
import json
import re
import shutil
import threading
import traceback
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, UploadFile, File
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .db import Database, dumps, loads, now
from .demo import DEMO_PAPERS
from .pdfgen import render_demo_pdf
from .extraction import (
    EXTRACTION_MODES,
    FIELD_KEYS,
    FIELD_LABELS,
    MODE_VALUES,
    PDFError,
    llm_status,
    load_paper,
    run_extraction,
    verify_evidence,
)
from .qa import Retriever, build_answer
from .settings import (
    SettingsError,
    clear_key,
    read_settings,
    save_settings,
    test_connection,
)

ROOT = Path(__file__).resolve().parents[2]
WEBAPP = ROOT / "webapp"
DATA_DIR = WEBAPP / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
STATIC_DIR = WEBAPP / "static"

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
db = Database(DATA_DIR / "mesoscope.db")

app = FastAPI(title="MesoScope", version="1.0.0")
_JOB_LOCK = threading.Lock()


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _next_paper_id() -> int:
    row = db.one("SELECT MAX(paper_id) AS top FROM papers")
    return int((row or {}).get("top") or 0) + 1


def _safe_title(filename: str) -> str:
    stem = Path(filename).stem.replace("_", " ").replace("-", " ")
    return re.sub(r"\s+", " ", stem).strip()[:200] or "未命名论文"


def _paper_row(paper_fk: int) -> dict[str, Any]:
    row = db.one("SELECT * FROM papers WHERE id = ?", (paper_fk,))
    if not row:
        raise HTTPException(404, "论文不存在")
    return row


def _paper_payload(row: dict[str, Any]) -> dict[str, Any]:
    stats = db.one(
        """
        SELECT
          (SELECT COUNT(*) FROM records WHERE paper_fk = ?) AS n_records,
          (SELECT COUNT(*) FROM records WHERE paper_fk = ? AND reviewed = 1) AS n_reviewed
        """,
        (row["id"], row["id"]),
    ) or {}
    job = db.one(
        "SELECT id, status, stage, progress, engine, model, message, n_records, created_at,"
        " finished_at, issues_json FROM jobs WHERE paper_fk = ? ORDER BY id DESC LIMIT 1",
        (row["id"],),
    )
    return {
        "id": row["id"],
        "paper_key": row["paper_key"],
        "paper_id": row["paper_id"],
        "title": row["title"],
        "filename": row["filename"],
        "pages": row["pages"],
        "chars": row["chars"],
        "n_tables": row["n_tables"],
        "source": row["source"],
        "created_at": row["created_at"],
        "n_records": int(stats.get("n_records") or 0),
        "n_reviewed": int(stats.get("n_reviewed") or 0),
        "latest_job": _job_payload(job) if job else None,
    }


def _job_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "status": row["status"],
        "stage": row["stage"],
        "progress": row["progress"],
        "engine": row.get("engine", ""),
        "model": row.get("model", ""),
        "message": row.get("message", ""),
        "n_records": row.get("n_records", 0),
        "issues": loads(row.get("issues_json"), []),
        "created_at": row.get("created_at", ""),
        "finished_at": row.get("finished_at", ""),
    }


def _record_payload(row: dict[str, Any], paper: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {
        "id": row["id"],
        "record_id": row["record_id"],
        "record_index": row["record_index"],
        "paper_fk": row["paper_fk"],
        "fields": loads(row["fields_json"], {}),
        "evidence": loads(row["evidence_json"], {}),
        "confidence": loads(row["confidence_json"], {}),
        "reviewed": bool(row["reviewed"]),
        "updated_at": row["updated_at"],
    }
    if paper:
        payload["paper_key"] = paper["paper_key"]
        payload["paper_title"] = paper["title"]
        payload["paper_id"] = paper["paper_id"]
    return payload


def _store_pages(paper_fk: int, pages: list[str]) -> None:
    with db.connect() as conn:
        conn.execute("DELETE FROM paper_pages WHERE paper_fk = ?", (paper_fk,))
        conn.executemany(
            "INSERT INTO paper_pages (paper_fk, page, text) VALUES (?, ?, ?)",
            [(paper_fk, index, text) for index, text in enumerate(pages, 1)],
        )


# --------------------------------------------------------------------------- #
# papers
# --------------------------------------------------------------------------- #

@app.get("/api/status")
def api_status() -> dict[str, Any]:
    status = llm_status()
    counts = db.one(
        "SELECT (SELECT COUNT(*) FROM papers) AS papers,"
        " (SELECT COUNT(*) FROM records) AS records,"
        " (SELECT COUNT(*) FROM records WHERE reviewed=1) AS reviewed,"
        " (SELECT COUNT(*) FROM jobs WHERE status='running') AS running"
    ) or {}
    return {
        "llm": status,
        "counts": {key: int(value or 0) for key, value in counts.items()},
        "fields": [{"key": key, "label": FIELD_LABELS[key]} for key in FIELD_KEYS],
        "modes": EXTRACTION_MODES,
    }


# --------------------------------------------------------------------------- #
# in-app LLM settings
# --------------------------------------------------------------------------- #

@app.get("/api/settings/llm")
def api_get_llm_settings() -> dict[str, Any]:
    """Current credentials (key redacted) plus the provider presets."""
    return read_settings()


@app.put("/api/settings/llm")
async def api_put_llm_settings(request: Request) -> dict[str, Any]:
    """Save credentials to .env and apply them without a restart.

    ``test`` (default true) verifies the endpoint before persisting, so a typo
    cannot silently disable the LLM engine.
    """
    body = await request.json()
    api_key = body.get("api_key")
    base_url = str(body.get("base_url") or "")
    model = str(body.get("model") or "")
    should_test = body.get("test", True)

    probe: dict[str, Any] | None = None
    if should_test:
        probe = test_connection(api_key=api_key, base_url=base_url, model=model)
        if not probe["ok"]:
            raise HTTPException(400, probe["message"])

    try:
        settings = save_settings(api_key=api_key, base_url=base_url, model=model)
    except SettingsError as exc:
        raise HTTPException(400, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(500, f"写入配置文件失败：{exc}") from exc

    return {"settings": settings, "llm": llm_status(), "test": probe}


@app.post("/api/settings/llm/test")
async def api_test_llm_settings(request: Request) -> dict[str, Any]:
    """Verify endpoint/key/model without saving anything."""
    body = await request.json()
    return test_connection(
        api_key=body.get("api_key"),
        base_url=body.get("base_url"),
        model=body.get("model"),
    )


@app.delete("/api/settings/llm")
def api_delete_llm_settings() -> dict[str, Any]:
    """Forget the stored key and fall back to the rule engine."""
    try:
        settings = clear_key()
    except OSError as exc:
        raise HTTPException(500, f"写入配置文件失败：{exc}") from exc
    return {"settings": settings, "llm": llm_status()}


@app.get("/api/papers")
def api_papers() -> dict[str, Any]:
    rows = db.query("SELECT * FROM papers ORDER BY paper_id")
    return {"papers": [_paper_payload(row) for row in rows]}


@app.get("/api/papers/{paper_fk}")
def api_paper(paper_fk: int) -> dict[str, Any]:
    row = _paper_row(paper_fk)
    payload = _paper_payload(row)
    records = db.query(
        "SELECT * FROM records WHERE paper_fk = ? ORDER BY record_index", (paper_fk,)
    )
    payload["records"] = [_record_payload(item, row) for item in records]
    payload["jobs"] = [
        _job_payload(job)
        for job in db.query("SELECT * FROM jobs WHERE paper_fk = ? ORDER BY id DESC", (paper_fk,))
    ]
    return payload


@app.get("/api/papers/{paper_fk}/pdf")
def api_paper_pdf(paper_fk: int) -> FileResponse:
    row = _paper_row(paper_fk)
    path = Path(row["pdf_path"])
    if not path.is_absolute():
        path = ROOT / path
    if not path.exists():
        raise HTTPException(404, "PDF 文件不存在（可能是演示数据，无原始文件）")
    return FileResponse(path, media_type="application/pdf", filename=row["filename"])


@app.get("/api/papers/{paper_fk}/pages/{page}")
def api_paper_page(paper_fk: int, page: int) -> dict[str, Any]:
    _paper_row(paper_fk)
    row = db.one(
        "SELECT text FROM paper_pages WHERE paper_fk = ? AND page = ?", (paper_fk, page)
    )
    if not row:
        raise HTTPException(404, "该页文本不存在")
    return {"page": page, "text": row["text"]}


@app.post("/api/papers")
async def api_upload(file: UploadFile = File(...)) -> dict[str, Any]:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "只接受 PDF 文件")

    paper_id = _next_paper_id()
    paper_key = f"paper_{paper_id:03d}"
    target = UPLOAD_DIR / f"{paper_key}.pdf"
    with target.open("wb") as handle:
        shutil.copyfileobj(file.file, handle)

    try:
        paper = load_paper(target)
    except PDFError as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(400, f"PDF 解析失败：{exc}") from exc

    stats = paper.stats()
    paper_fk = db.execute(
        """
        INSERT INTO papers (paper_key, paper_id, title, filename, pdf_path, pages, chars,
                            n_tables, source, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'upload', ?)
        """,
        (
            paper_key,
            paper_id,
            _safe_title(file.filename),
            file.filename,
            str(target.relative_to(ROOT)).replace("\\", "/"),
            int(stats["pages"]),
            int(stats["chars"]),
            int(stats["tables"]),
            now(),
        ),
    )
    _store_pages(paper_fk, paper.pages_with_tables)
    return _paper_payload(_paper_row(paper_fk))


@app.delete("/api/papers/{paper_fk}")
def api_delete_paper(paper_fk: int) -> dict[str, Any]:
    row = _paper_row(paper_fk)
    db.execute("DELETE FROM papers WHERE id = ?", (paper_fk,))
    path = ROOT / row["pdf_path"]
    if row["source"] == "upload" and path.exists() and UPLOAD_DIR in path.parents:
        path.unlink(missing_ok=True)
    return {"ok": True}


# --------------------------------------------------------------------------- #
# jobs
# --------------------------------------------------------------------------- #

def _run_job(job_fk: int, paper_fk: int, mode: str) -> None:
    def progress(stage: str, percent: int) -> None:
        db.execute(
            "UPDATE jobs SET stage = ?, progress = ? WHERE id = ?", (stage, percent, job_fk)
        )

    try:
        row = db.one("SELECT * FROM papers WHERE id = ?", (paper_fk,))
        if not row:
            raise RuntimeError("论文记录已被删除")
        db.execute(
            "UPDATE jobs SET status='running', stage='解析 PDF', progress=15 WHERE id=?",
            (job_fk,),
        )
        path = ROOT / row["pdf_path"]
        paper = load_paper(path)
        _store_pages(paper_fk, paper.pages_with_tables)

        result = run_extraction(paper, mode=mode, progress=progress)
        records = result["records"]

        # Deterministic evidence grounding before anything reaches the UI.
        dropped = 0
        for record in records:
            kept: dict[str, Any] = {}
            for key, items in record["evidence"].items():
                valid = [
                    item for item in items if verify_evidence(paper, item["page"], item["quote"])
                ]
                if valid:
                    kept[key] = valid
                else:
                    dropped += len(items)
            record["evidence"] = kept
        issues = list(result["issues"])
        if dropped:
            issues.append(f"证据校验丢弃 {dropped} 条无法在原页定位的引文")

        with db.connect() as conn:
            conn.execute("DELETE FROM records WHERE paper_fk = ?", (paper_fk,))
            stamp = now()
            for record in records:
                conn.execute(
                    """
                    INSERT INTO records (paper_fk, job_fk, record_id, record_index, fields_json,
                                         evidence_json, confidence_json, reviewed, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                    """,
                    (
                        paper_fk,
                        job_fk,
                        record["record_id"],
                        record["record_index"],
                        dumps(record["fields"]),
                        dumps(record["evidence"]),
                        dumps(record["confidence"]),
                        stamp,
                        stamp,
                    ),
                )

        db.execute(
            """
            UPDATE jobs SET status='succeeded', stage='完成', progress=100, engine=?, model=?,
                            n_records=?, usage_json=?, issues_json=?, finished_at=?, message=''
            WHERE id=?
            """,
            (
                result["engine"],
                result["model"],
                len(records),
                dumps(result["usage"]),
                dumps(issues),
                now(),
                job_fk,
            ),
        )
    except Exception as exc:  # noqa: BLE001 - job must record its own failure
        traceback.print_exc()
        db.execute(
            """
            UPDATE jobs SET status='failed', stage='失败', progress=100, message=?,
                            finished_at=? WHERE id=?
            """,
            (str(exc)[:500], now(), job_fk),
        )


@app.post("/api/jobs")
async def api_create_job(request: Request) -> dict[str, Any]:
    body = await request.json()
    paper_fk = int(body.get("paper_fk") or 0)
    mode = str(body.get("mode") or "anchored")
    if mode not in MODE_VALUES:
        raise HTTPException(400, "mode 不合法")
    _paper_row(paper_fk)

    with _JOB_LOCK:
        active = db.one(
            "SELECT id FROM jobs WHERE paper_fk = ? AND status IN ('queued','running')",
            (paper_fk,),
        )
        if active:
            raise HTTPException(409, "该论文已有进行中的任务")
        job_fk = db.execute(
            "INSERT INTO jobs (paper_fk, status, stage, progress, mode, created_at)"
            " VALUES (?, 'queued', '排队中', 5, ?, ?)",
            (paper_fk, mode, now()),
        )

    thread = threading.Thread(target=_run_job, args=(job_fk, paper_fk, mode), daemon=True)
    thread.start()
    job = db.one("SELECT * FROM jobs WHERE id = ?", (job_fk,))
    return _job_payload(job or {})


@app.get("/api/jobs/{job_fk}")
def api_job(job_fk: int) -> dict[str, Any]:
    row = db.one(
        """
        SELECT jobs.*, papers.paper_key, papers.title
        FROM jobs JOIN papers ON papers.id = jobs.paper_fk
        WHERE jobs.id = ?
        """,
        (job_fk,),
    )
    if not row:
        raise HTTPException(404, "任务不存在")
    payload = _job_payload(row)
    payload["paper_fk"] = row["paper_fk"]
    payload["paper_key"] = row["paper_key"]
    payload["title"] = row["title"]
    payload["usage"] = loads(row["usage_json"], {})
    return payload


@app.get("/api/jobs")
def api_jobs(limit: int = Query(30, ge=1, le=200)) -> dict[str, Any]:
    rows = db.query(
        """
        SELECT jobs.*, papers.paper_key, papers.title
        FROM jobs JOIN papers ON papers.id = jobs.paper_fk
        ORDER BY jobs.id DESC LIMIT ?
        """,
        (limit,),
    )
    jobs = []
    for row in rows:
        payload = _job_payload(row)
        payload.update(
            {"paper_fk": row["paper_fk"], "paper_key": row["paper_key"], "title": row["title"]}
        )
        jobs.append(payload)
    return {"jobs": jobs}


# --------------------------------------------------------------------------- #
# records / review
# --------------------------------------------------------------------------- #

@app.put("/api/records/{record_fk}")
async def api_update_record(record_fk: int, request: Request) -> dict[str, Any]:
    body = await request.json()
    row = db.one("SELECT * FROM records WHERE id = ?", (record_fk,))
    if not row:
        raise HTTPException(404, "记录不存在")

    fields = loads(row["fields_json"], {})
    incoming = body.get("fields") or {}
    changes: list[tuple[str, Any, Any]] = []
    for key, value in incoming.items():
        if key not in FIELD_KEYS:
            continue
        clean = value.strip() if isinstance(value, str) else value
        if clean == "":
            clean = None
        if fields.get(key) != clean:
            changes.append((key, fields.get(key), clean))
            fields[key] = clean

    reviewed = body.get("reviewed")
    stamp = now()
    with db.connect() as conn:
        conn.execute(
            "UPDATE records SET fields_json = ?, reviewed = ?, updated_at = ? WHERE id = ?",
            (
                dumps(fields),
                1 if (reviewed if reviewed is not None else row["reviewed"]) else 0,
                stamp,
                record_fk,
            ),
        )
        for key, old, new in changes:
            conn.execute(
                "INSERT INTO edits (record_fk, field, old_value, new_value, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (record_fk, key, old, new, stamp),
            )

    updated = db.one("SELECT * FROM records WHERE id = ?", (record_fk,))
    paper = db.one("SELECT * FROM papers WHERE id = ?", (row["paper_fk"],))
    payload = _record_payload(updated or {}, paper)
    payload["n_changes"] = len(changes)
    return payload


@app.get("/api/records/{record_fk}/history")
def api_record_history(record_fk: int) -> dict[str, Any]:
    rows = db.query(
        "SELECT id, field, old_value, new_value, created_at FROM edits WHERE record_fk = ?"
        " ORDER BY id DESC",
        (record_fk,),
    )
    edits = [
        {
            **row,
            "label": FIELD_LABELS.get(row["field"], row["field"]),
        }
        for row in rows
    ]
    return {"edits": edits, "total": len(edits)}


@app.post("/api/records/{record_fk}/revert")
async def api_revert_edit(record_fk: int, request: Request) -> dict[str, Any]:
    """Restore one field to the value it had before a given edit."""
    body = await request.json()
    edit_id = int(body.get("edit_id") or 0)
    record = db.one("SELECT * FROM records WHERE id = ?", (record_fk,))
    if not record:
        raise HTTPException(404, "记录不存在")
    edit = db.one(
        "SELECT * FROM edits WHERE id = ? AND record_fk = ?", (edit_id, record_fk)
    )
    if not edit:
        raise HTTPException(404, "修改记录不存在")

    fields = loads(record["fields_json"], {})
    key = edit["field"]
    current = fields.get(key)
    target = edit["old_value"]
    if current == target:
        raise HTTPException(409, "该字段当前已是撤销后的值")

    fields[key] = target
    stamp = now()
    with db.connect() as conn:
        conn.execute(
            "UPDATE records SET fields_json = ?, updated_at = ? WHERE id = ?",
            (dumps(fields), stamp, record_fk),
        )
        # 撤销本身也记入历史，保持可追溯
        conn.execute(
            "INSERT INTO edits (record_fk, field, old_value, new_value, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (record_fk, key, current, target, stamp),
        )

    updated = db.one("SELECT * FROM records WHERE id = ?", (record_fk,))
    paper = db.one("SELECT * FROM papers WHERE id = ?", (record["paper_fk"],))
    payload = _record_payload(updated or {}, paper)
    payload["reverted_field"] = key
    payload["label"] = FIELD_LABELS.get(key, key)
    return payload


# --------------------------------------------------------------------------- #
# batch operations
# --------------------------------------------------------------------------- #

@app.post("/api/jobs/batch")
async def api_batch_jobs(request: Request) -> dict[str, Any]:
    """Queue extraction for several papers at once."""
    body = await request.json()
    ids = body.get("paper_fks") or []
    mode = str(body.get("mode") or "anchored")
    if mode not in MODE_VALUES:
        raise HTTPException(400, "mode 不合法")
    if not isinstance(ids, list) or not ids:
        raise HTTPException(400, "paper_fks 不能为空")

    queued: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for raw in ids:
        try:
            paper_fk = int(raw)
        except (TypeError, ValueError):
            continue
        paper = db.one("SELECT paper_key FROM papers WHERE id = ?", (paper_fk,))
        if not paper:
            skipped.append({"paper_fk": paper_fk, "reason": "论文不存在"})
            continue
        with _JOB_LOCK:
            active = db.one(
                "SELECT id FROM jobs WHERE paper_fk = ? AND status IN ('queued','running')",
                (paper_fk,),
            )
            if active:
                skipped.append({"paper_fk": paper_fk, "reason": "已有进行中的任务"})
                continue
            job_fk = db.execute(
                "INSERT INTO jobs (paper_fk, status, stage, progress, mode, created_at)"
                " VALUES (?, 'queued', '排队中', 5, ?, ?)",
                (paper_fk, mode, now()),
            )
        threading.Thread(
            target=_run_job, args=(job_fk, paper_fk, mode), daemon=True
        ).start()
        queued.append({"paper_fk": paper_fk, "job_fk": job_fk, "paper_key": paper["paper_key"]})

    return {"queued": queued, "skipped": skipped, "mode": mode}


@app.post("/api/papers/batch-delete")
async def api_batch_delete(request: Request) -> dict[str, Any]:
    """Delete several papers and their records at once."""
    body = await request.json()
    ids = body.get("paper_fks") or []
    if not isinstance(ids, list) or not ids:
        raise HTTPException(400, "paper_fks 不能为空")

    deleted: list[str] = []
    blocked: list[dict[str, Any]] = []
    for raw in ids:
        try:
            paper_fk = int(raw)
        except (TypeError, ValueError):
            continue
        row = db.one("SELECT * FROM papers WHERE id = ?", (paper_fk,))
        if not row:
            continue
        active = db.one(
            "SELECT id FROM jobs WHERE paper_fk = ? AND status IN ('queued','running')",
            (paper_fk,),
        )
        if active:
            blocked.append({"paper_key": row["paper_key"], "reason": "任务进行中"})
            continue
        db.execute("DELETE FROM papers WHERE id = ?", (paper_fk,))
        path = ROOT / row["pdf_path"]
        if row["source"] == "upload" and path.exists() and UPLOAD_DIR in path.parents:
            path.unlink(missing_ok=True)
        deleted.append(row["paper_key"])

    return {"deleted": deleted, "blocked": blocked}


# --------------------------------------------------------------------------- #
# cross-paper structured search
# --------------------------------------------------------------------------- #

def _to_number(value: Any) -> float | None:
    if value is None:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", str(value))
    return float(match.group(0)) if match else None


@app.get("/api/search")
def api_search(
    q: str = Query("", description="字段值关键词"),
    field: str = Query("", description="限定字段 key"),
    sa_min: float | None = None,
    sa_max: float | None = None,
    pore_min: float | None = None,
    pore_max: float | None = None,
    reviewed_only: bool = False,
) -> dict[str, Any]:
    rows = db.query(
        """
        SELECT records.*, papers.paper_key, papers.title, papers.paper_id AS paper_num
        FROM records JOIN papers ON papers.id = records.paper_fk
        ORDER BY papers.paper_id, records.record_index
        """
    )
    needle = q.strip().lower()
    results = []
    for row in rows:
        fields = loads(row["fields_json"], {})
        if reviewed_only and not row["reviewed"]:
            continue
        if needle:
            if field and field in FIELD_KEYS:
                haystack = str(fields.get(field) or "").lower()
            else:
                haystack = " ".join(str(value or "") for value in fields.values()).lower()
            if needle not in haystack:
                continue
        area = _to_number(fields.get("surface_area"))
        pore = _to_number(fields.get("pore_size"))
        if sa_min is not None and (area is None or area < sa_min):
            continue
        if sa_max is not None and (area is None or area > sa_max):
            continue
        if pore_min is not None and (pore is None or pore < pore_min):
            continue
        if pore_max is not None and (pore is None or pore > pore_max):
            continue
        payload = _record_payload(row, {"paper_key": row["paper_key"], "title": row["title"], "paper_id": row["paper_num"]})
        payload["surface_area_num"] = area
        payload["pore_size_num"] = pore
        results.append(payload)
    return {"results": results, "total": len(results)}


@app.get("/api/export")
def api_export(fmt: str = Query("jsonl", pattern="^(jsonl|csv)$"), paper_fk: int | None = None):
    sql = """
        SELECT records.*, papers.paper_key, papers.paper_id AS paper_num, papers.pdf_path
        FROM records JOIN papers ON papers.id = records.paper_fk
    """
    params: tuple = ()
    if paper_fk:
        sql += " WHERE records.paper_fk = ?"
        params = (paper_fk,)
    sql += " ORDER BY papers.paper_id, records.record_index"
    rows = db.query(sql, params)

    if fmt == "jsonl":
        buffer = io.StringIO()
        for row in rows:
            # Exactly the contract in docs/extraction-contract.md
            buffer.write(
                json.dumps(
                    {
                        "paper_id": row["paper_num"],
                        "paper_key": row["paper_key"],
                        "record_id": row["record_id"],
                        "record_index": row["record_index"],
                        "pdf_path": f"papers/{row['paper_key']}.pdf",
                        "fields": loads(row["fields_json"], {}),
                        "evidence": loads(row["evidence_json"], {}),
                        "confidence": loads(row["confidence_json"], {}),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
        return StreamingResponse(
            io.BytesIO(buffer.getvalue().encode("utf-8")),
            media_type="application/x-ndjson",
            headers={"Content-Disposition": 'attachment; filename="predictions.jsonl"'},
        )

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["paper_key", "record_id"] + [FIELD_LABELS[key] for key in FIELD_KEYS])
    for row in rows:
        fields = loads(row["fields_json"], {})
        writer.writerow(
            [row["paper_key"], row["record_id"]] + [fields.get(key) or "" for key in FIELD_KEYS]
        )
    return StreamingResponse(
        io.BytesIO(buffer.getvalue().encode("utf-8-sig")),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="records.csv"'},
    )


# --------------------------------------------------------------------------- #
# QA
# --------------------------------------------------------------------------- #

def _build_retriever(paper_fk: int | None) -> Retriever:
    sql = """
        SELECT paper_pages.page, paper_pages.text, papers.paper_key, papers.title, papers.id AS paper_fk
        FROM paper_pages JOIN papers ON papers.id = paper_pages.paper_fk
    """
    params: tuple = ()
    if paper_fk:
        sql += " WHERE papers.id = ?"
        params = (paper_fk,)
    chunks = [row for row in db.query(sql, params) if (row["text"] or "").strip()]
    return Retriever(chunks)


@app.post("/api/ask")
async def api_ask(request: Request) -> dict[str, Any]:
    body = await request.json()
    question = str(body.get("question") or "").strip()
    if not question:
        raise HTTPException(400, "问题不能为空")
    paper_fk = body.get("paper_fk")
    paper_fk = int(paper_fk) if paper_fk else None
    top_k = int(body.get("top_k") or 4)

    retriever = _build_retriever(paper_fk)
    hits = retriever.search(question, top_k=max(1, min(8, top_k)))
    result = build_answer(question, hits, llm_status()["available"])
    result["question"] = question
    result["scope"] = "single" if paper_fk else "all"
    return result


# --------------------------------------------------------------------------- #
# demo corpus
# --------------------------------------------------------------------------- #

@app.post("/api/demo/seed")
def api_seed(run: bool = True) -> dict[str, Any]:
    """Create the demo papers as real PDFs and optionally extract them."""
    created: list[dict[str, Any]] = []
    for item in DEMO_PAPERS:
        existing = db.one("SELECT id FROM papers WHERE filename = ?", (item["filename"],))
        if existing:
            continue
        paper_id = _next_paper_id()
        paper_key = f"paper_{paper_id:03d}"
        target = UPLOAD_DIR / f"{paper_key}.pdf"
        render_demo_pdf(item["pages"], target)

        try:
            paper = load_paper(target)
        except PDFError as exc:  # pragma: no cover - demo text is known-good
            target.unlink(missing_ok=True)
            raise HTTPException(500, f"演示 PDF 生成失败：{exc}") from exc

        stats = paper.stats()
        paper_fk = db.execute(
            """
            INSERT INTO papers (paper_key, paper_id, title, filename, pdf_path, pages, chars,
                                n_tables, source, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'demo', ?)
            """,
            (
                paper_key,
                paper_id,
                item["title"],
                item["filename"],
                str(target.relative_to(ROOT)).replace("\\", "/"),
                int(stats["pages"]),
                int(stats["chars"]),
                int(stats["tables"]),
                now(),
            ),
        )
        _store_pages(paper_fk, paper.pages_with_tables)
        created.append({"paper_fk": paper_fk, "paper_key": paper_key})

        if run:
            job_fk = db.execute(
                "INSERT INTO jobs (paper_fk, status, stage, progress, mode, created_at)"
                " VALUES (?, 'queued', '排队中', 5, 'anchored', ?)",
                (paper_fk, now()),
            )
            threading.Thread(
                target=_run_job, args=(job_fk, paper_fk, "anchored"), daemon=True
            ).start()

    if not created:
        return {"created": [], "message": "演示数据已存在"}
    return {"created": created, "message": f"已载入 {len(created)} 篇演示论文"}


# --------------------------------------------------------------------------- #
# static front-end
# --------------------------------------------------------------------------- #

@app.get("/")
def index() -> HTMLResponse:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html)


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
    _ = request
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)


if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
