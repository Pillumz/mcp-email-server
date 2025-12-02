"""SQLite cache for Yandex Mail web link calculation.

Stores message index mapping (account, folder, uid) -> web_id for quick lookups.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from mcp_email_server.log import logger

if TYPE_CHECKING:
    from collections.abc import Sequence

# Default cache location
DEFAULT_CACHE_PATH = "~/.mcp-email-server/cache.db"
CACHE_PATH = Path(os.getenv("MCP_EMAIL_CACHE_PATH", DEFAULT_CACHE_PATH)).expanduser().resolve()


def _get_connection() -> sqlite3.Connection:
    """Get a connection to the cache database."""
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(CACHE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Initialize the cache database tables."""
    conn = _get_connection()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS message_index (
                account TEXT NOT NULL,
                folder TEXT NOT NULL,
                uid INTEGER NOT NULL,
                internal_date TEXT NOT NULL,
                web_id INTEGER NOT NULL,
                PRIMARY KEY (account, folder, uid)
            );

            CREATE INDEX IF NOT EXISTS idx_message_web_id
            ON message_index(account, web_id);

            CREATE INDEX IF NOT EXISTS idx_message_date
            ON message_index(account, internal_date);

            CREATE TABLE IF NOT EXISTS sync_state (
                account TEXT PRIMARY KEY,
                last_sync_date TEXT NOT NULL,
                max_web_id INTEGER NOT NULL
            );
        """)
        conn.commit()
        logger.debug(f"Cache database initialized at {CACHE_PATH}")
    finally:
        conn.close()


def get_web_id(account: str, folder: str, uid: int) -> int | None:
    """Get cached web_id for a message.

    Args:
        account: Account name
        folder: IMAP folder name
        uid: IMAP UID

    Returns:
        web_id if found in cache, None otherwise
    """
    conn = _get_connection()
    try:
        cursor = conn.execute(
            "SELECT web_id FROM message_index WHERE account = ? AND folder = ? AND uid = ?",
            (account, folder, uid)
        )
        row = cursor.fetchone()
        return row["web_id"] if row else None
    finally:
        conn.close()


def bulk_insert_messages(account: str, messages: Sequence[dict]) -> int:
    """Insert or update multiple messages into the cache.

    Args:
        account: Account name
        messages: List of dicts with keys: folder, uid, internal_date, web_id

    Returns:
        Number of messages inserted/updated
    """
    if not messages:
        return 0

    conn = _get_connection()
    try:
        conn.executemany(
            """INSERT OR REPLACE INTO message_index
               (account, folder, uid, internal_date, web_id)
               VALUES (?, ?, ?, ?, ?)""",
            [(account, m["folder"], m["uid"], m["internal_date"], m["web_id"])
             for m in messages]
        )
        conn.commit()
        logger.debug(f"Cached {len(messages)} messages for account {account}")
        return len(messages)
    finally:
        conn.close()


def get_last_sync_date(account: str) -> datetime | None:
    """Get the last sync date for an account.

    Args:
        account: Account name

    Returns:
        Last sync datetime or None if never synced
    """
    conn = _get_connection()
    try:
        cursor = conn.execute(
            "SELECT last_sync_date FROM sync_state WHERE account = ?",
            (account,)
        )
        row = cursor.fetchone()
        if row:
            return datetime.fromisoformat(row["last_sync_date"])
        return None
    finally:
        conn.close()


def get_max_web_id(account: str) -> int | None:
    """Get the maximum web_id for an account from sync state.

    Args:
        account: Account name

    Returns:
        Maximum web_id or None if never synced
    """
    conn = _get_connection()
    try:
        cursor = conn.execute(
            "SELECT max_web_id FROM sync_state WHERE account = ?",
            (account,)
        )
        row = cursor.fetchone()
        return row["max_web_id"] if row else None
    finally:
        conn.close()


def update_sync_state(account: str, last_date: datetime, max_web_id: int) -> None:
    """Update the sync state for an account.

    Args:
        account: Account name
        last_date: Date of the most recent synced message
        max_web_id: Maximum web_id assigned
    """
    conn = _get_connection()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO sync_state
               (account, last_sync_date, max_web_id)
               VALUES (?, ?, ?)""",
            (account, last_date.isoformat(), max_web_id)
        )
        conn.commit()
        logger.debug(f"Updated sync state for {account}: max_web_id={max_web_id}")
    finally:
        conn.close()


def get_all_cached_messages(account: str) -> list[dict]:
    """Get all cached messages for an account, sorted by date.

    Args:
        account: Account name

    Returns:
        List of message dicts sorted by internal_date
    """
    conn = _get_connection()
    try:
        cursor = conn.execute(
            """SELECT folder, uid, internal_date, web_id
               FROM message_index
               WHERE account = ?
               ORDER BY internal_date ASC""",
            (account,)
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def prune_old_messages(account: str, days: int = 90) -> int:
    """Remove messages older than specified days.

    Args:
        account: Account name
        days: Number of days to keep

    Returns:
        Number of messages deleted
    """
    from datetime import timedelta
    cutoff = datetime.now() - timedelta(days=days)

    conn = _get_connection()
    try:
        cursor = conn.execute(
            "DELETE FROM message_index WHERE account = ? AND internal_date < ?",
            (account, cutoff.isoformat())
        )
        conn.commit()
        deleted = cursor.rowcount
        if deleted:
            logger.info(f"Pruned {deleted} old messages from cache for {account}")
        return deleted
    finally:
        conn.close()


def clear_account_cache(account: str) -> None:
    """Clear all cached data for an account.

    Args:
        account: Account name
    """
    conn = _get_connection()
    try:
        conn.execute("DELETE FROM message_index WHERE account = ?", (account,))
        conn.execute("DELETE FROM sync_state WHERE account = ?", (account,))
        conn.commit()
        logger.info(f"Cleared cache for account {account}")
    finally:
        conn.close()


def get_cache_stats(account: str | None = None) -> dict:
    """Get cache statistics.

    Args:
        account: Optional account name to filter by

    Returns:
        Dict with cache statistics
    """
    conn = _get_connection()
    try:
        if account:
            cursor = conn.execute(
                "SELECT COUNT(*) as count FROM message_index WHERE account = ?",
                (account,)
            )
            count = cursor.fetchone()["count"]

            cursor = conn.execute(
                "SELECT max_web_id, last_sync_date FROM sync_state WHERE account = ?",
                (account,)
            )
            state = cursor.fetchone()

            return {
                "account": account,
                "message_count": count,
                "max_web_id": state["max_web_id"] if state else None,
                "last_sync": state["last_sync_date"] if state else None,
            }
        else:
            cursor = conn.execute("SELECT COUNT(*) as count FROM message_index")
            total_count = cursor.fetchone()["count"]

            cursor = conn.execute(
                "SELECT account, COUNT(*) as count FROM message_index GROUP BY account"
            )
            per_account = {row["account"]: row["count"] for row in cursor.fetchall()}

            return {
                "total_messages": total_count,
                "per_account": per_account,
                "cache_path": str(CACHE_PATH),
            }
    finally:
        conn.close()
