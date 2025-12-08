"""Tests for mcp_email_server/cache.py - SQLite cache operations."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from mcp_email_server import cache


@pytest.fixture
def temp_cache_db(tmp_path):
    """Create a temporary cache database for testing."""
    db_path = tmp_path / "test_cache.db"
    with patch.object(cache, "CACHE_PATH", db_path):
        cache.init_db()
        yield db_path
        # Cleanup
        if db_path.exists():
            db_path.unlink()


class TestCacheDatabase:
    """Test cache database initialization and basic operations."""

    def test_init_db_creates_tables(self, temp_cache_db):
        """Test that init_db creates all required tables and indexes."""
        conn = sqlite3.connect(temp_cache_db)
        try:
            cursor = conn.cursor()

            # Check message_index table exists
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='message_index'"
            )
            assert cursor.fetchone() is not None

            # Check sync_state table exists
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='sync_state'"
            )
            assert cursor.fetchone() is not None

            # Check indexes exist
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_message_mid'"
            )
            assert cursor.fetchone() is not None

            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_message_date'"
            )
            assert cursor.fetchone() is not None

        finally:
            conn.close()

    def test_init_db_creates_directory(self, tmp_path):
        """Test that init_db creates parent directories if they don't exist."""
        db_path = tmp_path / "nested" / "path" / "cache.db"
        with patch.object(cache, "CACHE_PATH", db_path):
            cache.init_db()
            assert db_path.exists()
            assert db_path.parent.exists()

    def test_init_db_idempotent(self, temp_cache_db):
        """Test that init_db can be called multiple times without error."""
        with patch.object(cache, "CACHE_PATH", temp_cache_db):
            cache.init_db()
            cache.init_db()  # Should not raise an error

            # Verify tables still exist
            conn = sqlite3.connect(temp_cache_db)
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = [row[0] for row in cursor.fetchall()]
                assert "message_index" in tables
                assert "sync_state" in tables
            finally:
                conn.close()


class TestGetMid:
    """Test get_mid function."""

    def test_get_mid_not_found(self, temp_cache_db):
        """Test get_mid returns None when message not found."""
        with patch.object(cache, "CACHE_PATH", temp_cache_db):
            result = cache.get_mid("test_account", "INBOX", 123)
            assert result is None

    def test_get_mid_found(self, temp_cache_db):
        """Test get_mid returns mid when message found."""
        with patch.object(cache, "CACHE_PATH", temp_cache_db):
            # Insert a message
            messages = [{
                "folder": "INBOX",
                "uid": 123,
                "internal_date": datetime.now().isoformat(),
                "mid": 456,
            }]
            cache.bulk_insert_messages("test_account", messages)

            # Retrieve it
            result = cache.get_mid("test_account", "INBOX", 123)
            assert result == 456

    def test_get_mid_different_folders(self, temp_cache_db):
        """Test get_mid correctly distinguishes between folders."""
        with patch.object(cache, "CACHE_PATH", temp_cache_db):
            # Insert messages in different folders with same UID
            messages = [
                {
                    "folder": "INBOX",
                    "uid": 123,
                    "internal_date": datetime.now().isoformat(),
                    "mid": 456,
                },
                {
                    "folder": "Sent",
                    "uid": 123,
                    "internal_date": datetime.now().isoformat(),
                    "mid": 789,
                },
            ]
            cache.bulk_insert_messages("test_account", messages)

            # Verify each returns correct mid
            assert cache.get_mid("test_account", "INBOX", 123) == 456
            assert cache.get_mid("test_account", "Sent", 123) == 789

    def test_get_mid_different_accounts(self, temp_cache_db):
        """Test get_mid correctly distinguishes between accounts."""
        with patch.object(cache, "CACHE_PATH", temp_cache_db):
            # Insert messages for different accounts
            cache.bulk_insert_messages("account1", [{
                "folder": "INBOX",
                "uid": 123,
                "internal_date": datetime.now().isoformat(),
                "mid": 456,
            }])
            cache.bulk_insert_messages("account2", [{
                "folder": "INBOX",
                "uid": 123,
                "internal_date": datetime.now().isoformat(),
                "mid": 789,
            }])

            # Verify each returns correct mid
            assert cache.get_mid("account1", "INBOX", 123) == 456
            assert cache.get_mid("account2", "INBOX", 123) == 789


class TestBulkInsertMessages:
    """Test bulk_insert_messages function."""

    def test_bulk_insert_empty_list(self, temp_cache_db):
        """Test bulk_insert with empty list returns 0."""
        with patch.object(cache, "CACHE_PATH", temp_cache_db):
            result = cache.bulk_insert_messages("test_account", [])
            assert result == 0

    def test_bulk_insert_single_message(self, temp_cache_db):
        """Test bulk_insert with single message."""
        with patch.object(cache, "CACHE_PATH", temp_cache_db):
            messages = [{
                "folder": "INBOX",
                "uid": 123,
                "internal_date": "2025-01-01T00:00:00",
                "mid": 456,
            }]
            result = cache.bulk_insert_messages("test_account", messages)
            assert result == 1

            # Verify it was inserted
            mid = cache.get_mid("test_account", "INBOX", 123)
            assert mid == 456

    def test_bulk_insert_multiple_messages(self, temp_cache_db):
        """Test bulk_insert with multiple messages."""
        with patch.object(cache, "CACHE_PATH", temp_cache_db):
            messages = [
                {
                    "folder": "INBOX",
                    "uid": i,
                    "internal_date": f"2025-01-{i:02d}T00:00:00",
                    "mid": i * 100,
                }
                for i in range(1, 11)
            ]
            result = cache.bulk_insert_messages("test_account", messages)
            assert result == 10

            # Verify all were inserted
            for i in range(1, 11):
                mid = cache.get_mid("test_account", "INBOX", i)
                assert mid == i * 100

    def test_bulk_insert_replace_existing(self, temp_cache_db):
        """Test bulk_insert replaces existing messages."""
        with patch.object(cache, "CACHE_PATH", temp_cache_db):
            # Insert initial message
            messages = [{
                "folder": "INBOX",
                "uid": 123,
                "internal_date": "2025-01-01T00:00:00",
                "mid": 456,
            }]
            cache.bulk_insert_messages("test_account", messages)

            # Replace with updated mid
            updated_messages = [{
                "folder": "INBOX",
                "uid": 123,
                "internal_date": "2025-01-01T00:00:00",
                "mid": 789,
            }]
            result = cache.bulk_insert_messages("test_account", updated_messages)
            assert result == 1

            # Verify it was updated
            mid = cache.get_mid("test_account", "INBOX", 123)
            assert mid == 789


class TestSyncState:
    """Test sync state management functions."""

    def test_get_last_sync_date_none(self, temp_cache_db):
        """Test get_last_sync_date returns None for new account."""
        with patch.object(cache, "CACHE_PATH", temp_cache_db):
            result = cache.get_last_sync_date("test_account")
            assert result is None

    def test_get_max_mid_none(self, temp_cache_db):
        """Test get_max_mid returns None for new account."""
        with patch.object(cache, "CACHE_PATH", temp_cache_db):
            result = cache.get_max_mid("test_account")
            assert result is None

    def test_update_and_get_sync_state(self, temp_cache_db):
        """Test updating and retrieving sync state."""
        with patch.object(cache, "CACHE_PATH", temp_cache_db):
            last_date = datetime(2025, 1, 15, 12, 30, 0)
            max_mid = 12345

            # Update sync state
            cache.update_sync_state("test_account", last_date, max_mid)

            # Verify retrieval
            retrieved_date = cache.get_last_sync_date("test_account")
            assert retrieved_date == last_date

            retrieved_mid = cache.get_max_mid("test_account")
            assert retrieved_mid == max_mid

    def test_update_sync_state_replace(self, temp_cache_db):
        """Test updating sync state replaces existing values."""
        with patch.object(cache, "CACHE_PATH", temp_cache_db):
            # Insert initial state
            cache.update_sync_state("test_account", datetime(2025, 1, 1), 100)

            # Update with new state
            new_date = datetime(2025, 1, 15)
            new_mid = 200
            cache.update_sync_state("test_account", new_date, new_mid)

            # Verify new state
            assert cache.get_last_sync_date("test_account") == new_date
            assert cache.get_max_mid("test_account") == new_mid

    def test_sync_state_different_accounts(self, temp_cache_db):
        """Test sync state is account-specific."""
        with patch.object(cache, "CACHE_PATH", temp_cache_db):
            # Update state for different accounts
            cache.update_sync_state("account1", datetime(2025, 1, 1), 100)
            cache.update_sync_state("account2", datetime(2025, 1, 15), 200)

            # Verify each account has correct state
            assert cache.get_max_mid("account1") == 100
            assert cache.get_max_mid("account2") == 200


class TestGetAllCachedMessages:
    """Test get_all_cached_messages function."""

    def test_get_all_cached_messages_empty(self, temp_cache_db):
        """Test get_all_cached_messages returns empty list for new account."""
        with patch.object(cache, "CACHE_PATH", temp_cache_db):
            result = cache.get_all_cached_messages("test_account")
            assert result == []

    def test_get_all_cached_messages_sorted_by_date(self, temp_cache_db):
        """Test get_all_cached_messages returns messages sorted by date."""
        with patch.object(cache, "CACHE_PATH", temp_cache_db):
            # Insert messages in random order
            messages = [
                {
                    "folder": "INBOX",
                    "uid": 3,
                    "internal_date": "2025-01-15T00:00:00",
                    "mid": 300,
                },
                {
                    "folder": "INBOX",
                    "uid": 1,
                    "internal_date": "2025-01-10T00:00:00",
                    "mid": 100,
                },
                {
                    "folder": "INBOX",
                    "uid": 2,
                    "internal_date": "2025-01-12T00:00:00",
                    "mid": 200,
                },
            ]
            cache.bulk_insert_messages("test_account", messages)

            # Retrieve and verify order
            result = cache.get_all_cached_messages("test_account")
            assert len(result) == 3
            assert result[0]["uid"] == 1
            assert result[1]["uid"] == 2
            assert result[2]["uid"] == 3

    def test_get_all_cached_messages_account_specific(self, temp_cache_db):
        """Test get_all_cached_messages filters by account."""
        with patch.object(cache, "CACHE_PATH", temp_cache_db):
            # Insert messages for different accounts
            cache.bulk_insert_messages("account1", [{
                "folder": "INBOX",
                "uid": 1,
                "internal_date": "2025-01-01T00:00:00",
                "mid": 100,
            }])
            cache.bulk_insert_messages("account2", [{
                "folder": "INBOX",
                "uid": 2,
                "internal_date": "2025-01-01T00:00:00",
                "mid": 200,
            }])

            # Verify each account gets only its messages
            result1 = cache.get_all_cached_messages("account1")
            assert len(result1) == 1
            assert result1[0]["uid"] == 1

            result2 = cache.get_all_cached_messages("account2")
            assert len(result2) == 1
            assert result2[0]["uid"] == 2


class TestPruneOldMessages:
    """Test prune_old_messages function."""

    def test_prune_old_messages_none_to_delete(self, temp_cache_db):
        """Test prune_old_messages returns 0 when no old messages."""
        with patch.object(cache, "CACHE_PATH", temp_cache_db):
            # Insert recent message
            messages = [{
                "folder": "INBOX",
                "uid": 1,
                "internal_date": datetime.now().isoformat(),
                "mid": 100,
            }]
            cache.bulk_insert_messages("test_account", messages)

            # Prune old messages
            deleted = cache.prune_old_messages("test_account", days=90)
            assert deleted == 0

            # Verify message still exists
            assert cache.get_mid("test_account", "INBOX", 1) == 100

    def test_prune_old_messages_deletes_old(self, temp_cache_db):
        """Test prune_old_messages deletes messages older than cutoff."""
        with patch.object(cache, "CACHE_PATH", temp_cache_db):
            # Insert old and new messages
            old_date = (datetime.now() - timedelta(days=100)).isoformat()
            new_date = datetime.now().isoformat()

            messages = [
                {"folder": "INBOX", "uid": 1, "internal_date": old_date, "mid": 100},
                {"folder": "INBOX", "uid": 2, "internal_date": new_date, "mid": 200},
            ]
            cache.bulk_insert_messages("test_account", messages)

            # Prune messages older than 90 days
            deleted = cache.prune_old_messages("test_account", days=90)
            assert deleted == 1

            # Verify old message deleted, new message remains
            assert cache.get_mid("test_account", "INBOX", 1) is None
            assert cache.get_mid("test_account", "INBOX", 2) == 200

    def test_prune_old_messages_custom_days(self, temp_cache_db):
        """Test prune_old_messages with custom days parameter."""
        with patch.object(cache, "CACHE_PATH", temp_cache_db):
            # Insert messages at different ages
            dates = [
                (datetime.now() - timedelta(days=50)).isoformat(),  # 50 days old
                (datetime.now() - timedelta(days=20)).isoformat(),  # 20 days old
                datetime.now().isoformat(),  # today
            ]

            messages = [
                {"folder": "INBOX", "uid": i, "internal_date": date, "mid": i * 100}
                for i, date in enumerate(dates, 1)
            ]
            cache.bulk_insert_messages("test_account", messages)

            # Prune messages older than 30 days
            deleted = cache.prune_old_messages("test_account", days=30)
            assert deleted == 1  # Only first message should be deleted

            # Verify
            assert cache.get_mid("test_account", "INBOX", 1) is None
            assert cache.get_mid("test_account", "INBOX", 2) == 200
            assert cache.get_mid("test_account", "INBOX", 3) == 300


class TestClearAccountCache:
    """Test clear_account_cache function."""

    def test_clear_account_cache_messages_only(self, temp_cache_db):
        """Test clear_account_cache removes all messages for account."""
        with patch.object(cache, "CACHE_PATH", temp_cache_db):
            # Insert messages for multiple accounts
            cache.bulk_insert_messages("account1", [{
                "folder": "INBOX",
                "uid": 1,
                "internal_date": datetime.now().isoformat(),
                "mid": 100,
            }])
            cache.bulk_insert_messages("account2", [{
                "folder": "INBOX",
                "uid": 2,
                "internal_date": datetime.now().isoformat(),
                "mid": 200,
            }])

            # Clear account1
            cache.clear_account_cache("account1")

            # Verify account1 cleared, account2 remains
            assert cache.get_mid("account1", "INBOX", 1) is None
            assert cache.get_mid("account2", "INBOX", 2) == 200

    def test_clear_account_cache_sync_state(self, temp_cache_db):
        """Test clear_account_cache removes sync state."""
        with patch.object(cache, "CACHE_PATH", temp_cache_db):
            # Set sync state
            cache.update_sync_state("test_account", datetime.now(), 100)

            # Clear cache
            cache.clear_account_cache("test_account")

            # Verify sync state cleared
            assert cache.get_last_sync_date("test_account") is None
            assert cache.get_max_mid("test_account") is None

    def test_clear_account_cache_nonexistent(self, temp_cache_db):
        """Test clear_account_cache handles nonexistent account gracefully."""
        with patch.object(cache, "CACHE_PATH", temp_cache_db):
            # Should not raise error
            cache.clear_account_cache("nonexistent_account")


class TestGetCacheStats:
    """Test get_cache_stats function."""

    def test_get_cache_stats_empty(self, temp_cache_db):
        """Test get_cache_stats with empty cache."""
        with patch.object(cache, "CACHE_PATH", temp_cache_db):
            stats = cache.get_cache_stats()
            assert stats["total_messages"] == 0
            assert stats["per_account"] == {}
            assert "cache_path" in stats

    def test_get_cache_stats_global(self, temp_cache_db):
        """Test get_cache_stats returns global statistics."""
        with patch.object(cache, "CACHE_PATH", temp_cache_db):
            # Insert messages for multiple accounts
            cache.bulk_insert_messages("account1", [
                {"folder": "INBOX", "uid": 1, "internal_date": datetime.now().isoformat(), "mid": 100},
                {"folder": "INBOX", "uid": 2, "internal_date": datetime.now().isoformat(), "mid": 200},
            ])
            cache.bulk_insert_messages("account2", [
                {"folder": "INBOX", "uid": 3, "internal_date": datetime.now().isoformat(), "mid": 300},
            ])

            stats = cache.get_cache_stats()
            assert stats["total_messages"] == 3
            assert stats["per_account"]["account1"] == 2
            assert stats["per_account"]["account2"] == 1

    def test_get_cache_stats_specific_account(self, temp_cache_db):
        """Test get_cache_stats for specific account."""
        with patch.object(cache, "CACHE_PATH", temp_cache_db):
            # Insert messages and sync state
            cache.bulk_insert_messages("test_account", [
                {"folder": "INBOX", "uid": 1, "internal_date": datetime.now().isoformat(), "mid": 100},
                {"folder": "INBOX", "uid": 2, "internal_date": datetime.now().isoformat(), "mid": 200},
            ])
            cache.update_sync_state("test_account", datetime.now(), 200)

            stats = cache.get_cache_stats("test_account")
            assert stats["account"] == "test_account"
            assert stats["message_count"] == 2
            assert stats["max_mid"] == 200
            assert stats["last_sync"] is not None

    def test_get_cache_stats_account_no_sync_state(self, temp_cache_db):
        """Test get_cache_stats for account without sync state."""
        with patch.object(cache, "CACHE_PATH", temp_cache_db):
            # Insert messages without sync state
            cache.bulk_insert_messages("test_account", [{
                "folder": "INBOX",
                "uid": 1,
                "internal_date": datetime.now().isoformat(),
                "mid": 100,
            }])

            stats = cache.get_cache_stats("test_account")
            assert stats["message_count"] == 1
            assert stats["max_mid"] is None
            assert stats["last_sync"] is None


class TestCachePath:
    """Test cache path configuration."""

    def test_default_cache_path(self):
        """Test default cache path is correct."""
        assert cache.DEFAULT_CACHE_PATH == "~/.mcp-email-server/cache.db"

    def test_custom_cache_path_from_env(self, tmp_path):
        """Test cache path can be set via environment variable."""
        custom_path = tmp_path / "custom_cache.db"
        with patch.dict(os.environ, {"MCP_EMAIL_CACHE_PATH": str(custom_path)}):
            # Re-import to pick up new env var
            import importlib
            importlib.reload(cache)

            # CACHE_PATH should be the expanded absolute path
            assert cache.CACHE_PATH == custom_path.resolve()
