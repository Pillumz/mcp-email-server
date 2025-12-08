import email.utils
import mimetypes
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from email.header import Header
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.parser import BytesParser
from email.policy import default
from pathlib import Path
from typing import Any

import aioimaplib
import aiosmtplib

from mcp_email_server.config import EmailServer, EmailSettings
from mcp_email_server.emails import EmailHandler
from mcp_email_server.emails.imap_connection import IMAPConnectionManager
from mcp_email_server.emails.models import (
    AttachmentDownloadResponse,
    EmailBodyResponse,
    EmailContentBatchResponse,
    EmailMetadata,
    EmailMetadataPageResponse,
)
from mcp_email_server.emails.yandex_links import YandexLinkCalculator, decode_imap_utf7, encode_imap_utf7
from mcp_email_server.log import logger
from mcp_email_server import cache

# Common Sent folder names across email providers
SENT_FOLDER_NAMES = [
    "Sent",
    "INBOX.Sent",
    "Sent Items",
    "Sent Messages",
    "[Gmail]/Sent Mail",
    "Отправленные",  # Yandex (Russian)
    "&BB4EQgQ,BEAEMAQyBDsENQQ9BD0ESwQ1-",  # Yandex UTF-7 encoded
    "INBOX/Sent",
]


class EmailClient:
    def __init__(self, email_server: EmailServer, sender: str | None = None):
        self.email_server = email_server
        self.sender = sender or email_server.user_name

        self.imap_class = aioimaplib.IMAP4_SSL if self.email_server.use_ssl else aioimaplib.IMAP4

        self.smtp_use_tls = self.email_server.use_ssl
        self.smtp_start_tls = self.email_server.start_ssl

        # Connection manager for reusable IMAP connections
        self._imap_manager = IMAPConnectionManager(email_server)

    async def close(self) -> None:
        """Close the IMAP connection. Call when done with the client."""
        await self._imap_manager.close()

    async def list_folders(self) -> list[str]:
        """List all available IMAP folders, with names decoded from UTF-7."""

        async def _list_folders(imap):
            result = await imap.list('""', '"*"')
            if result.result != "OK" or not result.lines:
                return ["INBOX"]

            folders = []
            for item in result.lines:
                if isinstance(item, bytes):
                    item = item.decode("utf-8", errors="replace")

                # Parse folder name from LIST response
                # Format: '(\\HasNoChildren) "/" "FolderName"'
                if '"' in item:
                    parts = item.split('"')
                    if len(parts) >= 2:
                        folder_name = parts[-2]
                        # Skip invalid folder names
                        if folder_name and folder_name != "|":
                            # Decode IMAP UTF-7 to readable names
                            decoded_name = decode_imap_utf7(folder_name)
                            folders.append(decoded_name)

            # Always include INBOX
            if "INBOX" not in folders:
                folders.insert(0, "INBOX")

            logger.debug(f"Available folders: {folders}")
            return folders

        return await self._imap_manager.execute_with_retry(_list_folders)

    def _parse_email_data(self, raw_email: bytes, email_id: str | None = None) -> dict[str, Any]:  # noqa: C901
        """Parse raw email data into a structured dictionary."""
        parser = BytesParser(policy=default)
        email_message = parser.parsebytes(raw_email)

        # Extract email parts
        subject = email_message.get("Subject", "")
        sender = email_message.get("From", "")
        date_str = email_message.get("Date", "")

        # Extract recipients
        to_addresses = []
        to_header = email_message.get("To", "")
        if to_header:
            # Simple parsing - split by comma and strip whitespace
            to_addresses = [addr.strip() for addr in to_header.split(",")]

        # Also check CC recipients
        cc_header = email_message.get("Cc", "")
        if cc_header:
            to_addresses.extend([addr.strip() for addr in cc_header.split(",")])

        # Parse date
        try:
            date_tuple = email.utils.parsedate_tz(date_str)
            date = (
                datetime.fromtimestamp(email.utils.mktime_tz(date_tuple), tz=timezone.utc)
                if date_tuple
                else datetime.now(timezone.utc)
            )
        except Exception:
            date = datetime.now(timezone.utc)

        # Get body content
        body = ""
        attachments = []

        if email_message.is_multipart():
            for part in email_message.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get("Content-Disposition", ""))

                # Handle attachments
                if "attachment" in content_disposition:
                    filename = part.get_filename()
                    if filename:
                        attachments.append(filename)
                # Handle text parts
                elif content_type == "text/plain":
                    body_part = part.get_payload(decode=True)
                    if body_part:
                        charset = part.get_content_charset("utf-8")
                        try:
                            body += body_part.decode(charset)
                        except UnicodeDecodeError:
                            body += body_part.decode("utf-8", errors="replace")
        else:
            # Handle plain text emails
            payload = email_message.get_payload(decode=True)
            if payload:
                charset = email_message.get_content_charset("utf-8")
                try:
                    body = payload.decode(charset)
                except UnicodeDecodeError:
                    body = payload.decode("utf-8", errors="replace")
        # TODO: Allow retrieving full email body
        if body and len(body) > 20000:
            body = body[:20000] + "...[TRUNCATED]"

        # Extract threading headers
        message_id = email_message.get("Message-ID", "")
        in_reply_to = email_message.get("In-Reply-To", "")
        references = email_message.get("References", "")

        return {
            "email_id": email_id or "",
            "subject": subject,
            "from": sender,
            "to": to_addresses,
            "body": body,
            "date": date,
            "attachments": attachments,
            "message_id": message_id,
            "in_reply_to": in_reply_to,
            "references": references,
        }

    @staticmethod
    def _build_search_criteria(
        before: datetime | None = None,
        since: datetime | None = None,
        subject: str | None = None,
        body: str | None = None,
        text: str | None = None,
        from_address: str | None = None,
        to_address: str | None = None,
    ):
        search_criteria = []
        if before:
            search_criteria.extend(["BEFORE", before.strftime("%d-%b-%Y")])
        if since:
            search_criteria.extend(["SINCE", since.strftime("%d-%b-%Y")])
        if subject:
            search_criteria.extend(["SUBJECT", subject])
        if body:
            search_criteria.extend(["BODY", body])
        if text:
            search_criteria.extend(["TEXT", text])
        if from_address:
            search_criteria.extend(["FROM", from_address])
        if to_address:
            search_criteria.extend(["TO", to_address])

        # If no specific criteria, search for ALL
        if not search_criteria:
            search_criteria = ["ALL"]

        return search_criteria

    async def get_email_count(
        self,
        before: datetime | None = None,
        since: datetime | None = None,
        subject: str | None = None,
        from_address: str | None = None,
        to_address: str | None = None,
        mailbox: str = "INBOX",
    ) -> int:
        async def _get_count(imap):
            search_criteria = self._build_search_criteria(
                before, since, subject, from_address=from_address, to_address=to_address
            )
            logger.info(f"Count: Search criteria: {search_criteria}")
            # Search for messages and count them - use UID SEARCH for consistency
            _, messages = await imap.uid_search(*search_criteria)
            return len(messages[0].split()) if messages and messages[0] else 0

        return await self._imap_manager.execute_with_retry(_get_count, folder=mailbox)

    async def get_emails_metadata_stream(  # noqa: C901
        self,
        page: int = 1,
        page_size: int = 10,
        before: datetime | None = None,
        since: datetime | None = None,
        subject: str | None = None,
        from_address: str | None = None,
        to_address: str | None = None,
        order: str = "desc",
        mailbox: str = "INBOX",
    ) -> AsyncGenerator[dict[str, Any], None]:
        # Use connection manager - select folder and get connection
        await self._imap_manager.select_folder(mailbox)
        imap = await self._imap_manager.ensure_connected()

        search_criteria = self._build_search_criteria(
            before, since, subject, from_address=from_address, to_address=to_address
        )
        logger.info(f"Get metadata: Search criteria: {search_criteria}")

        # Search for messages - use UID SEARCH for better compatibility
        _, messages = await imap.uid_search(*search_criteria)

        # Handle empty or None responses
        if not messages or not messages[0]:
            logger.warning("No messages returned from search")
            return

        email_ids = messages[0].split()
        logger.info(f"Found {len(email_ids)} email IDs")
        start = (page - 1) * page_size
        end = start + page_size

        if order == "desc":
            email_ids.reverse()

        # Fetch each message's metadata only
        for email_id in email_ids[start:end]:
            try:
                # Convert email_id from bytes to string
                email_id_str = email_id.decode("utf-8")

                # Fetch only headers to get metadata without body
                _, data = await imap.uid("fetch", email_id_str, "BODY.PEEK[HEADER]")

                if not data:
                    logger.error(f"Failed to fetch headers for UID {email_id_str}")
                    continue

                # Find the email headers in the response
                raw_headers = None
                if len(data) > 1 and isinstance(data[1], bytearray):
                    raw_headers = bytes(data[1])
                else:
                    # Search through all items for header content
                    for item in data:
                        if isinstance(item, bytes | bytearray) and len(item) > 10:
                            # Skip IMAP protocol responses
                            if isinstance(item, bytes) and b"FETCH" in item:
                                continue
                            # This is likely the header content
                            raw_headers = bytes(item) if isinstance(item, bytearray) else item
                            break

                if raw_headers:
                    try:
                        # Parse headers only
                        parser = BytesParser(policy=default)
                        email_message = parser.parsebytes(raw_headers)

                        # Extract metadata
                        subj = email_message.get("Subject", "")
                        sender = email_message.get("From", "")
                        date_str = email_message.get("Date", "")

                        # Extract recipients
                        to_addresses = []
                        to_header = email_message.get("To", "")
                        if to_header:
                            to_addresses = [addr.strip() for addr in to_header.split(",")]

                        cc_header = email_message.get("Cc", "")
                        if cc_header:
                            to_addresses.extend([addr.strip() for addr in cc_header.split(",")])

                        # Parse date
                        try:
                            date_tuple = email.utils.parsedate_tz(date_str)
                            date = (
                                datetime.fromtimestamp(email.utils.mktime_tz(date_tuple), tz=timezone.utc)
                                if date_tuple
                                else datetime.now(timezone.utc)
                            )
                        except Exception:
                            date = datetime.now(timezone.utc)

                        # For metadata, we don't fetch attachments to save bandwidth
                        metadata = {
                            "email_id": email_id_str,
                            "subject": subj,
                            "from": sender,
                            "to": to_addresses,
                            "date": date,
                            "attachments": [],  # We don't fetch attachment info for metadata
                        }
                        yield metadata
                    except Exception as e:
                        # Log error but continue with other emails
                        logger.error(f"Error parsing email metadata: {e!s}")
                else:
                    logger.error(f"Could not find header data in response for email ID: {email_id_str}")
            except Exception as e:
                logger.error(f"Error fetching email metadata {email_id}: {e!s}")

    async def get_uids_above_watermark(self, watermark: int, mailbox: str = "INBOX") -> list[int]:
        """Get all UIDs above a certain watermark (for incremental sync).

        Args:
            watermark: Fetch UIDs greater than this value (0 means all)
            mailbox: Folder to search

        Returns:
            List of UIDs above watermark, sorted ascending
        """
        async def _search_uids(imap):
            if watermark == 0:
                # First sync - get all UIDs
                _, messages = await imap.uid_search("ALL")
            else:
                # Incremental sync - get UIDs above watermark
                _, messages = await imap.uid_search(f"UID {watermark + 1}:*")

            if not messages or not messages[0]:
                return []

            uids = []
            for uid_bytes in messages[0].split():
                try:
                    uid = int(uid_bytes.decode("utf-8"))
                    # Filter out UIDs <= watermark (IMAP may return boundary)
                    if uid > watermark:
                        uids.append(uid)
                except ValueError:
                    continue

            return sorted(uids)

        return await self._imap_manager.execute_with_retry(_search_uids, folder=mailbox)

    async def fetch_metadata_for_uids(self, uids: list[int], mailbox: str = "INBOX") -> list[dict]:
        """Fetch email metadata for specific UIDs.

        Args:
            uids: List of UIDs to fetch
            mailbox: Folder to fetch from

        Returns:
            List of metadata dicts with uid, subject, sender, recipients, date
        """
        if not uids:
            return []

        async def _fetch_metadata(imap):
            results = []

            for uid in uids:
                try:
                    uid_str = str(uid)
                    _, data = await imap.uid("fetch", uid_str, "BODY.PEEK[HEADER]")

                    if not data:
                        logger.error(f"Failed to fetch headers for UID {uid_str}")
                        continue

                    # Find the email headers in the response
                    raw_headers = None
                    if len(data) > 1 and isinstance(data[1], bytearray):
                        raw_headers = bytes(data[1])
                    else:
                        for item in data:
                            if isinstance(item, bytes | bytearray) and len(item) > 10:
                                if isinstance(item, bytes) and b"FETCH" in item:
                                    continue
                                raw_headers = bytes(item) if isinstance(item, bytearray) else item
                                break

                    if raw_headers:
                        parser = BytesParser(policy=default)
                        email_message = parser.parsebytes(raw_headers)

                        subject = email_message.get("Subject", "")
                        sender = email_message.get("From", "")
                        date_str = email_message.get("Date", "")

                        # Extract recipients
                        to_addresses = []
                        to_header = email_message.get("To", "")
                        if to_header:
                            to_addresses = [addr.strip() for addr in to_header.split(",")]
                        cc_header = email_message.get("Cc", "")
                        if cc_header:
                            to_addresses.extend([addr.strip() for addr in cc_header.split(",")])

                        # Parse date
                        try:
                            date_tuple = email.utils.parsedate_tz(date_str)
                            date = (
                                datetime.fromtimestamp(email.utils.mktime_tz(date_tuple), tz=timezone.utc)
                                if date_tuple
                                else datetime.now(timezone.utc)
                            )
                        except Exception:
                            date = datetime.now(timezone.utc)

                        results.append({
                            "uid": uid,
                            "subject": subject,
                            "sender": sender,
                            "recipients": to_addresses,
                            "date": date.isoformat(),
                        })

                except Exception as e:
                    logger.error(f"Error fetching metadata for UID {uid}: {e}")

            return results

        return await self._imap_manager.execute_with_retry(_fetch_metadata, folder=mailbox)

    def _check_email_content(self, data: list) -> bool:
        """Check if the fetched data contains actual email content."""
        for item in data:
            if isinstance(item, bytes) and b"FETCH (" in item and b"RFC822" not in item and b"BODY" not in item:
                # This is just metadata, not actual content
                continue
            elif isinstance(item, bytes | bytearray) and len(item) > 100:
                # This looks like email content
                return True
        return False

    def _extract_raw_email(self, data: list) -> bytes | None:
        """Extract raw email bytes from IMAP response data."""
        # The email content is typically at index 1 as a bytearray
        if len(data) > 1 and isinstance(data[1], bytearray):
            return bytes(data[1])

        # Search through all items for email content
        for item in data:
            if isinstance(item, bytes | bytearray) and len(item) > 100:
                # Skip IMAP protocol responses
                if isinstance(item, bytes) and b"FETCH" in item:
                    continue
                # This is likely the email content
                return bytes(item) if isinstance(item, bytearray) else item
        return None

    async def _fetch_email_with_formats(self, imap, email_id: str) -> list | None:
        """Try different fetch formats to get email data."""
        fetch_formats = ["RFC822", "BODY[]", "BODY.PEEK[]", "(BODY.PEEK[])"]

        for fetch_format in fetch_formats:
            try:
                _, data = await imap.uid("fetch", email_id, fetch_format)

                if data and len(data) > 0 and self._check_email_content(data):
                    return data

            except Exception as e:
                logger.debug(f"Fetch format {fetch_format} failed: {e}")

        return None

    async def get_email_body_by_id(self, email_id: str, mailbox: str = "INBOX") -> dict[str, Any] | None:
        """Fetch a single email body by UID using connection pooling."""

        async def _fetch_body(imap):
            # Fetch the specific email by UID
            data = await self._fetch_email_with_formats(imap, email_id)
            if not data:
                logger.error(f"Failed to fetch UID {email_id} with any format")
                return None

            # Extract raw email data
            raw_email = self._extract_raw_email(data)
            if not raw_email:
                logger.error(f"Could not find email data in response for email ID: {email_id}")
                return None

            # Parse the email
            try:
                return self._parse_email_data(raw_email, email_id)
            except Exception as e:
                logger.error(f"Error parsing email: {e!s}")
                return None

        return await self._imap_manager.execute_with_retry(_fetch_body, folder=mailbox)

    async def get_email_bodies_batch(
        self,
        email_ids: list[str],
        mailbox: str = "INBOX",
    ) -> list[dict[str, Any] | None]:
        """Fetch multiple email bodies using a single connection.

        This is significantly more efficient than calling get_email_body_by_id
        in a loop, as it reuses the same connection for all fetches.

        Args:
            email_ids: List of email UIDs to fetch
            mailbox: Folder to fetch from

        Returns:
            List of parsed email data dicts (or None for failed fetches),
            in the same order as email_ids
        """

        async def _fetch_batch(imap):
            results = []

            for email_id in email_ids:
                try:
                    data = await self._fetch_email_with_formats(imap, email_id)
                    if not data:
                        logger.error(f"Failed to fetch UID {email_id} with any format")
                        results.append(None)
                        continue

                    raw_email = self._extract_raw_email(data)
                    if not raw_email:
                        logger.error(f"Could not find email data for email ID: {email_id}")
                        results.append(None)
                        continue

                    try:
                        parsed = self._parse_email_data(raw_email, email_id)
                        results.append(parsed)
                    except Exception as e:
                        logger.error(f"Error parsing email: {e!s}")
                        results.append(None)

                except Exception as e:
                    logger.error(f"Error fetching email {email_id}: {e!s}")
                    results.append(None)

            return results

        return await self._imap_manager.execute_with_retry(_fetch_batch, folder=mailbox)

    async def download_attachment(
        self,
        email_id: str,
        attachment_name: str,
        save_path: str,
        mailbox: str = "INBOX",
    ) -> dict[str, Any]:
        """Download a specific attachment from an email and save it to disk."""

        async def _download(imap):
            data = await self._fetch_email_with_formats(imap, email_id)
            if not data:
                msg = f"Failed to fetch email with UID {email_id}"
                logger.error(msg)
                raise ValueError(msg)

            raw_email = self._extract_raw_email(data)
            if not raw_email:
                msg = f"Could not find email data for email ID: {email_id}"
                logger.error(msg)
                raise ValueError(msg)

            parser = BytesParser(policy=default)
            email_message = parser.parsebytes(raw_email)

            # Find the attachment
            attachment_data = None
            mime_type = None

            if email_message.is_multipart():
                for part in email_message.walk():
                    content_disposition = str(part.get("Content-Disposition", ""))
                    if "attachment" in content_disposition:
                        filename = part.get_filename()
                        if filename == attachment_name:
                            attachment_data = part.get_payload(decode=True)
                            mime_type = part.get_content_type()
                            break

            if attachment_data is None:
                msg = f"Attachment '{attachment_name}' not found in email {email_id}"
                logger.error(msg)
                raise ValueError(msg)

            # Save to disk
            save_file = Path(save_path)
            save_file.parent.mkdir(parents=True, exist_ok=True)
            save_file.write_bytes(attachment_data)

            logger.info(f"Attachment '{attachment_name}' saved to {save_path}")

            return {
                "email_id": email_id,
                "attachment_name": attachment_name,
                "mime_type": mime_type or "application/octet-stream",
                "size": len(attachment_data),
                "saved_path": str(save_file.resolve()),
            }

        return await self._imap_manager.execute_with_retry(_download, folder=mailbox)

    def _validate_attachment(self, file_path: str) -> Path:
        """Validate attachment file path."""
        path = Path(file_path)
        if not path.exists():
            msg = f"Attachment file not found: {file_path}"
            logger.error(msg)
            raise FileNotFoundError(msg)

        if not path.is_file():
            msg = f"Attachment path is not a file: {file_path}"
            logger.error(msg)
            raise ValueError(msg)

        return path

    def _create_attachment_part(self, path: Path) -> MIMEApplication:
        """Create MIME attachment part from file."""
        with open(path, "rb") as f:
            file_data = f.read()

        mime_type, _ = mimetypes.guess_type(str(path))
        if mime_type is None:
            mime_type = "application/octet-stream"

        attachment_part = MIMEApplication(file_data, _subtype=mime_type.split("/")[1])
        attachment_part.add_header(
            "Content-Disposition",
            "attachment",
            filename=path.name,
        )
        logger.info(f"Attached file: {path.name} ({mime_type})")
        return attachment_part

    def _create_message_with_attachments(self, body: str, html: bool, attachments: list[str]) -> MIMEMultipart:
        """Create multipart message with attachments."""
        msg = MIMEMultipart()
        content_type = "html" if html else "plain"
        text_part = MIMEText(body, content_type, "utf-8")
        msg.attach(text_part)

        for file_path in attachments:
            try:
                path = self._validate_attachment(file_path)
                attachment_part = self._create_attachment_part(path)
                msg.attach(attachment_part)
            except Exception as e:
                logger.error(f"Failed to attach file {file_path}: {e}")
                raise

        return msg

    async def send_email(
        self,
        recipients: list[str],
        subject: str,
        body: str,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        html: bool = False,
        attachments: list[str] | None = None,
        in_reply_to: str | None = None,
        references: str | None = None,
    ):
        # Create message with or without attachments
        if attachments:
            msg = self._create_message_with_attachments(body, html, attachments)
        else:
            content_type = "html" if html else "plain"
            msg = MIMEText(body, content_type, "utf-8")

        # Generate a unique Message-ID for this email
        domain = self.sender.split("@")[-1].rstrip(">") if "@" in self.sender else "localhost"
        msg["Message-ID"] = f"<{uuid.uuid4()}@{domain}>"

        # Add threading headers for replies
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
        if references:
            msg["References"] = references

        # Handle subject with special characters
        if any(ord(c) > 127 for c in subject):
            msg["Subject"] = Header(subject, "utf-8")
        else:
            msg["Subject"] = subject

        # Handle sender name with special characters
        if any(ord(c) > 127 for c in self.sender):
            msg["From"] = Header(self.sender, "utf-8")
        else:
            msg["From"] = self.sender

        msg["To"] = ", ".join(recipients)

        # Add Date header
        msg["Date"] = email.utils.formatdate(localtime=True)

        # Add CC header if provided (visible to recipients)
        if cc:
            msg["Cc"] = ", ".join(cc)

        # Note: BCC recipients are not added to headers (they remain hidden)
        # but will be included in the actual recipients for SMTP delivery

        async with aiosmtplib.SMTP(
            hostname=self.email_server.host,
            port=self.email_server.port,
            start_tls=self.smtp_start_tls,
            use_tls=self.smtp_use_tls,
        ) as smtp:
            await smtp.login(self.email_server.user_name, self.email_server.password)

            # Create a combined list of all recipients for delivery
            all_recipients = recipients.copy()
            if cc:
                all_recipients.extend(cc)
            if bcc:
                all_recipients.extend(bcc)

            await smtp.send_message(msg, recipients=all_recipients)

        return msg

    async def _find_sent_folder(self, imap) -> str | None:
        """Find the Sent folder name for this IMAP server."""
        try:
            result = await imap.list('""', '"*"')
            if result.result != "OK" or not result.lines:
                return None
            folders_data = result.lines

            # Parse folder names from LIST response
            available_folders = []
            for item in folders_data:
                if isinstance(item, bytes):
                    item = item.decode("utf-8", errors="replace")
                # Extract folder name from LIST response like '(\\HasNoChildren) "/" "Sent"'
                if '"' in item:
                    parts = item.split('"')
                    if len(parts) >= 2:
                        folder_name = parts[-2]
                        available_folders.append(folder_name)

            logger.debug(f"Available folders: {available_folders}")

            # Try to find a matching Sent folder
            for sent_name in SENT_FOLDER_NAMES:
                for folder in available_folders:
                    # Decode folder name from IMAP UTF-7 for comparison
                    decoded_folder = decode_imap_utf7(folder)
                    if decoded_folder.lower() == sent_name.lower() or decoded_folder.lower().endswith(sent_name.lower()):
                        logger.info(f"Found Sent folder: {folder} (decoded: {decoded_folder})")
                        return folder

            # Fallback: look for any folder containing "sent" (case-insensitive)
            for folder in available_folders:
                decoded_folder = decode_imap_utf7(folder)
                if "sent" in decoded_folder.lower() or "отправлен" in decoded_folder.lower():
                    logger.info(f"Found Sent folder (fallback): {folder}")
                    return folder

            logger.warning("Could not find Sent folder")
            return None

        except Exception as e:
            logger.error(f"Error finding Sent folder: {e}")
            return None

    async def save_to_sent_folder(self, msg, imap_server: "EmailServer") -> bool:
        """Save a message to the Sent folder via IMAP."""
        imap = self.imap_class(imap_server.host, imap_server.port)
        try:
            await imap._client_task
            await imap.wait_hello_from_server()
            await imap.login(imap_server.user_name, imap_server.password)

            sent_folder = await self._find_sent_folder(imap)
            if not sent_folder:
                logger.warning("Sent folder not found, message will not be saved")
                return False

            # Convert message to bytes
            msg_bytes = msg.as_bytes()

            # Append to Sent folder with \Seen flag
            # Note: aioimaplib.append takes (message_bytes, mailbox, flags, date)
            result = await imap.append(msg_bytes, mailbox=sent_folder, flags="(\\Seen)")
            logger.info(f"Saved message to Sent folder: {result}")
            return True

        except Exception as e:
            logger.error(f"Failed to save message to Sent folder: {e}")
            return False
        finally:
            try:
                await imap.logout()
            except Exception as e:
                logger.debug(f"Error during IMAP logout: {e}")

    async def delete_emails(self, email_ids: list[str], mailbox: str = "INBOX") -> tuple[list[str], list[str]]:
        """Delete emails by their UIDs. Returns (deleted_ids, failed_ids)."""

        async def _delete(imap):
            deleted_ids = []
            failed_ids = []

            for email_id in email_ids:
                try:
                    await imap.uid("store", email_id, "+FLAGS", r"(\Deleted)")
                    deleted_ids.append(email_id)
                except Exception as e:
                    logger.error(f"Failed to delete email {email_id}: {e}")
                    failed_ids.append(email_id)

            await imap.expunge()
            return deleted_ids, failed_ids

        return await self._imap_manager.execute_with_retry(_delete, folder=mailbox)


class ClassicEmailHandler(EmailHandler):
    def __init__(self, email_settings: EmailSettings):
        self.email_settings = email_settings
        self.incoming_client = EmailClient(email_settings.incoming)
        self.outgoing_client = EmailClient(
            email_settings.outgoing,
            sender=f"{email_settings.full_name} <{email_settings.email_address}>",
        )
        self._yandex_calculator = None  # Cached calculator to avoid repeated syncs

    async def list_folders(self) -> list[str]:
        """List all available folders for this email account."""
        return await self.incoming_client.list_folders()

    async def get_emails_metadata(
        self,
        page: int = 1,
        page_size: int = 10,
        before: datetime | None = None,
        since: datetime | None = None,
        subject: str | None = None,
        from_address: str | None = None,
        to_address: str | None = None,
        order: str = "desc",
        mailbox: str = "INBOX",
    ) -> EmailMetadataPageResponse:
        # If filters are applied, fall back to IMAP-only (existing behavior)
        has_filters = any([before, since, subject, from_address, to_address])

        if has_filters:
            # Use original IMAP-based approach for filtered queries
            emails = []
            async for email_data in self.incoming_client.get_emails_metadata_stream(
                page, page_size, before, since, subject, from_address, to_address, order, mailbox
            ):
                emails.append(EmailMetadata.from_email(email_data))
            total = await self.incoming_client.get_email_count(
                before, since, subject, from_address=from_address, to_address=to_address, mailbox=mailbox
            )
            return EmailMetadataPageResponse(
                page=page,
                page_size=page_size,
                before=before,
                since=since,
                subject=subject,
                emails=emails,
                total=total,
            )

        # Hybrid approach: cache + IMAP for new emails only
        account = self.email_settings.account_name

        # Ensure cache is initialized
        cache.init_db()

        # 1. Get watermark (highest cached UID)
        watermark = cache.get_watermark(account, mailbox)
        logger.debug(f"Watermark for {account}/{mailbox}: {watermark}")

        # 2. Fetch new UIDs from IMAP (always fresh for new mail)
        new_uids = await self.incoming_client.get_uids_above_watermark(watermark, mailbox)

        if new_uids:
            logger.info(f"Found {len(new_uids)} new emails above watermark {watermark}")

            # 3. Fetch metadata for new emails
            new_metadata = await self.incoming_client.fetch_metadata_for_uids(new_uids, mailbox)

            # 4. Cache new metadata
            if new_metadata:
                cache.store_metadata(account, mailbox, new_metadata)

            # 5. Update watermark to highest UID
            max_uid = max(new_uids)
            cache.set_watermark(account, mailbox, max_uid)
            logger.debug(f"Updated watermark to {max_uid}")

        # 6. Get requested page from cache
        cached_metadata, total = cache.get_metadata_page(
            account, mailbox, page, page_size, order
        )

        # Convert cached dicts to EmailMetadata objects
        emails = []
        for meta in cached_metadata:
            # Parse date string back to datetime
            date = datetime.now(timezone.utc)
            if meta.get("date"):
                try:
                    date = datetime.fromisoformat(meta["date"])
                except Exception:
                    pass

            emails.append(EmailMetadata(
                email_id=str(meta["uid"]),
                subject=meta.get("subject") or "",
                sender=meta.get("sender") or "",
                recipients=meta.get("recipients") or [],
                date=date,
                attachments=[],  # Metadata doesn't include attachments
            ))

        return EmailMetadataPageResponse(
            page=page,
            page_size=page_size,
            before=before,
            since=since,
            subject=subject,
            emails=emails,
            total=total,
        )

    async def get_emails_content(self, email_ids: list[str], mailbox: str = "INBOX") -> EmailContentBatchResponse:
        """Batch retrieve email body content using cache-first approach with IMAP fallback."""
        emails = []
        failed_ids = []
        account = self.email_settings.account_name

        # Ensure cache is initialized
        cache.init_db()

        # Use cached Yandex link calculator if configured (avoids repeated syncs)
        yandex_calculator = None
        if self.email_settings.yandex_link and self.email_settings.yandex_link.enabled:
            if self._yandex_calculator is None:
                self._yandex_calculator = YandexLinkCalculator(self.email_settings)
            yandex_calculator = self._yandex_calculator

        # Separate cached and uncached emails
        cached_bodies = {}
        uncached_ids = []

        for email_id in email_ids:
            uid = int(email_id)
            cached = cache.get_body(account, mailbox, uid)
            if cached:
                cached_bodies[email_id] = cached
                logger.debug(f"Cache hit for body {account}/{mailbox}/{uid}")
            else:
                uncached_ids.append(email_id)

        # Fetch uncached emails from IMAP
        imap_results = {}
        if uncached_ids:
            logger.info(f"Fetching {len(uncached_ids)} uncached bodies from IMAP")
            email_data_list = await self.incoming_client.get_email_bodies_batch(uncached_ids, mailbox)

            for email_id, email_data in zip(uncached_ids, email_data_list):
                imap_results[email_id] = email_data

                # Cache successfully fetched bodies
                if email_data:
                    uid = int(email_id)
                    cache.store_body(
                        account, mailbox, uid,
                        body_text=email_data.get("body"),
                        body_html=None,  # We don't store HTML separately yet
                        attachments=email_data.get("attachments", []),
                    )
                    # Also cache metadata if we have it
                    cache.store_metadata(account, mailbox, [{
                        "uid": uid,
                        "subject": email_data.get("subject"),
                        "sender": email_data.get("from"),
                        "recipients": email_data.get("to", []),
                        "date": email_data.get("date").isoformat() if email_data.get("date") else None,
                    }])

        # Build response for each requested email
        for email_id in email_ids:
            uid = int(email_id)

            # Check if we have cached body
            if email_id in cached_bodies:
                cached = cached_bodies[email_id]

                # Get metadata from cache
                meta = cache.get_metadata_for_uid(account, mailbox, uid)

                # Parse date
                date = datetime.now(timezone.utc)
                if meta and meta.get("date"):
                    try:
                        date = datetime.fromisoformat(meta["date"])
                    except Exception:
                        pass

                # Calculate web_url
                web_url = None
                if yandex_calculator:
                    try:
                        web_url = yandex_calculator.get_web_url(mailbox, uid, date)
                    except Exception as e:
                        logger.warning(f"Failed to calculate web_url for {email_id}: {e}")

                emails.append(EmailBodyResponse(
                    email_id=email_id,
                    status="ok",
                    subject=meta.get("subject", "") if meta else "",
                    sender=meta.get("sender", "") if meta else "",
                    recipients=meta.get("recipients", []) if meta else [],
                    date=date,
                    body=cached.get("body_text", ""),
                    attachments=cached.get("attachments", []),
                    web_url=web_url,
                ))

            # Check IMAP results for uncached emails
            elif email_id in imap_results:
                email_data = imap_results[email_id]

                if email_data:
                    # Successfully fetched from IMAP
                    web_url = None
                    if yandex_calculator:
                        try:
                            web_url = yandex_calculator.get_web_url(
                                mailbox, uid, email_data.get("date")
                            )
                        except Exception as e:
                            logger.warning(f"Failed to calculate web_url for {email_id}: {e}")

                    emails.append(EmailBodyResponse(
                        email_id=email_data["email_id"],
                        status="ok",
                        subject=email_data["subject"],
                        sender=email_data["from"],
                        recipients=email_data["to"],
                        date=email_data["date"],
                        body=email_data["body"],
                        attachments=email_data["attachments"],
                        message_id=email_data.get("message_id", ""),
                        in_reply_to=email_data.get("in_reply_to", ""),
                        references=email_data.get("references", ""),
                        web_url=web_url,
                    ))
                else:
                    # Email not found on IMAP - deleted or moved
                    logger.warning(f"Email {email_id} not found on server, returning not_found status")

                    # Clean up cache for this email
                    cache.delete_email(account, mailbox, uid)

                    emails.append(EmailBodyResponse.not_found(
                        email_id,
                        message="Email no longer exists on server (deleted or moved)"
                    ))
                    failed_ids.append(email_id)
            else:
                # Should not happen, but handle gracefully
                logger.error(f"Email {email_id} not in cache or IMAP results")
                emails.append(EmailBodyResponse.not_found(email_id))
                failed_ids.append(email_id)

        return EmailContentBatchResponse(
            emails=emails,
            requested_count=len(email_ids),
            retrieved_count=len(email_ids) - len(failed_ids),
            failed_ids=failed_ids,
        )

    async def send_email(
        self,
        recipients: list[str],
        subject: str,
        body: str,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        html: bool = False,
        attachments: list[str] | None = None,
        in_reply_to: str | None = None,
        references: str | None = None,
    ) -> None:
        msg = await self.outgoing_client.send_email(
            recipients, subject, body, cc, bcc, html, attachments, in_reply_to, references
        )

        # Save to Sent folder using incoming (IMAP) server settings
        await self.outgoing_client.save_to_sent_folder(msg, self.email_settings.incoming)

    async def delete_emails(self, email_ids: list[str], mailbox: str = "INBOX") -> tuple[list[str], list[str]]:
        """Delete emails by their UIDs. Returns (deleted_ids, failed_ids)."""
        deleted_ids, failed_ids = await self.incoming_client.delete_emails(email_ids, mailbox)

        # Invalidate cache for deleted emails
        account = self.email_settings.account_name
        for uid in deleted_ids:
            cache.delete_email(account, mailbox, int(uid))

        return deleted_ids, failed_ids

    async def download_attachment(
        self,
        email_id: str,
        attachment_name: str,
        save_path: str,
    ) -> AttachmentDownloadResponse:
        """Download an email attachment and save it to the specified path."""
        result = await self.incoming_client.download_attachment(email_id, attachment_name, save_path)
        return AttachmentDownloadResponse(
            email_id=result["email_id"],
            attachment_name=result["attachment_name"],
            mime_type=result["mime_type"],
            size=result["size"],
            saved_path=result["saved_path"],
        )

    async def reply_to_email(
        self,
        email_id: str,
        body: str,
        reply_all: bool = False,
        html: bool = False,
        attachments: list[str] | None = None,
    ) -> str:
        """Reply to an email, properly setting threading headers."""
        # Fetch the original email to get threading info
        original = await self.incoming_client.get_email_body_by_id(email_id)
        if not original:
            raise ValueError(f"Original email with ID {email_id} not found")

        # Build reply subject
        original_subject = original.get("subject", "")
        if original_subject.lower().startswith("re:"):
            reply_subject = original_subject
        else:
            reply_subject = f"Re: {original_subject}"

        # Build threading headers
        original_message_id = original.get("message_id", "")
        original_references = original.get("references", "")

        # In-Reply-To is the Message-ID of the email we're replying to
        in_reply_to = original_message_id

        # References is the chain: original's References + original's Message-ID
        if original_references and original_message_id:
            references = f"{original_references} {original_message_id}"
        elif original_message_id:
            references = original_message_id
        else:
            references = ""

        # Determine recipients
        original_sender = original.get("from", "")
        if reply_all:
            # Reply to sender + all original recipients (excluding ourselves)
            recipients = [original_sender]
            original_to = original.get("to", [])
            my_address = self.email_settings.email_address.lower()
            for addr in original_to:
                # Skip our own address
                if my_address not in addr.lower():
                    recipients.append(addr)
        else:
            # Reply only to sender
            recipients = [original_sender]

        # Send the reply
        await self.send_email(
            recipients=recipients,
            subject=reply_subject,
            body=body,
            html=html,
            attachments=attachments,
            in_reply_to=in_reply_to,
            references=references,
        )

        return f"Reply sent to {', '.join(recipients)}"
