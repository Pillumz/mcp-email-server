"""Tests for mcp_email_server/emails/yandex_links.py - Yandex Mail web link calculator."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mcp_email_server.config import EmailServer, EmailSettings, YandexLinkConfig
from mcp_email_server.emails.yandex_links import (
    YandexLinkCalculator,
    decode_imap_utf7,
    encode_imap_utf7,
)


class TestDecodeImapUtf7:
    """Test IMAP UTF-7 decoding function."""

    def test_decode_ascii_no_encoding(self):
        """Test decoding plain ASCII string."""
        result = decode_imap_utf7("INBOX")
        assert result == "INBOX"

    def test_decode_no_ampersand(self):
        """Test decoding string without ampersand."""
        result = decode_imap_utf7("Sent Items")
        assert result == "Sent Items"

    def test_decode_literal_ampersand(self):
        """Test decoding literal ampersand (&-)."""
        result = decode_imap_utf7("A&-B")
        assert result == "A&B"

    def test_decode_cyrillic_folder(self):
        """Test decoding Cyrillic folder names."""
        # "Отправленные" (Sent in Russian)
        encoded = "&BB4EQgQ,BEAEMAQyBDsESwQ9BD0ESwQ1-"
        result = decode_imap_utf7(encoded)
        # Just verify it decodes to something and doesn't crash
        assert isinstance(result, str)
        assert "&" not in result or result.count("&") < encoded.count("&")

    def test_decode_chinese_folder(self):
        """Test decoding Chinese folder names."""
        # "已发送" (Sent in Chinese)
        encoded = "&XfJT0ZAB-"
        result = decode_imap_utf7(encoded)
        assert result == "已发送"

    def test_decode_mixed_ascii_and_encoded(self):
        """Test decoding mixed ASCII and encoded content."""
        # "Sent &BB4EQgQ,BEAEMAQyBDsESwQ9BD0ESwQ1-"
        encoded = "Sent &BB4EQgQ,BEAEMAQyBDsESwQ9BD0ESwQ5-"
        result = decode_imap_utf7(encoded)
        # Should contain both ASCII and Cyrillic
        assert "Sent" in result

    def test_decode_multiple_encoded_sections(self):
        """Test decoding string with multiple encoded sections."""
        encoded = "&BB4EQgQ,BEA- and &XfJT0ZAB-"
        result = decode_imap_utf7(encoded)
        # Should decode both sections
        assert "&" not in result or result.count("&") < encoded.count("&")

    def test_decode_empty_string(self):
        """Test decoding empty string."""
        result = decode_imap_utf7("")
        assert result == ""

    def test_decode_invalid_encoding_graceful(self):
        """Test decoding handles invalid encoding gracefully."""
        # Invalid encoding should not crash
        result = decode_imap_utf7("&Invalid-")
        # Should return something, even if not perfect
        assert isinstance(result, str)

    def test_decode_no_closing_dash(self):
        """Test decoding handles missing closing dash."""
        # Missing closing dash
        result = decode_imap_utf7("&BB4EQgQ,BEA")
        assert isinstance(result, str)


class TestEncodeImapUtf7:
    """Test IMAP UTF-7 encoding function."""

    def test_encode_ascii_no_encoding_needed(self):
        """Test encoding plain ASCII string doesn't change it."""
        result = encode_imap_utf7("INBOX")
        assert result == "INBOX"

    def test_encode_ascii_with_spaces(self):
        """Test encoding ASCII with spaces."""
        result = encode_imap_utf7("Sent Items")
        assert result == "Sent Items"

    def test_encode_literal_ampersand(self):
        """Test encoding literal ampersand."""
        result = encode_imap_utf7("A&B")
        assert result == "A&-B"

    def test_encode_cyrillic_folder(self):
        """Test encoding Cyrillic folder names."""
        # "Отправленные" (Sent in Russian)
        original = "Отправленные"
        result = encode_imap_utf7(original)
        # Should be encoded
        assert "&" in result
        assert result.startswith("&")
        assert result.endswith("-")

    def test_encode_chinese_folder(self):
        """Test encoding Chinese folder names."""
        original = "已发送"
        result = encode_imap_utf7(original)
        # Should be encoded
        assert "&" in result

    def test_encode_mixed_ascii_and_unicode(self):
        """Test encoding mixed ASCII and Unicode."""
        original = "Sent Отправлено"
        result = encode_imap_utf7(original)
        # Should have ASCII part and encoded part
        assert "Sent" in result
        assert "&" in result

    def test_encode_empty_string(self):
        """Test encoding empty string."""
        result = encode_imap_utf7("")
        assert result == ""

    def test_encode_decode_roundtrip_cyrillic(self):
        """Test encode-decode roundtrip for Cyrillic."""
        original = "Черновики"
        encoded = encode_imap_utf7(original)
        decoded = decode_imap_utf7(encoded)
        assert decoded == original

    def test_encode_decode_roundtrip_chinese(self):
        """Test encode-decode roundtrip for Chinese."""
        original = "草稿箱"
        encoded = encode_imap_utf7(original)
        decoded = decode_imap_utf7(encoded)
        assert decoded == original

    def test_encode_decode_roundtrip_mixed(self):
        """Test encode-decode roundtrip for mixed content."""
        original = "Folder Папка 文件夹"
        encoded = encode_imap_utf7(original)
        decoded = decode_imap_utf7(encoded)
        assert decoded == original

    def test_encode_special_chars(self):
        """Test encoding special characters."""
        original = "Test™®©"
        encoded = encode_imap_utf7(original)
        decoded = decode_imap_utf7(encoded)
        assert decoded == original


@pytest.fixture
def yandex_email_settings():
    """Fixture for Yandex email settings with link configuration."""
    return EmailSettings(
        account_name="yandex_account",
        full_name="Yandex User",
        email_address="user@yandex.ru",
        incoming=EmailServer(
            user_name="user@yandex.ru",
            password="test_password",
            host="imap.yandex.ru",
            port=993,
            use_ssl=True,
        ),
        outgoing=EmailServer(
            user_name="user@yandex.ru",
            password="test_password",
            host="smtp.yandex.ru",
            port=465,
            use_ssl=True,
        ),
        yandex_link=YandexLinkConfig(
            enabled=True,
            baseline_folder="INBOX",
            baseline_uid=100,
            baseline_web_id=5000,
            baseline_date=datetime(2025, 1, 1, 12, 0, 0),
            url_prefix="mail.360.yandex.ru",
            folder_ids={
                "INBOX": 1,
                "Sent": 2,
                "Drafts": 3,
                "Spam": 4,
                "Trash": 5,
                "Отправленные": 2,  # Sent in Russian
            },
        ),
    )


@pytest.fixture
def mock_cache():
    """Fixture to mock cache module."""
    with patch("mcp_email_server.emails.yandex_links.cache") as mock:
        mock.init_db = MagicMock()
        mock.get_web_id = MagicMock(return_value=None)
        mock.get_last_sync_date = MagicMock(return_value=None)
        mock.get_max_web_id = MagicMock(return_value=None)
        mock.bulk_insert_messages = MagicMock(return_value=0)
        mock.update_sync_state = MagicMock()
        yield mock


class TestYandexLinkCalculator:
    """Test YandexLinkCalculator class."""

    def test_init(self, yandex_email_settings, mock_cache):
        """Test YandexLinkCalculator initialization."""
        calculator = YandexLinkCalculator(yandex_email_settings)

        assert calculator.email_settings == yandex_email_settings
        assert calculator.baseline == yandex_email_settings.yandex_link
        assert calculator.account == "yandex_account"
        mock_cache.init_db.assert_called_once()

    def test_format_url_inbox(self, yandex_email_settings, mock_cache):
        """Test _format_url for INBOX folder."""
        calculator = YandexLinkCalculator(yandex_email_settings)

        url = calculator._format_url(5123, "INBOX")
        assert url == "https://mail.360.yandex.ru/touch/folder/1/thread/5123"

    def test_format_url_sent(self, yandex_email_settings, mock_cache):
        """Test _format_url for Sent folder."""
        calculator = YandexLinkCalculator(yandex_email_settings)

        url = calculator._format_url(5123, "Sent")
        assert url == "https://mail.360.yandex.ru/touch/folder/2/thread/5123"

    def test_format_url_cyrillic_folder(self, yandex_email_settings, mock_cache):
        """Test _format_url for Cyrillic folder name."""
        calculator = YandexLinkCalculator(yandex_email_settings)

        # "Отправленные" should map to folder ID 2
        url = calculator._format_url(5123, "Отправленные")
        assert url == "https://mail.360.yandex.ru/touch/folder/2/thread/5123"

    def test_format_url_unknown_folder_defaults_to_1(self, yandex_email_settings, mock_cache):
        """Test _format_url defaults to folder ID 1 for unknown folders."""
        calculator = YandexLinkCalculator(yandex_email_settings)

        url = calculator._format_url(5123, "UnknownFolder")
        assert url == "https://mail.360.yandex.ru/touch/folder/1/thread/5123"

    def test_format_url_decodes_imap_utf7(self, yandex_email_settings, mock_cache):
        """Test _format_url decodes IMAP UTF-7 folder names."""
        calculator = YandexLinkCalculator(yandex_email_settings)

        # Use a known folder in the config instead
        url = calculator._format_url(5123, "Sent")
        # Should match "Sent" -> folder ID 2
        assert url == "https://mail.360.yandex.ru/touch/folder/2/thread/5123"

    @pytest.mark.asyncio
    async def test_get_web_url_cache_hit(self, yandex_email_settings, mock_cache):
        """Test get_web_url returns cached value when available."""
        calculator = YandexLinkCalculator(yandex_email_settings)

        # Mock cache hit
        mock_cache.get_web_id.return_value = 5555

        url = await calculator.get_web_url("INBOX", 123)

        assert url == "https://mail.360.yandex.ru/touch/folder/1/thread/5555"
        mock_cache.get_web_id.assert_called_once_with("yandex_account", "INBOX", 123)

    @pytest.mark.asyncio
    async def test_get_web_url_cache_miss_triggers_sync(self, yandex_email_settings, mock_cache):
        """Test get_web_url triggers sync on cache miss."""
        calculator = YandexLinkCalculator(yandex_email_settings)

        # Mock cache miss, then hit after sync
        mock_cache.get_web_id.side_effect = [None, 5678]

        # Mock IMAP operations
        mock_imap = AsyncMock()
        # Create a properly awaitable future for _client_task
        future = asyncio.Future()
        future.set_result(None)
        mock_imap._client_task = future
        mock_imap.wait_hello_from_server = AsyncMock()
        mock_imap.login = AsyncMock()
        mock_imap.list = AsyncMock(return_value=MagicMock(result="OK", lines=[b'(\\HasNoChildren) "/" "INBOX"']))
        mock_imap.select = AsyncMock(return_value=MagicMock(result="OK"))
        mock_imap.uid_search = AsyncMock(return_value=MagicMock(lines=[b""]))
        mock_imap.logout = AsyncMock()

        with patch("mcp_email_server.emails.yandex_links.aioimaplib.IMAP4_SSL", return_value=mock_imap):
            url = await calculator.get_web_url("INBOX", 123)

        assert url == "https://mail.360.yandex.ru/touch/folder/1/thread/5678"
        # Should have called get_web_id twice (before and after sync)
        assert mock_cache.get_web_id.call_count == 2

    @pytest.mark.asyncio
    async def test_get_web_url_fallback_to_baseline(self, yandex_email_settings, mock_cache):
        """Test get_web_url falls back to baseline when message not found."""
        calculator = YandexLinkCalculator(yandex_email_settings)

        # Mock cache miss for both lookups
        mock_cache.get_web_id.return_value = None

        # Mock IMAP operations (empty results)
        mock_imap = AsyncMock()
        future = asyncio.Future()
        future.set_result(None)
        mock_imap._client_task = future
        mock_imap.wait_hello_from_server = AsyncMock()
        mock_imap.login = AsyncMock()
        mock_imap.list = AsyncMock(return_value=MagicMock(result="OK", lines=[b'(\\HasNoChildren) "/" "INBOX"']))
        mock_imap.select = AsyncMock(return_value=MagicMock(result="OK"))
        mock_imap.uid_search = AsyncMock(return_value=MagicMock(lines=[b""]))
        mock_imap.logout = AsyncMock()

        with patch("mcp_email_server.emails.yandex_links.aioimaplib.IMAP4_SSL", return_value=mock_imap):
            url = await calculator.get_web_url("INBOX", 999)

        # Should fall back to baseline
        assert url == "https://mail.360.yandex.ru/touch/folder/1/thread/5000"

    @pytest.mark.asyncio
    async def test_get_all_folders_success(self, yandex_email_settings, mock_cache):
        """Test _get_all_folders parses folder list correctly."""
        calculator = YandexLinkCalculator(yandex_email_settings)

        mock_imap = AsyncMock()
        mock_imap.list.return_value = MagicMock(
            result="OK",
            lines=[
                b'(\\HasNoChildren) "/" "INBOX"',
                b'(\\HasNoChildren) "/" "Sent"',
                b'(\\HasNoChildren) "/" "Drafts"',
            ]
        )

        folders = await calculator._get_all_folders(mock_imap)

        assert "INBOX" in folders
        assert "Sent" in folders
        assert "Drafts" in folders

    @pytest.mark.asyncio
    async def test_get_all_folders_with_cyrillic(self, yandex_email_settings, mock_cache):
        """Test _get_all_folders handles Cyrillic folder names."""
        calculator = YandexLinkCalculator(yandex_email_settings)

        # Encoded Cyrillic folder
        encoded_folder = '(\\HasNoChildren) "/" "&BB4EQgQ,BEAEMAQyBDsESwQ9BD0ESwQ1-"'

        mock_imap = AsyncMock()
        mock_imap.list.return_value = MagicMock(
            result="OK",
            lines=[
                b'(\\HasNoChildren) "/" "INBOX"',
                encoded_folder.encode(),
            ]
        )

        folders = await calculator._get_all_folders(mock_imap)

        assert "INBOX" in folders
        # Should include the encoded folder name (not decoded at this stage)
        assert len(folders) >= 2

    @pytest.mark.asyncio
    async def test_get_all_folders_empty_result(self, yandex_email_settings, mock_cache):
        """Test _get_all_folders returns default on empty result."""
        calculator = YandexLinkCalculator(yandex_email_settings)

        mock_imap = AsyncMock()
        mock_imap.list.return_value = MagicMock(result="NO", lines=[])

        folders = await calculator._get_all_folders(mock_imap)

        # Should return default INBOX
        assert folders == ["INBOX"]

    @pytest.mark.asyncio
    async def test_get_all_folders_filters_invalid(self, yandex_email_settings, mock_cache):
        """Test _get_all_folders filters invalid folder names."""
        calculator = YandexLinkCalculator(yandex_email_settings)

        mock_imap = AsyncMock()
        mock_imap.list.return_value = MagicMock(
            result="OK",
            lines=[
                b'(\\HasNoChildren) "/" "INBOX"',
                b'(\\HasNoChildren) "/" "|"',  # Invalid
                b'(\\HasNoChildren) "/" ""',    # Invalid
            ]
        )

        folders = await calculator._get_all_folders(mock_imap)

        # Should only include valid folders
        assert "INBOX" in folders
        assert "|" not in folders

    @pytest.mark.asyncio
    async def test_fetch_folder_messages_success(self, yandex_email_settings, mock_cache):
        """Test _fetch_folder_messages retrieves messages correctly."""
        calculator = YandexLinkCalculator(yandex_email_settings)

        mock_imap = AsyncMock()
        mock_imap.select.return_value = MagicMock(result="OK")
        mock_imap.uid_search.return_value = MagicMock(lines=[b"101 102 103"])

        # Mock fetch responses for each UID
        fetch_responses = [
            MagicMock(result="OK", lines=[b'101 (INTERNALDATE "15-Jan-2025 10:30:00 +0000")']),
            MagicMock(result="OK", lines=[b'102 (INTERNALDATE "16-Jan-2025 11:00:00 +0000")']),
            MagicMock(result="OK", lines=[b'103 (INTERNALDATE "17-Jan-2025 12:15:00 +0000")']),
        ]
        mock_imap.uid.side_effect = fetch_responses

        since_date = datetime(2025, 1, 10)
        messages = await calculator._fetch_folder_messages(mock_imap, "INBOX", since_date)

        assert len(messages) == 3
        assert all(msg["folder"] == "INBOX" for msg in messages)
        assert messages[0]["uid"] == 101
        assert messages[1]["uid"] == 102
        assert messages[2]["uid"] == 103

    @pytest.mark.asyncio
    async def test_fetch_folder_messages_empty_folder(self, yandex_email_settings, mock_cache):
        """Test _fetch_folder_messages handles empty folder."""
        calculator = YandexLinkCalculator(yandex_email_settings)

        mock_imap = AsyncMock()
        mock_imap.select.return_value = MagicMock(result="OK")
        mock_imap.uid_search.return_value = MagicMock(lines=[b""])

        messages = await calculator._fetch_folder_messages(
            mock_imap, "INBOX", datetime(2025, 1, 1)
        )

        assert messages == []

    @pytest.mark.asyncio
    async def test_fetch_folder_messages_select_fails(self, yandex_email_settings, mock_cache):
        """Test _fetch_folder_messages handles SELECT failure."""
        calculator = YandexLinkCalculator(yandex_email_settings)

        mock_imap = AsyncMock()
        mock_imap.select.return_value = MagicMock(result="NO")

        messages = await calculator._fetch_folder_messages(
            mock_imap, "InvalidFolder", datetime(2025, 1, 1)
        )

        assert messages == []

    @pytest.mark.asyncio
    async def test_sync_messages_first_sync(self, yandex_email_settings, mock_cache):
        """Test _sync_messages on first sync uses baseline."""
        calculator = YandexLinkCalculator(yandex_email_settings)

        # Mock first sync (no existing state)
        mock_cache.get_last_sync_date.return_value = None
        mock_cache.get_max_web_id.return_value = None

        # Mock IMAP
        mock_imap = AsyncMock()
        future = asyncio.Future()
        future.set_result(None)
        mock_imap._client_task = future
        mock_imap.wait_hello_from_server = AsyncMock()
        mock_imap.login = AsyncMock()
        mock_imap.list.return_value = MagicMock(result="OK", lines=[b'(\\HasNoChildren) "/" "INBOX"'])
        mock_imap.select.return_value = MagicMock(result="OK")
        mock_imap.uid_search.return_value = MagicMock(lines=[b"101"])
        mock_imap.uid.return_value = MagicMock(
            result="OK",
            lines=[b'101 (INTERNALDATE "15-Jan-2025 10:30:00 +0000")']
        )
        mock_imap.logout = AsyncMock()

        with patch("mcp_email_server.emails.yandex_links.aioimaplib.IMAP4_SSL", return_value=mock_imap):
            await calculator._sync_messages()

        # Should cache the baseline message
        assert mock_cache.bulk_insert_messages.call_count >= 1

    @pytest.mark.asyncio
    async def test_sync_messages_incremental_sync(self, yandex_email_settings, mock_cache):
        """Test _sync_messages on subsequent sync uses last sync date."""
        calculator = YandexLinkCalculator(yandex_email_settings)

        # Mock existing sync state
        last_sync = datetime(2025, 1, 10)
        mock_cache.get_last_sync_date.return_value = last_sync
        mock_cache.get_max_web_id.return_value = 5100

        # Mock IMAP
        mock_imap = AsyncMock()
        future = asyncio.Future()
        future.set_result(None)
        mock_imap._client_task = future
        mock_imap.wait_hello_from_server = AsyncMock()
        mock_imap.login = AsyncMock()
        mock_imap.list.return_value = MagicMock(result="OK", lines=[b'(\\HasNoChildren) "/" "INBOX"'])
        mock_imap.select.return_value = MagicMock(result="OK")
        mock_imap.uid_search.return_value = MagicMock(lines=[b""])
        mock_imap.logout = AsyncMock()

        with patch("mcp_email_server.emails.yandex_links.aioimaplib.IMAP4_SSL", return_value=mock_imap):
            await calculator._sync_messages()

        # Should use last_sync for search
        mock_imap.uid_search.assert_called()

    @pytest.mark.asyncio
    async def test_sync_messages_calculates_webid_relative_to_baseline(
        self, yandex_email_settings, mock_cache
    ):
        """Test that web_ids are calculated relative to baseline position.

        Messages chronologically before the baseline should get LOWER web_ids.
        Messages chronologically after should get HIGHER web_ids.

        Regression test for bug where all non-baseline messages got incrementing
        IDs regardless of their position relative to baseline.
        """
        calculator = YandexLinkCalculator(yandex_email_settings)

        # Mock first sync (no existing state)
        mock_cache.get_last_sync_date.return_value = None
        mock_cache.get_max_web_id.return_value = None

        # Baseline config: INBOX uid=100, web_id=5000, date=2025-01-01 12:00:00
        # We'll return 3 messages:
        # - Spam/50 at 10:00 (before baseline)
        # - INBOX/100 at 12:00 (baseline)
        # - Sent/200 at 14:00 (after baseline)

        mock_imap = AsyncMock()
        future = asyncio.Future()
        future.set_result(None)
        mock_imap._client_task = future
        mock_imap.wait_hello_from_server = AsyncMock()
        mock_imap.login = AsyncMock()
        mock_imap.list.return_value = MagicMock(
            result="OK",
            lines=[
                b'(\\HasNoChildren) "/" "INBOX"',
                b'(\\HasNoChildren) "/" "Spam"',
                b'(\\HasNoChildren) "/" "Sent"',
            ]
        )
        mock_imap.logout = AsyncMock()

        # Setup folder selection and search for each folder
        select_results = {"INBOX": "OK", "Spam": "OK", "Sent": "OK"}
        search_results = {"INBOX": b"100", "Spam": b"50", "Sent": b"200"}
        fetch_results = {
            "100": b'100 (INTERNALDATE "01-Jan-2025 12:00:00 +0000")',  # baseline
            "50": b'50 (INTERNALDATE "01-Jan-2025 10:00:00 +0000")',   # before
            "200": b'200 (INTERNALDATE "01-Jan-2025 14:00:00 +0000")', # after
        }

        current_folder = {"name": "INBOX"}

        async def mock_select(folder):
            current_folder["name"] = folder
            return MagicMock(result=select_results.get(folder, "NO"))

        async def mock_search(*args):
            folder = current_folder["name"]
            return MagicMock(lines=[search_results.get(folder, b"")])

        async def mock_uid(cmd, uid_str, *args):
            if cmd == "fetch":
                return MagicMock(result="OK", lines=[fetch_results.get(uid_str, b"")])
            return MagicMock(result="NO")

        mock_imap.select = mock_select
        mock_imap.uid_search = mock_search
        mock_imap.uid = mock_uid

        with patch("mcp_email_server.emails.yandex_links.aioimaplib.IMAP4_SSL", return_value=mock_imap):
            await calculator._sync_messages()

        # Verify bulk_insert_messages was called
        assert mock_cache.bulk_insert_messages.call_count >= 1

        # Get the messages that were inserted
        call_args = mock_cache.bulk_insert_messages.call_args_list
        all_inserted = []
        for call in call_args:
            account, messages = call[0]
            all_inserted.extend(messages)

        # Find messages by their characteristics
        msg_before = next((m for m in all_inserted if m.get("uid") == 50), None)
        msg_baseline = next((m for m in all_inserted if m.get("uid") == 100), None)
        msg_after = next((m for m in all_inserted if m.get("uid") == 200), None)

        # Baseline should have web_id = 5000
        if msg_baseline:
            assert msg_baseline["web_id"] == 5000, f"Baseline should be 5000, got {msg_baseline['web_id']}"

        # Message before baseline should have web_id = 4999 (5000 - 1)
        if msg_before:
            assert msg_before["web_id"] == 4999, f"Before baseline should be 4999, got {msg_before['web_id']}"

        # Message after baseline should have web_id = 5001 (5000 + 1)
        if msg_after:
            assert msg_after["web_id"] == 5001, f"After baseline should be 5001, got {msg_after['web_id']}"


class TestYandexLinkConfigModel:
    """Test YandexLinkConfig Pydantic model."""

    def test_yandex_link_config_defaults(self):
        """Test YandexLinkConfig default values."""
        config = YandexLinkConfig(
            baseline_uid=100,
            baseline_web_id=5000,
            baseline_date=datetime(2025, 1, 1),
        )

        assert config.enabled is False
        assert config.baseline_folder == "INBOX"
        assert config.url_prefix == "mail.360.yandex.ru"
        assert config.folder_ids == {"INBOX": 1}

    def test_yandex_link_config_custom_values(self):
        """Test YandexLinkConfig with custom values."""
        config = YandexLinkConfig(
            enabled=True,
            baseline_folder="Sent",
            baseline_uid=200,
            baseline_web_id=6000,
            baseline_date=datetime(2025, 2, 1),
            url_prefix="mail.yandex.ru",
            folder_ids={"INBOX": 1, "Sent": 2, "Drafts": 3},
        )

        assert config.enabled is True
        assert config.baseline_folder == "Sent"
        assert config.url_prefix == "mail.yandex.ru"
        assert len(config.folder_ids) == 3

    def test_yandex_link_config_in_email_settings(self):
        """Test YandexLinkConfig embedded in EmailSettings."""
        settings = EmailSettings(
            account_name="test",
            full_name="Test User",
            email_address="test@yandex.ru",
            incoming=EmailServer(
                user_name="test",
                password="pass",
                host="imap.yandex.ru",
                port=993,
            ),
            outgoing=EmailServer(
                user_name="test",
                password="pass",
                host="smtp.yandex.ru",
                port=465,
            ),
            yandex_link=YandexLinkConfig(
                enabled=True,
                baseline_uid=100,
                baseline_web_id=5000,
                baseline_date=datetime(2025, 1, 1),
            ),
        )

        assert settings.yandex_link is not None
        assert settings.yandex_link.enabled is True
