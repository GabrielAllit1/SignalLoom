from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import sqlite3

from .model import ExtractionResult, SourceDocument

APP_DIR = Path.home() / ".signalloom"
DB_FILE = APP_DIR / "signalloom.db"
EXPORT_DIR = APP_DIR / "exports"


def connect(path: Path = DB_FILE) -> sqlite3.Connection:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE IF NOT EXISTS documents (
        source_id TEXT PRIMARY KEY,
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_id TEXT NOT NULL,
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""")
    return con


def save_document(doc: SourceDocument) -> None:
    with connect() as con:
        con.execute(
            "INSERT OR REPLACE INTO documents(source_id, payload, created_at) VALUES (?, ?, ?)",
            (doc.source_id, json.dumps(doc.to_dict(), ensure_ascii=False), doc.ingested_at),
        )


def save_result(result: ExtractionResult) -> None:
    with connect() as con:
        con.execute(
            "INSERT INTO results(source_id, payload, created_at) VALUES (?, ?, ?)",
            (result.source_id, result.to_json(), result.created_at),
        )


def recent_results(limit: int = 30) -> list[dict[str, Any]]:
    with connect() as con:
        rows = con.execute("SELECT payload FROM results ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [json.loads(row[0]) for row in rows]


def export_text(title: str, body: str, suffix: str = ".md") -> Path:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in title.strip())[:64] or "deliverable"
    path = EXPORT_DIR / f"{safe}{suffix}"
    path.write_text(body, encoding="utf-8")
    return path
