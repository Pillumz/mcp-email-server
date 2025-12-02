"""Yandex Mail web link calculator.

Calculates direct web URLs for Yandex Mail messages by tracking
message positions across all folders.
"""

from __future__ import annotations

import codecs
import re
from datetime import datetime
from typing import TYPE_CHECKING

import aioimaplib

from mcp_email_server import cache
from mcp_email_server.emails.imap_connection import IMAPConnectionManager
from mcp_email_server.log import logger


def decode_imap_utf7(s: str) -> str:
    """Decode IMAP Modified UTF-7 folder name to Unicode.

    IMAP uses a modified UTF-7 encoding where & is used instead of +
    and , is used instead of /.
    """
    if "&" not in s:
        return s

    # Convert IMAP modified UTF-7 to standard UTF-7
    # Replace &- with & (literal ampersand)
    # Replace & with + and , with / for base64 sections
    result = []
    i = 0
    while i < len(s):
        if s[i] == "&":
            if i + 1 < len(s) and s[i + 1] == "-":
                # &- means literal &
                result.append("&")
                i += 2
            else:
                # Find the closing -
                j = s.find("-", i + 1)
                if j == -1:
                    result.append(s[i:])
                    break
                # Extract and convert the encoded part
                encoded = s[i + 1:j].replace(",", "/")
                try:
                    # Convert to standard UTF-7 and decode
                    utf7_str = "+" + encoded + "-"
                    decoded = utf7_str.encode("ascii").decode("utf-7")
                    result.append(decoded)
                except Exception:
                    result.append(s[i:j + 1])
                i = j + 1
        else:
            result.append(s[i])
            i += 1

    return "".join(result)


def encode_imap_utf7(s: str) -> str:
    """Encode Unicode folder name to IMAP Modified UTF-7.

    IMAP uses a modified UTF-7 encoding where + is replaced with &
    and / is replaced with , in base64 sections.
    """
    # Check if encoding is needed (only ASCII printable chars don't need encoding)
    if all(0x20 <= ord(c) <= 0x7e for c in s) and "&" not in s:
        return s

    result = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == "&":
            # Literal ampersand becomes &-
            result.append("&-")
            i += 1
        elif 0x20 <= ord(c) <= 0x7e:
            # ASCII printable - pass through
            result.append(c)
            i += 1
        else:
            # Find run of non-ASCII characters
            j = i
            while j < len(s) and not (0x20 <= ord(s[j]) <= 0x7e):
                j += 1
            # Encode the run using UTF-7
            non_ascii = s[i:j]
            try:
                utf7_encoded = non_ascii.encode("utf-7").decode("ascii")
                # Convert from standard UTF-7 to IMAP modified UTF-7
                # Remove leading + and trailing -
                if utf7_encoded.startswith("+") and utf7_encoded.endswith("-"):
                    imap_encoded = "&" + utf7_encoded[1:-1].replace("/", ",") + "-"
                else:
                    imap_encoded = "&" + utf7_encoded[1:].replace("/", ",")
                    if not imap_encoded.endswith("-"):
                        imap_encoded += "-"
                result.append(imap_encoded)
            except Exception:
                result.append(non_ascii)
            i = j

    return "".join(result)


if TYPE_CHECKING:
    from mcp_email_server.config import EmailSettings, YandexLinkConfig


class YandexLinkCalculator:
    """Calculate Yandex Mail web URLs for messages.

    Web IDs in Yandex Mail are sequential across all folders.
    This calculator tracks message positions and calculates web_ids
    based on a known baseline.
    """

    def __init__(
        self,
        email_settings: "EmailSettings",
        imap_manager: IMAPConnectionManager | None = None,
    ):
        self.email_settings = email_settings
        self.baseline: "YandexLinkConfig" = email_settings.yandex_link  # type: ignore
        self.account = email_settings.account_name
        self._synced = False  # Track if we've already synced in this session

        # Use provided connection manager or create our own
        if imap_manager is not None:
            self._imap_manager = imap_manager
            self._owns_manager = False
        else:
            self._imap_manager = IMAPConnectionManager(email_settings.incoming)
            self._owns_manager = True

        # Ensure cache is initialized
        cache.init_db()

    async def close(self) -> None:
        """Close connection if we own it."""
        if self._owns_manager:
            await self._imap_manager.close()

    async def get_web_url(self, folder: str, uid: int) -> str:
        """Get web URL for a message.

        Args:
            folder: IMAP folder name
            uid: IMAP UID

        Returns:
            Direct URL to the message in Yandex Mail web interface
        """
        # 1. Check cache
        cached_web_id = cache.get_web_id(self.account, folder, uid)
        if cached_web_id:
            logger.debug(f"Cache hit for {self.account}/{folder}/{uid}: {cached_web_id}")
            return self._format_url(cached_web_id, folder)

        # 2. Cache miss - sync only once per session to avoid repeated slow syncs
        if not self._synced:
            logger.info(f"Cache miss for {self.account}/{folder}/{uid}, syncing...")
            await self._sync_messages()
            self._synced = True

            # 3. Lookup again (should exist now)
            web_id = cache.get_web_id(self.account, folder, uid)
            if web_id:
                return self._format_url(web_id, folder)

        # 4. If still not found, the message might be older than baseline
        # Fall back to baseline URL (user can navigate from there)
        logger.warning(f"Could not calculate web_id for {folder}/{uid}, using baseline")
        return self._format_url(self.baseline.baseline_web_id, self.baseline.baseline_folder)

    async def _sync_messages(self) -> None:
        """Fetch messages from all folders and update cache."""
        # Get last sync state
        last_sync = cache.get_last_sync_date(self.account)
        max_web_id = cache.get_max_web_id(self.account)

        # Determine start date for sync
        if last_sync and max_web_id:
            since_date = last_sync
            start_web_id = max_web_id
        else:
            # First sync - start from baseline
            since_date = self.baseline.baseline_date
            start_web_id = self.baseline.baseline_web_id

            # Also need to cache the baseline message itself
            cache.bulk_insert_messages(self.account, [{
                "folder": self.baseline.baseline_folder,
                "uid": self.baseline.baseline_uid,
                "internal_date": self.baseline.baseline_date.isoformat(),
                "web_id": self.baseline.baseline_web_id,
            }])

        logger.info(f"Syncing messages for {self.account} since {since_date}")

        # Use connection manager
        imap = await self._imap_manager.ensure_connected()

        # Get all folders
        folders = await self._get_all_folders(imap)
        logger.info(f"Found {len(folders)} folders")

        # Fetch messages from all folders
        all_messages = []
        for folder in folders:
            messages = await self._fetch_folder_messages(imap, folder, since_date)
            all_messages.extend(messages)

        if not all_messages:
            logger.info("No new messages to sync")
            return

        # Sort by date
        all_messages.sort(key=lambda m: m["internal_date"])

        # Find baseline position in sorted list
        baseline_key = (self.baseline.baseline_folder, self.baseline.baseline_uid)
        baseline_pos = None
        for i, msg in enumerate(all_messages):
            msg_key = (msg["folder"], msg["uid"])
            if msg_key == baseline_key:
                baseline_pos = i
                break

        # Assign web_ids relative to baseline position
        # web_id = baseline_web_id + (position - baseline_position)
        if baseline_pos is not None:
            for i, msg in enumerate(all_messages):
                msg["web_id"] = self.baseline.baseline_web_id + (i - baseline_pos)
        else:
            # Baseline not in list - use start_web_id for incremental sync
            current_web_id = start_web_id
            for msg in all_messages:
                current_web_id += 1
                msg["web_id"] = current_web_id

        # Bulk insert to cache
        cache.bulk_insert_messages(self.account, all_messages)

        # Update sync state
        if all_messages:
            newest_date = datetime.fromisoformat(all_messages[-1]["internal_date"])
            newest_web_id = all_messages[-1]["web_id"]
            cache.update_sync_state(self.account, newest_date, newest_web_id)
            logger.info(f"Synced {len(all_messages)} messages, max_web_id={newest_web_id}")

    async def _get_all_folders(self, imap) -> list[str]:
        """Get list of all IMAP folders."""
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
                        folders.append(folder_name)

        # Always include INBOX as it's a special folder
        if "INBOX" not in folders:
            folders.insert(0, "INBOX")

        logger.debug(f"Folders: {folders}")
        return folders

    async def _fetch_folder_messages(
        self, imap, folder: str, since_date: datetime
    ) -> list[dict]:
        """Fetch messages from a folder since a given date."""
        messages = []

        try:
            result = await imap.select(folder)
            if result.result != "OK":
                logger.debug(f"Could not select folder {folder}")
                return []

            # Search for messages since date (use proper case, not uppercase)
            date_str = since_date.strftime("%d-%b-%Y")
            result = await imap.uid_search("SINCE", date_str)
            data = result.lines

            if not data or not data[0]:
                return []

            uids = data[0].split()
            logger.debug(f"Folder {folder}: {len(uids)} messages since {date_str}")

            # Fetch internal dates for all UIDs
            for uid_bytes in uids:
                uid_str = uid_bytes.decode() if isinstance(uid_bytes, bytes) else str(uid_bytes)

                try:
                    fetch_result = await imap.uid("fetch", uid_str, "(INTERNALDATE)")

                    if fetch_result.result != "OK" or not fetch_result.lines:
                        continue

                    # Parse INTERNALDATE from response
                    for item in fetch_result.lines:
                        if isinstance(item, bytes):
                            item = item.decode("utf-8", errors="replace")

                        if "INTERNALDATE" in str(item):
                            date_match = re.search(r'INTERNALDATE "([^"]+)"', str(item))
                            if date_match:
                                date_str_raw = date_match.group(1)
                                try:
                                    dt = datetime.strptime(
                                        date_str_raw, "%d-%b-%Y %H:%M:%S %z"
                                    )
                                    messages.append({
                                        "folder": decode_imap_utf7(folder),
                                        "uid": int(uid_str),
                                        "internal_date": dt.isoformat(),
                                    })
                                except ValueError as e:
                                    logger.debug(f"Date parse error: {e}")
                            break

                except Exception as e:
                    logger.debug(f"Error fetching UID {uid_str} in {folder}: {e}")

        except Exception as e:
            logger.debug(f"Error processing folder {folder}: {e}")

        return messages

    def _format_url(self, web_id: int, folder: str) -> str:
        """Format web_id as Yandex Mail URL with folder."""
        # Decode IMAP UTF-7 folder name to match config keys
        decoded_folder = decode_imap_utf7(folder)
        folder_id = self.baseline.folder_ids.get(decoded_folder, 1)  # Default to 1 (INBOX)
        return f"https://{self.baseline.url_prefix}/touch/folder/{folder_id}/thread/{web_id}"
