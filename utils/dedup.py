"""
SQLite-based deduplication for seen tenders.
Prevents the same tender from being reported in multiple runs.
"""

import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "output" / "seen_tenders.db"
DB_PATH = DEFAULT_DB_PATH


def set_db_path(path: str | Path) -> Path:
    """Configure the SQLite database path used by dedup helpers."""
    global DB_PATH
    DB_PATH = Path(path).expanduser()
    return DB_PATH


def get_db_path() -> Path:
    """Return the currently configured SQLite database path."""
    return DB_PATH


def _get_conn(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path).expanduser() if db_path is not None else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS seen (
            hash TEXT PRIMARY KEY,
            site TEXT,
            title TEXT,
            ref_number TEXT,
            first_seen TEXT
        )
    """)
    conn.commit()
    return conn


def _make_hash(site: str, title: str, ref_number: str) -> str:
    """Create a unique hash for a tender based on site + title + ref."""
    raw = f"{site}|{title.strip()}|{ref_number.strip()}".lower()
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def is_seen(site: str, title: str, ref_number: str, db_path: str | Path | None = None) -> bool:
    """Check if we've already reported this tender."""
    h = _make_hash(site, title, ref_number)
    conn = _get_conn(db_path)
    try:
        row = conn.execute("SELECT 1 FROM seen WHERE hash = ?", (h,)).fetchone()
        return row is not None
    finally:
        conn.close()


def mark_seen(site: str, title: str, ref_number: str, db_path: str | Path | None = None) -> None:
    """Record a tender as seen."""
    h = _make_hash(site, title, ref_number)
    conn = _get_conn(db_path)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO seen (hash, site, title, ref_number, first_seen) "
            "VALUES (?, ?, ?, ?, ?)",
            (h, site, title.strip(), ref_number.strip(), datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def purge_old(days: int = 90, db_path: str | Path | None = None) -> int:
    """Remove records older than N days to keep the DB small."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    conn = _get_conn(db_path)
    try:
        cursor = conn.execute(
            "DELETE FROM seen WHERE first_seen < ?",
            (cutoff.isoformat(),),
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()
