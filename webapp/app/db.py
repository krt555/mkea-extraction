"""SQLite storage for the MesoScope web app.

One file, no ORM: the schema is small and the demo must start with zero setup.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_key     TEXT NOT NULL UNIQUE,
    paper_id      INTEGER NOT NULL,
    title         TEXT NOT NULL DEFAULT '',
    filename      TEXT NOT NULL DEFAULT '',
    pdf_path      TEXT NOT NULL DEFAULT '',
    pages         INTEGER NOT NULL DEFAULT 0,
    chars         INTEGER NOT NULL DEFAULT 0,
    n_tables      INTEGER NOT NULL DEFAULT 0,
    source        TEXT NOT NULL DEFAULT 'upload',
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_pages (
    paper_fk  INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    page      INTEGER NOT NULL,
    text      TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (paper_fk, page)
);

CREATE TABLE IF NOT EXISTS jobs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_fk     INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    status       TEXT NOT NULL DEFAULT 'queued',
    stage        TEXT NOT NULL DEFAULT 'queued',
    progress     INTEGER NOT NULL DEFAULT 0,
    mode         TEXT NOT NULL DEFAULT 'anchored',
    engine       TEXT NOT NULL DEFAULT '',
    model        TEXT NOT NULL DEFAULT '',
    message      TEXT NOT NULL DEFAULT '',
    n_records    INTEGER NOT NULL DEFAULT 0,
    usage_json   TEXT NOT NULL DEFAULT '{}',
    issues_json  TEXT NOT NULL DEFAULT '[]',
    created_at   TEXT NOT NULL,
    finished_at  TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS records (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_fk        INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    job_fk          INTEGER,
    record_id       TEXT NOT NULL UNIQUE,
    record_index    INTEGER NOT NULL DEFAULT 1,
    fields_json     TEXT NOT NULL DEFAULT '{}',
    evidence_json   TEXT NOT NULL DEFAULT '{}',
    confidence_json TEXT NOT NULL DEFAULT '{}',
    reviewed        INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS edits (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    record_fk  INTEGER NOT NULL REFERENCES records(id) ON DELETE CASCADE,
    field      TEXT NOT NULL,
    old_value  TEXT,
    new_value  TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_records_paper ON records(paper_fk);
CREATE INDEX IF NOT EXISTS idx_jobs_paper ON jobs(paper_fk);
CREATE INDEX IF NOT EXISTS idx_pages_paper ON paper_pages(paper_fk);
"""

_LOCK = threading.Lock()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Serialized connection.

        SQLite under a threaded uvicorn worker plus a background extraction
        thread is only safe with one writer at a time, so every call takes the
        process lock.  The demo workload is tiny and this removes every
        "database is locked" failure mode.
        """
        with _LOCK:
            conn = sqlite3.connect(self.path, timeout=30.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def query(self, sql: str, params: tuple | dict = ()) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params: tuple | dict = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql: str, params: tuple | dict = ()) -> int:
        with self.connect() as conn:
            cursor = conn.execute(sql, params)
            return int(cursor.lastrowid or 0)


def loads(raw: Any, fallback: Any) -> Any:
    if raw in (None, ""):
        return fallback
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return fallback


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)
