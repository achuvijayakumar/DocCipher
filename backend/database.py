"""SQLite persistence for crack history."""

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

_lock = threading.Lock()
_db_path: Optional[Path] = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS history (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    original_filename TEXT    NOT NULL,
    original_path     TEXT,
    unlocked_filename TEXT,
    unlocked_path     TEXT,
    file_size_before  INTEGER DEFAULT 0,
    file_size_after   INTEGER DEFAULT 0,
    status            TEXT    NOT NULL,
    error             TEXT,
    duration          REAL    DEFAULT 0,
    protections_found INTEGER DEFAULT 0,
    file_format       TEXT    DEFAULT 'docx',
    method            TEXT,
    timestamp         TEXT    NOT NULL,
    logs              TEXT
);
CREATE INDEX IF NOT EXISTS idx_history_timestamp ON history(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_history_status ON history(status);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Columns added after v1.0.0. Existing databases are migrated in place on
# startup so an upgrade never loses a user's history.
MIGRATIONS = [
    ("file_format", "ALTER TABLE history ADD COLUMN file_format TEXT DEFAULT 'docx'"),
    ("method", "ALTER TABLE history ADD COLUMN method TEXT"),
]


def init(db_path: Path) -> None:
    """Create the database file and schema if absent."""
    global _db_path
    _db_path = Path(db_path)
    _db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after the first release, if they are missing.

    Indexes on those columns are created here rather than in SCHEMA, because on
    an upgraded database the column does not exist until this runs.
    """
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(history)")}
    for column, statement in MIGRATIONS:
        if column not in existing:
            conn.execute(statement)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_history_format ON history(file_format)")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """Serialized connection. Writes are infrequent; a lock is simpler than a pool."""
    if _db_path is None:
        raise RuntimeError("database.init() has not been called")
    with _lock:
        conn = sqlite3.connect(_db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


def record(result: dict) -> int:
    """Insert one crack result. Returns the new row id."""
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO history (
                original_filename, original_path, unlocked_filename, unlocked_path,
                file_size_before, file_size_after, status, error, duration,
                protections_found, file_format, method, timestamp, logs
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.get("input_name") or "",
                result.get("input_path"),
                result.get("output_name"),
                result.get("output_path"),
                result.get("size_before", 0),
                result.get("size_after", 0),
                result.get("status", "failed"),
                result.get("error"),
                result.get("duration", 0.0),
                result.get("protections_found", 0),
                result.get("format", "docx"),
                result.get("method"),
                datetime.now().isoformat(timespec="seconds"),
                json.dumps(result.get("logs", [])),
            ),
        )
        return cur.lastrowid


def list_history(
    search: Optional[str] = None,
    status: Optional[str] = None,
    file_format: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """Return history rows, newest first, optionally filtered."""
    clauses, params = [], []
    if search:
        clauses.append("(original_filename LIKE ? OR unlocked_filename LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])
    if status and status.lower() != "all":
        clauses.append("status = ?")
        params.append(status.lower())
    if file_format and file_format.lower() != "all":
        clauses.append("file_format = ?")
        params.append(file_format.lower())

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.extend([max(1, min(limit, 500)), max(0, offset)])

    with connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM history {where} ORDER BY id DESC LIMIT ? OFFSET ?", params
        ).fetchall()
    return [dict(row) for row in rows]


def count_history(
    search: Optional[str] = None,
    status: Optional[str] = None,
    file_format: Optional[str] = None,
) -> int:
    """Total rows matching the same filters list_history() uses."""
    clauses, params = [], []
    if search:
        clauses.append("(original_filename LIKE ? OR unlocked_filename LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])
    if status and status.lower() != "all":
        clauses.append("status = ?")
        params.append(status.lower())
    if file_format and file_format.lower() != "all":
        clauses.append("file_format = ?")
        params.append(file_format.lower())

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with connect() as conn:
        return conn.execute(f"SELECT COUNT(*) AS n FROM history {where}", params).fetchone()["n"]


def get(entry_id: int) -> Optional[dict]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM history WHERE id = ?", (entry_id,)).fetchone()
    return dict(row) if row else None


def stats() -> dict:
    """Aggregate counters for the UI header."""
    with connect() as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(*)                                              AS total,
                COALESCE(SUM(status = 'success'), 0)                  AS successes,
                COALESCE(SUM(status = 'failed'), 0)                   AS failures,
                COALESCE(SUM(protections_found), 0)                   AS protections,
                COALESCE(ROUND(AVG(NULLIF(duration, 0)), 2), 0)       AS avg_duration,
                COALESCE(SUM(
                    CASE WHEN status = 'success' AND file_size_after > 0
                         THEN file_size_before - file_size_after ELSE 0 END
                ), 0)                                                 AS bytes_saved,
                COALESCE(SUM(file_format = 'docx'), 0)                AS docx_count,
                COALESCE(SUM(file_format = 'pdf'), 0)                 AS pdf_count,
                COALESCE(SUM(file_format = 'xlsx'), 0)                AS xlsx_count
            FROM history
            """
        ).fetchone()
    return dict(row)


def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    """Read a persisted UI preference (e.g. the chosen theme)."""
    with connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    """Persist a UI preference.

    Stored server-side rather than in localStorage so the choice survives a
    WebView2 profile reset, and so the app window and browser fallback agree.
    """
    with connect() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def clear() -> int:
    """Delete all history rows. Returns the number removed."""
    with connect() as conn:
        cur = conn.execute("DELETE FROM history")
        return cur.rowcount


def delete(entry_id: int) -> bool:
    with connect() as conn:
        cur = conn.execute("DELETE FROM history WHERE id = ?", (entry_id,))
        return cur.rowcount > 0
