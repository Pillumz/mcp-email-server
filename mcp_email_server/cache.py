"""SQLite cache for Yandex Mail MID (message ID) storage.

Stores message index mapping (account, folder, uid) -> mid for quick lookups.
The MID is the actual Yandex Mail message ID used in web URLs.
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
                mid INTEGER NOT NULL,
                tid INTEGER,
                PRIMARY KEY (account, folder, uid)
            );

            CREATE INDEX IF NOT EXISTS idx_message_mid
            ON message_index(account, mid);

            CREATE INDEX IF NOT EXISTS idx_message_tid
            ON message_index(account, tid);

            CREATE INDEX IF NOT EXISTS idx_message_date
            ON message_index(account, internal_date);

            CREATE TABLE IF NOT EXISTS sync_state (
                account TEXT PRIMARY KEY,
                last_sync_date TEXT NOT NULL,
                max_mid INTEGER NOT NULL
            );

            -- Per-folder reference point for MID estimation
            CREATE TABLE IF NOT EXISTS folder_reference (
                account TEXT NOT NULL,
                folder TEXT NOT NULL,
                ref_uid INTEGER NOT NULL,
                ref_mid INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (account, folder)
            );

            -- Email metadata cache (subject, sender, date, flags)
            CREATE TABLE IF NOT EXISTS email_metadata (
                account TEXT NOT NULL,
                folder TEXT NOT NULL,
                uid INTEGER NOT NULL,
                subject TEXT,
                sender TEXT,
                recipients TEXT,
                date TEXT,
                flags TEXT,
                cached_at TEXT NOT NULL,
                PRIMARY KEY (account, folder, uid)
            );

            CREATE INDEX IF NOT EXISTS idx_metadata_date
            ON email_metadata(account, folder, date DESC);

            -- Email body cache
            CREATE TABLE IF NOT EXISTS email_body (
                account TEXT NOT NULL,
                folder TEXT NOT NULL,
                uid INTEGER NOT NULL,
                body_text TEXT,
                body_html TEXT,
                attachments TEXT,
                cached_at TEXT NOT NULL,
                PRIMARY KEY (account, folder, uid)
            );

            -- Folder watermark (highest UID seen)
            CREATE TABLE IF NOT EXISTS folder_watermark (
                account TEXT NOT NULL,
                folder TEXT NOT NULL,
                highest_uid INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (account, folder)
            );
        """)
        conn.commit()
        logger.debug(f"Cache database initialized at {CACHE_PATH}")
    finally:
        conn.close()


def get_mid(account: str, folder: str, uid: int) -> int | None:
    """Get cached MID for a message.

    Args:
        account: Account name
        folder: IMAP folder name
        uid: IMAP UID

    Returns:
        MID if found in cache, None otherwise
    """
    conn = _get_connection()
    try:
        cursor = conn.execute(
            "SELECT mid FROM message_index WHERE account = ? AND folder = ? AND uid = ?",
            (account, folder, uid)
        )
        row = cursor.fetchone()
        return row["mid"] if row else None
    finally:
        conn.close()


def get_message_ids(account: str, folder: str, uid: int) -> tuple[int, int] | None:
    """Get cached MID and TID for a message.

    Args:
        account: Account name
        folder: IMAP folder name
        uid: IMAP UID

    Returns:
        Tuple of (mid, tid) if found in cache, None otherwise.
        If tid is NULL in database, returns (mid, mid) as fallback.
    """
    conn = _get_connection()
    try:
        cursor = conn.execute(
            "SELECT mid, tid FROM message_index WHERE account = ? AND folder = ? AND uid = ?",
            (account, folder, uid)
        )
        row = cursor.fetchone()
        if row:
            mid = row["mid"]
            tid = row["tid"] if row["tid"] else mid  # Fallback to mid if tid is NULL
            return (mid, tid)
        return None
    finally:
        conn.close()


def bulk_insert_messages(account: str, messages: Sequence[dict]) -> int:
    """Insert or update multiple messages into the cache.

    Args:
        account: Account name
        messages: List of dicts with keys: folder, uid, internal_date, mid, tid (optional)

    Returns:
        Number of messages inserted/updated
    """
    if not messages:
        return 0

    conn = _get_connection()
    try:
        conn.executemany(
            """INSERT OR REPLACE INTO message_index
               (account, folder, uid, internal_date, mid, tid)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [(account, m["folder"], m["uid"], m["internal_date"], m["mid"], m.get("tid"))
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


def get_max_mid(account: str) -> int | None:
    """Get the maximum MID for an account from sync state.

    Args:
        account: Account name

    Returns:
        Maximum MID or None if never synced
    """
    conn = _get_connection()
    try:
        cursor = conn.execute(
            "SELECT max_mid FROM sync_state WHERE account = ?",
            (account,)
        )
        row = cursor.fetchone()
        return row["max_mid"] if row else None
    finally:
        conn.close()


def update_sync_state(account: str, last_date: datetime, max_mid: int) -> None:
    """Update the sync state for an account.

    Args:
        account: Account name
        last_date: Date of the most recent synced message
        max_mid: Maximum MID synced
    """
    conn = _get_connection()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO sync_state
               (account, last_sync_date, max_mid)
               VALUES (?, ?, ?)""",
            (account, last_date.isoformat(), max_mid)
        )
        conn.commit()
        logger.debug(f"Updated sync state for {account}: max_mid={max_mid}")
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
            """SELECT folder, uid, internal_date, mid
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
                "SELECT max_mid, last_sync_date FROM sync_state WHERE account = ?",
                (account,)
            )
            state = cursor.fetchone()

            return {
                "account": account,
                "message_count": count,
                "max_mid": state["max_mid"] if state else None,
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


# Folder reference functions for MID estimation


def get_folder_reference(account: str, folder: str) -> tuple[int, int] | None:
    """Get the reference (UID, MID) pair for a folder.

    Args:
        account: Account name
        folder: IMAP folder name

    Returns:
        Tuple of (ref_uid, ref_mid) or None if not set
    """
    conn = _get_connection()
    try:
        cursor = conn.execute(
            "SELECT ref_uid, ref_mid FROM folder_reference WHERE account = ? AND folder = ?",
            (account, folder)
        )
        row = cursor.fetchone()
        if row:
            return (row["ref_uid"], row["ref_mid"])
        return None
    finally:
        conn.close()


def set_folder_reference(account: str, folder: str, ref_uid: int, ref_mid: int) -> None:
    """Set the reference (UID, MID) pair for a folder.

    Args:
        account: Account name
        folder: IMAP folder name
        ref_uid: Reference IMAP UID
        ref_mid: Reference MID for that UID
    """
    conn = _get_connection()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO folder_reference
               (account, folder, ref_uid, ref_mid, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (account, folder, ref_uid, ref_mid, datetime.now().isoformat())
        )
        conn.commit()
        logger.debug(f"Set folder reference for {account}/{folder}: uid={ref_uid}, mid={ref_mid}")
    finally:
        conn.close()


def get_all_folder_references(account: str) -> dict[str, tuple[int, int]]:
    """Get all folder references for an account.

    Args:
        account: Account name

    Returns:
        Dict mapping folder name to (ref_uid, ref_mid) tuple
    """
    conn = _get_connection()
    try:
        cursor = conn.execute(
            "SELECT folder, ref_uid, ref_mid FROM folder_reference WHERE account = ?",
            (account,)
        )
        return {row["folder"]: (row["ref_uid"], row["ref_mid"]) for row in cursor.fetchall()}
    finally:
        conn.close()


# =============================================================================
# Folder Watermark Functions (for incremental sync)
# =============================================================================


def get_watermark(account: str, folder: str) -> int:
    """Get the highest UID seen for a folder.

    Args:
        account: Account name
        folder: IMAP folder name

    Returns:
        Highest UID, or 0 if never synced
    """
    conn = _get_connection()
    try:
        cursor = conn.execute(
            "SELECT highest_uid FROM folder_watermark WHERE account = ? AND folder = ?",
            (account, folder)
        )
        row = cursor.fetchone()
        return row["highest_uid"] if row else 0
    finally:
        conn.close()


def set_watermark(account: str, folder: str, highest_uid: int) -> None:
    """Set the highest UID seen for a folder.

    Args:
        account: Account name
        folder: IMAP folder name
        highest_uid: Highest UID to store
    """
    conn = _get_connection()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO folder_watermark
               (account, folder, highest_uid, updated_at)
               VALUES (?, ?, ?, ?)""",
            (account, folder, highest_uid, datetime.now().isoformat())
        )
        conn.commit()
        logger.debug(f"Set watermark for {account}/{folder}: uid={highest_uid}")
    finally:
        conn.close()


# =============================================================================
# Email Metadata Cache Functions
# =============================================================================


def store_metadata(account: str, folder: str, emails: Sequence[dict]) -> int:
    """Store email metadata in cache.

    Args:
        account: Account name
        folder: IMAP folder name
        emails: List of dicts with keys: uid, subject, sender, recipients, date, flags

    Returns:
        Number of emails cached
    """
    if not emails:
        return 0

    import json

    conn = _get_connection()
    try:
        now = datetime.now().isoformat()
        conn.executemany(
            """INSERT OR REPLACE INTO email_metadata
               (account, folder, uid, subject, sender, recipients, date, flags, cached_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [(
                account,
                folder,
                e["uid"],
                e.get("subject"),
                e.get("sender"),
                json.dumps(e.get("recipients", [])),
                e.get("date"),
                json.dumps(e.get("flags", [])),
                now,
            ) for e in emails]
        )
        conn.commit()
        logger.debug(f"Cached {len(emails)} metadata entries for {account}/{folder}")
        return len(emails)
    finally:
        conn.close()


def get_metadata_page(
    account: str,
    folder: str,
    page: int = 1,
    page_size: int = 10,
    order: str = "desc",
) -> tuple[list[dict], int]:
    """Get a page of cached email metadata.

    Args:
        account: Account name
        folder: IMAP folder name
        page: Page number (1-indexed)
        page_size: Number of items per page
        order: Sort order ('asc' or 'desc' by UID)

    Returns:
        Tuple of (list of metadata dicts, total count)
    """
    import json

    conn = _get_connection()
    try:
        # Get total count
        cursor = conn.execute(
            "SELECT COUNT(*) as count FROM email_metadata WHERE account = ? AND folder = ?",
            (account, folder)
        )
        total = cursor.fetchone()["count"]

        # Get page
        offset = (page - 1) * page_size
        order_dir = "DESC" if order == "desc" else "ASC"

        cursor = conn.execute(
            f"""SELECT uid, subject, sender, recipients, date, flags
                FROM email_metadata
                WHERE account = ? AND folder = ?
                ORDER BY uid {order_dir}
                LIMIT ? OFFSET ?""",
            (account, folder, page_size, offset)
        )

        results = []
        for row in cursor.fetchall():
            results.append({
                "uid": row["uid"],
                "subject": row["subject"],
                "sender": row["sender"],
                "recipients": json.loads(row["recipients"]) if row["recipients"] else [],
                "date": row["date"],
                "flags": json.loads(row["flags"]) if row["flags"] else [],
            })

        return results, total
    finally:
        conn.close()


def get_cached_uids(account: str, folder: str) -> set[int]:
    """Get all cached UIDs for a folder.

    Args:
        account: Account name
        folder: IMAP folder name

    Returns:
        Set of cached UIDs
    """
    conn = _get_connection()
    try:
        cursor = conn.execute(
            "SELECT uid FROM email_metadata WHERE account = ? AND folder = ?",
            (account, folder)
        )
        return {row["uid"] for row in cursor.fetchall()}
    finally:
        conn.close()


def get_metadata_for_uid(account: str, folder: str, uid: int) -> dict | None:
    """Get cached metadata for a specific email.

    Args:
        account: Account name
        folder: IMAP folder name
        uid: Email UID

    Returns:
        Metadata dict or None if not cached
    """
    import json

    conn = _get_connection()
    try:
        cursor = conn.execute(
            """SELECT uid, subject, sender, recipients, date, flags
               FROM email_metadata
               WHERE account = ? AND folder = ? AND uid = ?""",
            (account, folder, uid)
        )
        row = cursor.fetchone()
        if row:
            return {
                "uid": row["uid"],
                "subject": row["subject"],
                "sender": row["sender"],
                "recipients": json.loads(row["recipients"]) if row["recipients"] else [],
                "date": row["date"],
                "flags": json.loads(row["flags"]) if row["flags"] else [],
            }
        return None
    finally:
        conn.close()


# =============================================================================
# Email Body Cache Functions
# =============================================================================


def get_body(account: str, folder: str, uid: int) -> dict | None:
    """Get cached email body.

    Args:
        account: Account name
        folder: IMAP folder name
        uid: Email UID

    Returns:
        Dict with body_text, body_html, attachments, or None if not cached
    """
    import json

    conn = _get_connection()
    try:
        cursor = conn.execute(
            "SELECT body_text, body_html, attachments FROM email_body WHERE account = ? AND folder = ? AND uid = ?",
            (account, folder, uid)
        )
        row = cursor.fetchone()
        if row:
            return {
                "body_text": row["body_text"],
                "body_html": row["body_html"],
                "attachments": json.loads(row["attachments"]) if row["attachments"] else [],
            }
        return None
    finally:
        conn.close()


def store_body(account: str, folder: str, uid: int, body_text: str | None, body_html: str | None, attachments: list | None) -> None:
    """Store email body in cache.

    Args:
        account: Account name
        folder: IMAP folder name
        uid: Email UID
        body_text: Plain text body
        body_html: HTML body
        attachments: List of attachment info dicts
    """
    import json

    conn = _get_connection()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO email_body
               (account, folder, uid, body_text, body_html, attachments, cached_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                account,
                folder,
                uid,
                body_text,
                body_html,
                json.dumps(attachments or []),
                datetime.now().isoformat(),
            )
        )
        conn.commit()
        logger.debug(f"Cached body for {account}/{folder}/{uid}")
    finally:
        conn.close()


def delete_email(account: str, folder: str, uid: int) -> None:
    """Delete an email from all cache tables.

    Args:
        account: Account name
        folder: IMAP folder name
        uid: Email UID
    """
    conn = _get_connection()
    try:
        conn.execute(
            "DELETE FROM email_metadata WHERE account = ? AND folder = ? AND uid = ?",
            (account, folder, uid)
        )
        conn.execute(
            "DELETE FROM email_body WHERE account = ? AND folder = ? AND uid = ?",
            (account, folder, uid)
        )
        conn.execute(
            "DELETE FROM message_index WHERE account = ? AND folder = ? AND uid = ?",
            (account, folder, uid)
        )
        conn.commit()
        logger.debug(f"Deleted cached email {account}/{folder}/{uid}")
    finally:
        conn.close()
