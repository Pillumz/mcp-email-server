import asyncio
import email
from datetime import datetime, timezone
from email.mime.text import MIMEText
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mcp_email_server.config import EmailServer
from mcp_email_server.emails.classic import EmailClient


@pytest.fixture
def email_server():
    return EmailServer(
        user_name="test_user",
        password="test_password",
        host="imap.example.com",
        port=993,
        use_ssl=True,
    )


@pytest.fixture
def email_client(email_server):
    return EmailClient(email_server, sender="Test User <test@example.com>")


class TestEmailClient:
    def test_init(self, email_server):
        """Test initialization of EmailClient."""
        client = EmailClient(email_server)
        assert client.email_server == email_server
        assert client.sender == email_server.user_name
        assert client.smtp_use_tls is True
        assert client.smtp_start_tls is False

        # Test with custom sender
        custom_sender = "Custom <custom@example.com>"
        client = EmailClient(email_server, sender=custom_sender)
        assert client.sender == custom_sender

    def test_parse_email_data_plain(self):
        """Test parsing plain text email."""
        # Create a simple plain text email
        msg = MIMEText("This is a test email body")
        msg["Subject"] = "Test Subject"
        msg["From"] = "sender@example.com"
        msg["To"] = "recipient@example.com"
        msg["Date"] = email.utils.formatdate()

        raw_email = msg.as_bytes()

        client = EmailClient(MagicMock())
        result = client._parse_email_data(raw_email)

        assert result["subject"] == "Test Subject"
        assert result["from"] == "sender@example.com"
        assert result["body"] == "This is a test email body"
        assert isinstance(result["date"], datetime)
        assert result["attachments"] == []

    def test_parse_email_data_with_attachments(self):
        """Test parsing email with attachments."""
        # This would require creating a multipart email with attachments
        # For simplicity, we'll mock the email parsing
        with patch("email.parser.BytesParser.parsebytes") as mock_parse:
            mock_email = MagicMock()
            mock_email.get.side_effect = lambda x, default=None: {
                "Subject": "Test Subject",
                "From": "sender@example.com",
                "Date": email.utils.formatdate(),
            }.get(x, default)
            mock_email.is_multipart.return_value = True

            # Mock parts
            text_part = MagicMock()
            text_part.get_content_type.return_value = "text/plain"
            text_part.get.return_value = ""  # Not an attachment
            text_part.get_payload.return_value = b"This is the email body"
            text_part.get_content_charset.return_value = "utf-8"

            attachment_part = MagicMock()
            attachment_part.get_content_type.return_value = "application/pdf"
            attachment_part.get.return_value = "attachment; filename=test.pdf"
            attachment_part.get_filename.return_value = "test.pdf"

            mock_email.walk.return_value = [text_part, attachment_part]
            mock_parse.return_value = mock_email

            client = EmailClient(MagicMock())
            result = client._parse_email_data(b"dummy email content")

            assert result["subject"] == "Test Subject"
            assert result["from"] == "sender@example.com"
            assert result["body"] == "This is the email body"
            assert isinstance(result["date"], datetime)
            assert result["attachments"] == ["test.pdf"]

    def test_build_search_criteria_no_criteria(self):
        """Test building search criteria with no parameters returns ALL."""
        criteria = EmailClient._build_search_criteria()
        assert criteria == ["ALL"]

    def test_build_search_criteria_none_values(self):
        """Test that None values are ignored and don't appear in criteria."""
        criteria = EmailClient._build_search_criteria(
            before=None,
            since=None,
            subject=None,
            body=None,
            text=None,
            from_address=None,
            to_address=None,
        )
        assert criteria == ["ALL"]

    def test_build_search_criteria_empty_strings(self):
        """Test that empty strings are ignored and don't appear in criteria."""
        criteria = EmailClient._build_search_criteria(
            subject="",
            body="",
            text="",
            from_address="",
            to_address="",
        )
        assert criteria == ["ALL"]

    def test_build_search_criteria_date_format_proper_case(self):
        """
        Test that date formatting uses proper case (e.g., 'Nov' not 'NOV').
        This is the regression test for the bug where .upper() broke Yandex IMAP.
        """
        # Test November (the month mentioned in the bug report)
        nov_date = datetime(2025, 11, 24, tzinfo=timezone.utc)
        criteria = EmailClient._build_search_criteria(before=nov_date)
        assert criteria == ["BEFORE", "24-Nov-2025"]
        # Ensure it's NOT uppercase
        assert "24-NOV-2025" not in criteria
        assert "Nov" in criteria[1]

        # Test the same for SINCE
        criteria = EmailClient._build_search_criteria(since=nov_date)
        assert criteria == ["SINCE", "24-Nov-2025"]
        assert "24-NOV-2025" not in criteria
        assert "Nov" in criteria[1]

    def test_build_search_criteria_all_months_proper_case(self):
        """Test that all month abbreviations use proper case, not uppercase."""
        month_tests = [
            (datetime(2025, 1, 15, tzinfo=timezone.utc), "Jan"),
            (datetime(2025, 2, 15, tzinfo=timezone.utc), "Feb"),
            (datetime(2025, 3, 15, tzinfo=timezone.utc), "Mar"),
            (datetime(2025, 4, 15, tzinfo=timezone.utc), "Apr"),
            (datetime(2025, 5, 15, tzinfo=timezone.utc), "May"),
            (datetime(2025, 6, 15, tzinfo=timezone.utc), "Jun"),
            (datetime(2025, 7, 15, tzinfo=timezone.utc), "Jul"),
            (datetime(2025, 8, 15, tzinfo=timezone.utc), "Aug"),
            (datetime(2025, 9, 15, tzinfo=timezone.utc), "Sep"),
            (datetime(2025, 10, 15, tzinfo=timezone.utc), "Oct"),
            (datetime(2025, 11, 15, tzinfo=timezone.utc), "Nov"),
            (datetime(2025, 12, 15, tzinfo=timezone.utc), "Dec"),
        ]

        for test_date, expected_month in month_tests:
            criteria = EmailClient._build_search_criteria(before=test_date)
            date_string = criteria[1]
            assert expected_month in date_string, f"Expected '{expected_month}' in date string '{date_string}'"
            # Ensure it's not uppercase
            assert expected_month.upper() != expected_month or date_string != date_string.upper()

    def test_build_search_criteria_before_date(self):
        """Test building search criteria with before date."""
        before_date = datetime(2023, 1, 1, tzinfo=timezone.utc)
        criteria = EmailClient._build_search_criteria(before=before_date)
        assert criteria == ["BEFORE", "01-Jan-2023"]

    def test_build_search_criteria_since_date(self):
        """Test building search criteria with since date."""
        since_date = datetime(2023, 1, 1, tzinfo=timezone.utc)
        criteria = EmailClient._build_search_criteria(since=since_date)
        assert criteria == ["SINCE", "01-Jan-2023"]

    def test_build_search_criteria_both_dates(self):
        """Test building search criteria with both before and since dates."""
        before_date = datetime(2023, 12, 31, tzinfo=timezone.utc)
        since_date = datetime(2023, 1, 1, tzinfo=timezone.utc)
        criteria = EmailClient._build_search_criteria(before=before_date, since=since_date)
        assert criteria == ["BEFORE", "31-Dec-2023", "SINCE", "01-Jan-2023"]

    def test_build_search_criteria_subject(self):
        """Test building search criteria with subject."""
        criteria = EmailClient._build_search_criteria(subject="Test Subject")
        assert criteria == ["SUBJECT", "Test Subject"]

    def test_build_search_criteria_subject_with_special_chars(self):
        """Test building search criteria with subject containing special characters."""
        criteria = EmailClient._build_search_criteria(subject="Re: Important [URGENT]")
        assert criteria == ["SUBJECT", "Re: Important [URGENT]"]

    def test_build_search_criteria_body(self):
        """Test building search criteria with body."""
        criteria = EmailClient._build_search_criteria(body="Test Body")
        assert criteria == ["BODY", "Test Body"]

    def test_build_search_criteria_text(self):
        """Test building search criteria with text."""
        criteria = EmailClient._build_search_criteria(text="Test Text")
        assert criteria == ["TEXT", "Test Text"]

    def test_build_search_criteria_from_address(self):
        """Test building search criteria with from_address."""
        criteria = EmailClient._build_search_criteria(from_address="test@example.com")
        assert criteria == ["FROM", "test@example.com"]

    def test_build_search_criteria_to_address(self):
        """Test building search criteria with to_address."""
        criteria = EmailClient._build_search_criteria(to_address="recipient@example.com")
        assert criteria == ["TO", "recipient@example.com"]

    def test_build_search_criteria_multiple_criteria(self):
        """Test building search criteria with multiple parameters."""
        criteria = EmailClient._build_search_criteria(
            subject="Test",
            from_address="sender@example.com",
            since=datetime(2023, 1, 1, tzinfo=timezone.utc),
        )
        assert criteria == ["SINCE", "01-Jan-2023", "SUBJECT", "Test", "FROM", "sender@example.com"]

    def test_build_search_criteria_all_parameters(self):
        """Test building search criteria with all parameters provided."""
        before_date = datetime(2023, 12, 31, tzinfo=timezone.utc)
        since_date = datetime(2023, 1, 1, tzinfo=timezone.utc)

        criteria = EmailClient._build_search_criteria(
            before=before_date,
            since=since_date,
            subject="Important",
            body="meeting",
            text="agenda",
            from_address="boss@example.com",
            to_address="team@example.com",
        )

        # Verify all parameters are included
        assert "BEFORE" in criteria
        assert "31-Dec-2023" in criteria
        assert "SINCE" in criteria
        assert "01-Jan-2023" in criteria
        assert "SUBJECT" in criteria
        assert "Important" in criteria
        assert "BODY" in criteria
        assert "meeting" in criteria
        assert "TEXT" in criteria
        assert "agenda" in criteria
        assert "FROM" in criteria
        assert "boss@example.com" in criteria
        assert "TO" in criteria
        assert "team@example.com" in criteria

    def test_build_search_criteria_mixed_none_and_values(self):
        """Test building search criteria with mix of None and actual values."""
        criteria = EmailClient._build_search_criteria(
            before=None,
            since=datetime(2023, 6, 15, tzinfo=timezone.utc),
            subject="Test",
            body=None,
            from_address="sender@example.com",
            to_address=None,
        )

        # Only non-None values should appear
        assert "SINCE" in criteria
        assert "15-Jun-2023" in criteria
        assert "SUBJECT" in criteria
        assert "Test" in criteria
        assert "FROM" in criteria
        assert "sender@example.com" in criteria

        # None values should not appear
        assert "BEFORE" not in criteria
        assert "BODY" not in criteria
        assert "TO" not in criteria

    def test_build_search_criteria_date_edge_cases(self):
        """Test date formatting with edge cases like leap years and year boundaries."""
        # Leap year date
        leap_date = datetime(2024, 2, 29, tzinfo=timezone.utc)
        criteria = EmailClient._build_search_criteria(before=leap_date)
        assert criteria == ["BEFORE", "29-Feb-2024"]

        # First day of year
        first_day = datetime(2023, 1, 1, tzinfo=timezone.utc)
        criteria = EmailClient._build_search_criteria(since=first_day)
        assert criteria == ["SINCE", "01-Jan-2023"]

        # Last day of year
        last_day = datetime(2023, 12, 31, tzinfo=timezone.utc)
        criteria = EmailClient._build_search_criteria(before=last_day)
        assert criteria == ["BEFORE", "31-Dec-2023"]

    def test_build_search_criteria_ordering(self):
        """Test that search criteria maintains expected ordering."""
        before_date = datetime(2023, 12, 31, tzinfo=timezone.utc)
        since_date = datetime(2023, 1, 1, tzinfo=timezone.utc)

        criteria = EmailClient._build_search_criteria(
            before=before_date,
            since=since_date,
            subject="Test",
            from_address="test@example.com",
        )

        # Based on the implementation, order should be:
        # BEFORE, before_value, SINCE, since_value, SUBJECT, subject_value, FROM, from_value
        expected = [
            "BEFORE",
            "31-Dec-2023",
            "SINCE",
            "01-Jan-2023",
            "SUBJECT",
            "Test",
            "FROM",
            "test@example.com",
        ]
        assert criteria == expected

    @pytest.mark.asyncio
    async def test_get_emails_stream(self, email_client):
        """Test getting emails stream."""
        # Mock IMAP client
        mock_imap = AsyncMock()
        mock_imap._client_task = asyncio.Future()
        mock_imap._client_task.set_result(None)
        mock_imap.wait_hello_from_server = AsyncMock()
        mock_imap.login = AsyncMock()
        mock_imap.select = AsyncMock()
        mock_imap.search = AsyncMock(return_value=(None, [b"1 2 3"]))
        mock_imap.uid_search = AsyncMock(return_value=(None, [b"1 2 3"]))
        mock_imap.fetch = AsyncMock(return_value=(None, [b"HEADER", bytearray(b"EMAIL CONTENT")]))
        # Create a simple email with headers for testing
        test_email = b"""From: sender@example.com\r
To: recipient@example.com\r
Subject: Test Subject\r
Date: Mon, 1 Jan 2024 00:00:00 +0000\r
\r
This is the email body."""
        mock_imap.uid = AsyncMock(
            return_value=(None, [b"1 FETCH (UID 1 RFC822 {%d}" % len(test_email), bytearray(test_email)])
        )
        mock_imap.logout = AsyncMock()

        # Mock IMAP class
        with patch.object(email_client, "imap_class", return_value=mock_imap):
            # Mock _parse_email_data
            with patch.object(email_client, "_parse_email_data") as mock_parse:
                mock_parse.return_value = {
                    "subject": "Test Subject",
                    "from": "sender@example.com",
                    "body": "Test Body",
                    "date": datetime.now(timezone.utc),
                    "attachments": [],
                }

                emails = []
                async for email_data in email_client.get_emails_metadata_stream(page=1, page_size=10):
                    emails.append(email_data)

                # We should get 3 emails (from the mocked search result "1 2 3")
                assert len(emails) == 3
                assert emails[0]["subject"] == "Test Subject"
                assert emails[0]["from"] == "sender@example.com"

                # Verify IMAP methods were called correctly
                mock_imap.login.assert_called_once_with(
                    email_client.email_server.user_name, email_client.email_server.password
                )
                mock_imap.select.assert_called_once_with("INBOX")
                mock_imap.uid_search.assert_called_once_with("ALL")
                assert mock_imap.uid.call_count == 3
                mock_imap.logout.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_email_count(self, email_client):
        """Test getting email count."""
        # Mock IMAP client
        mock_imap = AsyncMock()
        mock_imap._client_task = asyncio.Future()
        mock_imap._client_task.set_result(None)
        mock_imap.wait_hello_from_server = AsyncMock()
        mock_imap.login = AsyncMock()
        mock_imap.select = AsyncMock()
        mock_imap.search = AsyncMock(return_value=(None, [b"1 2 3 4 5"]))
        mock_imap.uid_search = AsyncMock(return_value=(None, [b"1 2 3 4 5"]))
        mock_imap.logout = AsyncMock()

        # Mock IMAP class
        with patch.object(email_client, "imap_class", return_value=mock_imap):
            count = await email_client.get_email_count()

            assert count == 5

            # Verify IMAP methods were called correctly
            mock_imap.login.assert_called_once_with(
                email_client.email_server.user_name, email_client.email_server.password
            )
            mock_imap.select.assert_called_once_with("INBOX")
            mock_imap.uid_search.assert_called_once_with("ALL")
            mock_imap.logout.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_email(self, email_client):
        """Test sending email."""
        # Mock SMTP client
        mock_smtp = AsyncMock()
        mock_smtp.__aenter__.return_value = mock_smtp
        mock_smtp.__aexit__.return_value = None
        mock_smtp.login = AsyncMock()
        mock_smtp.send_message = AsyncMock()

        with patch("aiosmtplib.SMTP", return_value=mock_smtp):
            await email_client.send_email(
                recipients=["recipient@example.com"],
                subject="Test Subject",
                body="Test Body",
                cc=["cc@example.com"],
                bcc=["bcc@example.com"],
            )

            # Verify SMTP methods were called correctly
            mock_smtp.login.assert_called_once_with(
                email_client.email_server.user_name, email_client.email_server.password
            )
            mock_smtp.send_message.assert_called_once()

            # Check that the message was constructed correctly
            call_args = mock_smtp.send_message.call_args
            msg = call_args[0][0]
            recipients = call_args[1]["recipients"]

            assert msg["Subject"] == "Test Subject"
            assert msg["From"] == email_client.sender
            assert msg["To"] == "recipient@example.com"
            assert msg["Cc"] == "cc@example.com"
            assert "Bcc" not in msg  # BCC should not be in headers

            # Check that all recipients are included in the SMTP call
            assert "recipient@example.com" in recipients
            assert "cc@example.com" in recipients
            assert "bcc@example.com" in recipients
