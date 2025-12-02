"""Yandex Mail web link calculator.

Calculates direct web URLs for Yandex Mail messages using MID (message ID).
MID structure: high12 digits (timestamp-based) + low6 digits (global sequence).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from mcp_email_server import cache
from mcp_email_server.log import logger

if TYPE_CHECKING:
    from mcp_email_server.config import EmailSettings, YandexLinkConfig

# MID formula constants (derived from analysis)
# high12 = MID_BASE + (unix_timestamp * MID_FACTOR)
MID_BASE = 134694013349
MID_FACTOR = 32.1354


def decode_imap_utf7(s: str) -> str:
    """Decode IMAP Modified UTF-7 folder name to Unicode.

    IMAP uses a modified UTF-7 encoding where & is used instead of +
    and , is used instead of /.
    """
    if "&" not in s:
        return s

    result = []
    i = 0
    while i < len(s):
        if s[i] == "&":
            if i + 1 < len(s) and s[i + 1] == "-":
                result.append("&")
                i += 2
            else:
                j = s.find("-", i + 1)
                if j == -1:
                    result.append(s[i:])
                    break
                encoded = s[i + 1:j].replace(",", "/")
                try:
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
    """Encode Unicode folder name to IMAP Modified UTF-7."""
    if all(0x20 <= ord(c) <= 0x7e for c in s) and "&" not in s:
        return s

    result = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == "&":
            result.append("&-")
            i += 1
        elif 0x20 <= ord(c) <= 0x7e:
            result.append(c)
            i += 1
        else:
            j = i
            while j < len(s) and not (0x20 <= ord(s[j]) <= 0x7e):
                j += 1
            non_ascii = s[i:j]
            try:
                utf7_encoded = non_ascii.encode("utf-7").decode("ascii")
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


def calculate_high12(timestamp: datetime) -> int:
    """Calculate the high 12 digits of MID from timestamp.

    Args:
        timestamp: Message timestamp (with timezone)

    Returns:
        High 12 digits of MID
    """
    unix_ts = timestamp.timestamp()
    return int(MID_BASE + (unix_ts * MID_FACTOR))


def estimate_mid(timestamp: datetime, folder: str, uid: int, account: str) -> int:
    """Estimate MID from timestamp and per-folder reference.

    Args:
        timestamp: Message timestamp
        folder: IMAP folder name
        uid: IMAP UID
        account: Account name

    Returns:
        Estimated MID
    """
    # High 12 digits from timestamp (always accurate)
    high12 = calculate_high12(timestamp)

    # Low 6 digits from per-folder reference (approximate)
    ref = cache.get_folder_reference(account, folder)
    if ref:
        ref_uid, ref_mid = ref
        ref_low6 = ref_mid % 1_000_000
        # Estimate: low6 changes by uid difference
        estimated_low6 = ref_low6 + (uid - ref_uid)
        # Clamp to valid range
        estimated_low6 = max(0, min(999999, estimated_low6))
    else:
        # No reference - use a default based on uid
        # This will likely be wrong but better than nothing
        estimated_low6 = uid % 1_000_000

    return high12 * 1_000_000 + estimated_low6


class YandexLinkCalculator:
    """Calculate Yandex Mail web URLs for messages.

    Uses a hybrid approach:
    1. Check cache for exact MID
    2. If not found, estimate MID using timestamp + per-folder offset
    3. Optionally sync from Web API when cookies are available
    """

    def __init__(self, email_settings: "EmailSettings"):
        self.email_settings = email_settings
        self.config: "YandexLinkConfig" = email_settings.yandex_link  # type: ignore
        self.account = email_settings.account_name

        # Ensure cache is initialized
        cache.init_db()

    def get_web_url(self, folder: str, uid: int, timestamp: datetime | None = None) -> str:
        """Get web URL for a message.

        Args:
            folder: IMAP folder name
            uid: IMAP UID
            timestamp: Optional message timestamp (for estimation)

        Returns:
            Direct URL to the message/thread in Yandex Mail web interface
        """
        # Decode IMAP UTF-7 folder name
        decoded_folder = decode_imap_utf7(folder)

        # 1. Check cache for exact MID and TID
        cached_ids = cache.get_message_ids(self.account, decoded_folder, uid)
        if cached_ids:
            mid, tid = cached_ids
            logger.debug(f"Cache hit for {self.account}/{decoded_folder}/{uid}: mid={mid}, tid={tid}")
            return self._format_url(tid, decoded_folder)

        # 2. Estimate MID if timestamp provided (tid = mid for estimated)
        if timestamp:
            mid = estimate_mid(timestamp, decoded_folder, uid, self.account)
            logger.debug(f"Estimated MID for {decoded_folder}/{uid}: {mid}")
            return self._format_url(mid, decoded_folder)

        # 3. Try to get reference and estimate without timestamp
        ref = cache.get_folder_reference(self.account, decoded_folder)
        if ref:
            ref_uid, ref_mid = ref
            # Simple linear estimate
            mid = ref_mid + (uid - ref_uid)
            logger.debug(f"Reference-based MID for {decoded_folder}/{uid}: {mid}")
            return self._format_url(mid, decoded_folder)

        # 4. Fallback: return URL to folder (user can find message)
        logger.warning(f"Could not calculate MID for {decoded_folder}/{uid}, returning folder URL")
        folder_id = self.config.folder_ids.get(decoded_folder, 1)
        return f"https://{self.config.url_prefix}/touch/folder/{folder_id}"

    async def sync_from_web_api(
        self,
        imap_messages_by_folder: dict[str, list[dict]],
        count_per_folder: int = 50,
    ) -> int:
        """Sync MIDs from Yandex Web API.

        Args:
            imap_messages_by_folder: Dict mapping folder -> list of message dicts
                                     Each dict needs: uid, subject, date
            count_per_folder: Number of messages to fetch per folder

        Returns:
            Number of MIDs synced
        """
        if not self.config.cookies_file:
            logger.warning("No cookies file configured, cannot sync from Web API")
            return 0

        from mcp_email_server.emails.yandex_web_api import YandexWebAPI

        api = YandexWebAPI(self.config)
        try:
            synced = await api.sync_mids_to_cache(
                account=self.account,
                imap_messages_by_folder=imap_messages_by_folder,
                count_per_folder=count_per_folder,
            )
            logger.info(f"Synced {synced} MIDs from Web API for {self.account}")
            return synced
        finally:
            await api.close()

    def _format_url(self, tid: int, folder: str) -> str:
        """Format TID as Yandex Mail thread URL.

        Args:
            tid: Thread ID (or MID for single messages)
            folder: Folder name (for folder_id lookup)

        Returns:
            Full URL to thread/message
        """
        folder_id = self.config.folder_ids.get(folder, 1)
        return f"https://{self.config.url_prefix}/touch/folder/{folder_id}/thread/{tid}"

    def set_reference(self, folder: str, uid: int, mid: int) -> None:
        """Manually set a reference point for a folder.

        Args:
            folder: IMAP folder name
            uid: Known IMAP UID
            mid: Known MID for that UID
        """
        decoded_folder = decode_imap_utf7(folder)
        cache.set_folder_reference(self.account, decoded_folder, uid, mid)
        logger.info(f"Set reference for {self.account}/{decoded_folder}: uid={uid}, mid={mid}")

    def get_all_references(self) -> dict[str, tuple[int, int]]:
        """Get all folder references for this account.

        Returns:
            Dict mapping folder -> (ref_uid, ref_mid)
        """
        return cache.get_all_folder_references(self.account)
