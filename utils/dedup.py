"""
SQLite-based deduplication for seen tenders.
Prevents the same tender from being reported in multiple runs.
"""

import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "output" / "seen_tenders.db"


def _get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
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


def is_seen(site: str, title: str, ref_number: str) -> bool:
    """Check if we've already reported this tender."""
    h = _make_hash(site, title, ref_number)
    conn = _get_conn()
    try:
        row = conn.execute("SELECT 1 FROM seen WHERE hash = ?", (h,)).fetchone()
        return row is not None
    finally:
        conn.close()


def mark_seen(site: str, title: str, ref_number: str) -> None:
    """Record a tender as seen."""
    h = _make_hash(site, title, ref_number)
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO seen (hash, site, title, ref_number, first_seen) "
            "VALUES (?, ?, ?, ?, ?)",
            (h, site, title.strip(), ref_number.strip(), datetime.utcnow().isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def purge_old(days: int = 90) -> int:
    """Remove records older than N days to keep the DB small."""
    conn = _get_conn()
    try:
        cursor = conn.execute(
            "DELETE FROM seen WHERE first_seen < datetime('now', ?)",
            (f"-{days} days",),
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()
