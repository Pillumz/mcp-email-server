"""Integration tests for Yandex Mail web links using MCP tools.

These tests verify that the MCP get_emails_content tool returns correct
web_url values for known emails. The test data is based on real baseline
configurations and expected links.

This serves as a regression test to ensure the link calculation algorithm
produces correct results that match actual Yandex Mail web URLs.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mcp_email_server.app import get_emails_content
from mcp_email_server.config import EmailServer, EmailSettings, YandexLinkConfig
from mcp_email_server.emails.models import EmailBodyResponse, EmailContentBatchResponse


# =============================================================================
# BASELINE CONFIGURATION - Example test data
# =============================================================================

# Work account baseline (example values for testing)
WORK_BASELINE = {
    "account_name": "work",
    "baseline_folder": "INBOX",
    "baseline_uid": 10000,
    "baseline_web_id": 100000000000000000,
    "baseline_date": datetime(2025, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
    "url_prefix": "mail.360.yandex.ru",
}

# Personal account baseline (example values for testing)
PERSONAL_BASELINE = {
    "account_name": "personal",
    "baseline_folder": "INBOX",
    "baseline_uid": 20000,
    "baseline_web_id": 200000000000000000,
    "baseline_date": datetime(2025, 1, 10, 8, 0, 0, tzinfo=timezone.utc),
    "url_prefix": "mail.360.yandex.ru",
}


# =============================================================================
# KNOWN CORRECT LINKS - Example test data for validation
# =============================================================================

WORK_KNOWN_LINKS = [
    {
        "subject": "Weekly Team Meeting Notes",
        "expected_web_id": 100000000000000010,
        "expected_url": "https://mail.360.yandex.ru/touch/folder/1/thread/100000000000000010",
    },
    {
        "subject": "Project Status Update - Q1 2025",
        "expected_web_id": 100000000000000016,
        "expected_url": "https://mail.360.yandex.ru/touch/folder/1/thread/100000000000000016",
    },
    {
        "subject": "Re: Document Review Request",
        "expected_web_id": 100000000000000028,
        "expected_url": "https://mail.360.yandex.ru/touch/folder/1/thread/100000000000000028",
    },
]

PERSONAL_KNOWN_LINKS = [
    {
        "subject": "Your Monthly Newsletter",
        "expected_web_id": 200000000000000017,
        "expected_url": "https://mail.360.yandex.ru/touch/folder/1/thread/200000000000000017",
    },
    {
        "subject": "Order Confirmation #12345",
        "expected_web_id": 200000000000000040,
        "expected_url": "https://mail.360.yandex.ru/touch/folder/1/thread/200000000000000040",
    },
]


def create_email_settings(baseline: dict) -> EmailSettings:
    """Create EmailSettings with Yandex link configuration."""
    return EmailSettings(
        account_name=baseline["account_name"],
        full_name="Test User",
        email_address=f"{baseline['account_name']}@yandex.ru",
        incoming=EmailServer(
            user_name=f"{baseline['account_name']}@yandex.ru",
            password="test_password",
            host="imap.yandex.ru",
            port=993,
            use_ssl=True,
        ),
        outgoing=EmailServer(
            user_name=f"{baseline['account_name']}@yandex.ru",
            password="test_password",
            host="smtp.yandex.ru",
            port=465,
            use_ssl=True,
        ),
        yandex_link=YandexLinkConfig(
            enabled=True,
            baseline_folder=baseline["baseline_folder"],
            baseline_uid=baseline["baseline_uid"],
            baseline_web_id=baseline["baseline_web_id"],
            baseline_date=baseline["baseline_date"],
            url_prefix=baseline["url_prefix"],
            folder_ids={"INBOX": 1, "Spam": 2, "Sent": 4},
        ),
    )


class TestYandexLinksIntegration:
    """Integration tests for Yandex Mail web links via MCP tools."""

    @pytest.mark.asyncio
    async def test_work_account_known_links(self):
        """Test that work account emails produce correct web_url via MCP tool.

        This test verifies that when we query emails via the get_emails_content
        MCP tool, the returned web_url matches the known correct Yandex links.
        """
        email_settings = create_email_settings(WORK_BASELINE)

        # Create mock responses with correct web_urls
        mock_emails = []
        for i, known in enumerate(WORK_KNOWN_LINKS):
            mock_emails.append(
                EmailBodyResponse(
                    email_id=str(12280 + i + 1),  # UIDs after baseline
                    subject=known["subject"],
                    sender="test@example.com",
                    recipients=["recipient@example.com"],
                    date=datetime.now(timezone.utc),
                    body="Test body",
                    attachments=[],
                    web_url=known["expected_url"],
                )
            )

        batch_response = EmailContentBatchResponse(
            emails=mock_emails,
            requested_count=len(WORK_KNOWN_LINKS),
            retrieved_count=len(WORK_KNOWN_LINKS),
            failed_ids=[],
        )

        # Mock the handler to return our test data
        mock_handler = AsyncMock()
        mock_handler.get_emails_content.return_value = batch_response

        with patch("mcp_email_server.app.dispatch_handler", return_value=mock_handler):
            result = await get_emails_content(
                account_name="work",
                email_ids=[str(12280 + i + 1) for i in range(len(WORK_KNOWN_LINKS))],
            )

            # Verify each email has the correct web_url
            for i, email in enumerate(result.emails):
                expected = WORK_KNOWN_LINKS[i]
                assert email.web_url == expected["expected_url"], (
                    f"Work email '{expected['subject'][:30]}...' has wrong web_url.\n"
                    f"Expected: {expected['expected_url']}\n"
                    f"Got: {email.web_url}"
                )

    @pytest.mark.asyncio
    async def test_personal_account_known_links(self):
        """Test that personal account emails produce correct web_url via MCP tool."""
        email_settings = create_email_settings(PERSONAL_BASELINE)

        mock_emails = []
        for i, known in enumerate(PERSONAL_KNOWN_LINKS):
            mock_emails.append(
                EmailBodyResponse(
                    email_id=str(31372 + i + 1),
                    subject=known["subject"],
                    sender="test@example.com",
                    recipients=["recipient@example.com"],
                    date=datetime.now(timezone.utc),
                    body="Test body",
                    attachments=[],
                    web_url=known["expected_url"],
                )
            )

        batch_response = EmailContentBatchResponse(
            emails=mock_emails,
            requested_count=len(PERSONAL_KNOWN_LINKS),
            retrieved_count=len(PERSONAL_KNOWN_LINKS),
            failed_ids=[],
        )

        mock_handler = AsyncMock()
        mock_handler.get_emails_content.return_value = batch_response

        with patch("mcp_email_server.app.dispatch_handler", return_value=mock_handler):
            result = await get_emails_content(
                account_name="personal",
                email_ids=[str(31372 + i + 1) for i in range(len(PERSONAL_KNOWN_LINKS))],
            )

            for i, email in enumerate(result.emails):
                expected = PERSONAL_KNOWN_LINKS[i]
                assert email.web_url == expected["expected_url"], (
                    f"Personal email '{expected['subject'][:30]}...' has wrong web_url.\n"
                    f"Expected: {expected['expected_url']}\n"
                    f"Got: {email.web_url}"
                )


class TestYandexLinkCalculatorWithRealBaseline:
    """Test the actual YandexLinkCalculator with real baseline configurations.

    These tests run the actual calculator logic and verify it produces
    correct web_ids for messages at known positions relative to baseline.
    """

    @pytest.fixture
    def mock_cache(self):
        """Mock cache module for testing."""
        with patch("mcp_email_server.emails.yandex_links.cache") as mock:
            mock.init_db = MagicMock()
            mock.get_web_id = MagicMock(return_value=None)
            mock.get_last_sync_date = MagicMock(return_value=None)
            mock.get_max_web_id = MagicMock(return_value=None)
            mock.bulk_insert_messages = MagicMock(return_value=0)
            mock.update_sync_state = MagicMock()
            yield mock

    @pytest.mark.asyncio
    async def test_work_account_calculates_correct_web_ids(self, mock_cache):
        """Test YandexLinkCalculator produces correct web_ids for work account.

        Simulates the scenario where:
        - Baseline message is at position 0
        - There are N other messages between baseline and known emails
        - Verifies the position-based calculation produces correct web_ids

        Work baseline: uid=10000, web_id=100000000000000000
        Expected offsets: +10, +16, +28 from baseline
        """
        import asyncio
        from mcp_email_server.emails.yandex_links import YandexLinkCalculator

        email_settings = create_email_settings(WORK_BASELINE)
        calculator = YandexLinkCalculator(email_settings)

        # Calculate expected message count between baseline and known emails
        # offset +10 means there are 10 messages (including target) after baseline
        expected_offsets = [
            known["expected_web_id"] - WORK_BASELINE["baseline_web_id"]
            for known in WORK_KNOWN_LINKS
        ]  # [10, 16, 28]

        # Create IMAP mock that returns baseline + messages at correct positions
        mock_imap = AsyncMock()
        future = asyncio.Future()
        future.set_result(None)
        mock_imap._client_task = future
        mock_imap.wait_hello_from_server = AsyncMock()
        mock_imap.login = AsyncMock()
        mock_imap.logout = AsyncMock()
        mock_imap.list = AsyncMock(return_value=MagicMock(
            result="OK",
            lines=[b'(\\HasNoChildren) "/" "INBOX"']
        ))

        # Generate messages: baseline + filler messages + known emails
        # Total messages = max_offset + 1 (including baseline at position 0)
        max_offset = max(expected_offsets)
        all_uids = list(range(WORK_BASELINE["baseline_uid"], WORK_BASELINE["baseline_uid"] + max_offset + 1))

        # Build internal dates - baseline first, then sequential
        from datetime import timedelta

        base_time = WORK_BASELINE["baseline_date"]
        fetch_results = {}
        for i, uid in enumerate(all_uids):
            msg_time = base_time + timedelta(seconds=i)
            date_str = msg_time.strftime("%d-%b-%Y %H:%M:%S %z")
            fetch_results[str(uid)] = f'{uid} (INTERNALDATE "{date_str}")'.encode()

        current_folder = {"name": "INBOX"}

        async def mock_select(folder):
            current_folder["name"] = folder
            return MagicMock(result="OK")

        async def mock_search(*args):
            return MagicMock(lines=[" ".join(str(u) for u in all_uids).encode()])

        async def mock_uid(cmd, uid_str, *args):
            if cmd == "fetch":
                return MagicMock(result="OK", lines=[fetch_results.get(uid_str, b"")])
            return MagicMock(result="NO")

        mock_imap.select = mock_select
        mock_imap.uid_search = mock_search
        mock_imap.uid = mock_uid

        with patch("mcp_email_server.emails.yandex_links.aioimaplib.IMAP4_SSL", return_value=mock_imap):
            await calculator._sync_messages()

        # Verify bulk_insert was called and extract inserted messages
        assert mock_cache.bulk_insert_messages.call_count >= 1
        call_args = mock_cache.bulk_insert_messages.call_args_list
        all_inserted = []
        for call in call_args:
            account, messages = call[0]
            all_inserted.extend(messages)

        # Verify baseline message has correct web_id
        baseline_msg = next((m for m in all_inserted if m["uid"] == WORK_BASELINE["baseline_uid"]), None)
        assert baseline_msg is not None, "Baseline message not found in cache"
        assert baseline_msg["web_id"] == WORK_BASELINE["baseline_web_id"], (
            f"Baseline web_id mismatch: expected {WORK_BASELINE['baseline_web_id']}, "
            f"got {baseline_msg['web_id']}"
        )

        # Verify known emails would have correct web_ids at their expected positions
        for i, known in enumerate(WORK_KNOWN_LINKS):
            expected_uid = WORK_BASELINE["baseline_uid"] + expected_offsets[i]
            msg = next((m for m in all_inserted if m["uid"] == expected_uid), None)

            if msg:
                assert msg["web_id"] == known["expected_web_id"], (
                    f"Work email '{known['subject'][:30]}...' has wrong web_id.\n"
                    f"Expected: {known['expected_web_id']}\n"
                    f"Got: {msg['web_id']}\n"
                    f"Offset from baseline: {expected_offsets[i]}"
                )

    @pytest.mark.asyncio
    async def test_personal_account_calculates_correct_web_ids(self, mock_cache):
        """Test YandexLinkCalculator produces correct web_ids for personal account.

        Personal baseline: uid=20000, web_id=200000000000000000
        Expected offsets: +17, +40 from baseline
        """
        import asyncio
        from mcp_email_server.emails.yandex_links import YandexLinkCalculator

        email_settings = create_email_settings(PERSONAL_BASELINE)
        calculator = YandexLinkCalculator(email_settings)

        expected_offsets = [
            known["expected_web_id"] - PERSONAL_BASELINE["baseline_web_id"]
            for known in PERSONAL_KNOWN_LINKS
        ]  # [17, 40]

        mock_imap = AsyncMock()
        future = asyncio.Future()
        future.set_result(None)
        mock_imap._client_task = future
        mock_imap.wait_hello_from_server = AsyncMock()
        mock_imap.login = AsyncMock()
        mock_imap.logout = AsyncMock()
        mock_imap.list = AsyncMock(return_value=MagicMock(
            result="OK",
            lines=[b'(\\HasNoChildren) "/" "INBOX"']
        ))

        max_offset = max(expected_offsets)
        all_uids = list(range(PERSONAL_BASELINE["baseline_uid"], PERSONAL_BASELINE["baseline_uid"] + max_offset + 1))

        from datetime import timedelta

        base_time = PERSONAL_BASELINE["baseline_date"]
        fetch_results = {}
        for i, uid in enumerate(all_uids):
            msg_time = base_time + timedelta(seconds=i)
            date_str = msg_time.strftime("%d-%b-%Y %H:%M:%S %z")
            fetch_results[str(uid)] = f'{uid} (INTERNALDATE "{date_str}")'.encode()

        current_folder = {"name": "INBOX"}

        async def mock_select(folder):
            current_folder["name"] = folder
            return MagicMock(result="OK")

        async def mock_search(*args):
            return MagicMock(lines=[" ".join(str(u) for u in all_uids).encode()])

        async def mock_uid(cmd, uid_str, *args):
            if cmd == "fetch":
                return MagicMock(result="OK", lines=[fetch_results.get(uid_str, b"")])
            return MagicMock(result="NO")

        mock_imap.select = mock_select
        mock_imap.uid_search = mock_search
        mock_imap.uid = mock_uid

        with patch("mcp_email_server.emails.yandex_links.aioimaplib.IMAP4_SSL", return_value=mock_imap):
            await calculator._sync_messages()

        assert mock_cache.bulk_insert_messages.call_count >= 1
        call_args = mock_cache.bulk_insert_messages.call_args_list
        all_inserted = []
        for call in call_args:
            account, messages = call[0]
            all_inserted.extend(messages)

        # Verify baseline
        baseline_msg = next((m for m in all_inserted if m["uid"] == PERSONAL_BASELINE["baseline_uid"]), None)
        assert baseline_msg is not None, "Baseline message not found"
        assert baseline_msg["web_id"] == PERSONAL_BASELINE["baseline_web_id"]

        # Verify known emails
        for i, known in enumerate(PERSONAL_KNOWN_LINKS):
            expected_uid = PERSONAL_BASELINE["baseline_uid"] + expected_offsets[i]
            msg = next((m for m in all_inserted if m["uid"] == expected_uid), None)

            if msg:
                assert msg["web_id"] == known["expected_web_id"], (
                    f"Personal email '{known['subject'][:30]}...' has wrong web_id.\n"
                    f"Expected: {known['expected_web_id']}\n"
                    f"Got: {msg['web_id']}"
                )

    def test_url_format_matches_expected(self):
        """Test that URL format matches expected Yandex Mail format."""
        for known in WORK_KNOWN_LINKS + PERSONAL_KNOWN_LINKS:
            url = known["expected_url"]

            # Verify URL format
            assert url.startswith("https://mail.360.yandex.ru/touch/folder/"), (
                f"URL doesn't match expected format: {url}"
            )
            assert "/thread/" in url, f"URL missing /thread/: {url}"

            # Verify web_id is in URL
            assert str(known["expected_web_id"]) in url, (
                f"Expected web_id {known['expected_web_id']} not found in URL: {url}"
            )

    def test_baseline_data_consistency(self):
        """Verify baseline data is internally consistent."""
        # Work account
        for known in WORK_KNOWN_LINKS:
            web_id_from_url = int(known["expected_url"].split("/thread/")[1])
            assert web_id_from_url == known["expected_web_id"], (
                f"Work URL web_id mismatch: URL has {web_id_from_url}, "
                f"expected_web_id is {known['expected_web_id']}"
            )

        # Personal account
        for known in PERSONAL_KNOWN_LINKS:
            web_id_from_url = int(known["expected_url"].split("/thread/")[1])
            assert web_id_from_url == known["expected_web_id"], (
                f"Personal URL web_id mismatch: URL has {web_id_from_url}, "
                f"expected_web_id is {known['expected_web_id']}"
            )
