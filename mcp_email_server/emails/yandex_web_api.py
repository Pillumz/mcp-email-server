"""Yandex Mail Web API client for fetching message IDs (mid).

Uses the Yandex Mail touch interface API to fetch actual message IDs
that can be used in web URLs.
"""

from __future__ import annotations

import http.cookiejar
import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiohttp

from mcp_email_server.log import logger

if TYPE_CHECKING:
    from mcp_email_server.config import YandexLinkConfig


class YandexWebAPI:
    """Client for Yandex Mail Web API.

    Uses session cookies to authenticate with the Yandex Mail web interface
    and fetch message data including the actual message IDs (mid).
    """

    def __init__(self, config: "YandexLinkConfig"):
        self.config = config
        self._cookies: dict[str, str] = {}
        self._ckey: str | None = None
        self._session: aiohttp.ClientSession | None = None

        if config.cookies_file:
            self._load_cookies(config.cookies_file)

    def _load_cookies(self, cookies_file: str) -> None:
        """Load cookies from Netscape cookies.txt file."""
        path = Path(cookies_file).expanduser().resolve()
        if not path.exists():
            logger.warning(f"Cookies file not found: {path}")
            return

        # Parse Netscape cookies.txt format
        jar = http.cookiejar.MozillaCookieJar(str(path))
        try:
            jar.load(ignore_discard=True, ignore_expires=True)
            for cookie in jar:
                self._cookies[cookie.name] = cookie.value
            logger.info(f"Loaded {len(self._cookies)} cookies from {path}")
        except Exception as e:
            logger.error(f"Failed to load cookies: {e}")

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session with cookies."""
        if self._session is None or self._session.closed:
            # Convert cookies to aiohttp format
            jar = aiohttp.CookieJar()
            self._session = aiohttp.ClientSession(
                cookie_jar=jar,
                headers={
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
                    "Accept": "application/json",
                }
            )
            # Add cookies to session
            for name, value in self._cookies.items():
                self._session.cookie_jar.update_cookies(
                    {name: value},
                    response_url=aiohttp.client.URL(f"https://{self.config.url_prefix}/")
                )
        return self._session

    async def _fetch_ckey(self) -> str | None:
        """Fetch the session key (ckey/sk) from the page config."""
        if self._ckey:
            return self._ckey

        session = await self._get_session()
        try:
            async with session.get(
                f"https://{self.config.url_prefix}/touch/folder/1",
            ) as resp:
                if resp.status != 200:
                    logger.error(f"Failed to fetch page: {resp.status}")
                    return None

                html = await resp.text()

                # Extract qu-json-config
                import re
                match = re.search(r'id="qu-json-config"[^>]*>([^<]+)', html)
                if match:
                    try:
                        config_data = json.loads(match.group(1))
                        self._ckey = config_data.get("sk")
                        logger.debug(f"Got ckey: {self._ckey[:10]}...")
                        return self._ckey
                    except json.JSONDecodeError:
                        logger.error("Failed to parse page config JSON")
        except Exception as e:
            logger.error(f"Failed to fetch ckey: {e}")

        return None

    async def fetch_messages(
        self,
        folder_id: int = 1,
        first: int = 0,
        count: int = 50,
    ) -> list[dict[str, Any]]:
        """Fetch messages from a folder via Web API.

        Args:
            folder_id: Yandex folder ID (1=INBOX, 2=Spam, etc.)
            first: Offset for pagination
            count: Number of messages to fetch

        Returns:
            List of message dicts with mid, subject, date, etc.
        """
        ckey = await self._fetch_ckey()
        if not ckey:
            logger.error("No ckey available, cannot fetch messages")
            return []

        session = await self._get_session()
        try:
            async with session.post(
                f"https://{self.config.url_prefix}/touch/api/models",
                json={
                    "models": [{
                        "name": "messages",
                        "params": {
                            "fid": str(folder_id),
                            "first": first,
                            "count": count,
                        }
                    }],
                    "_ckey": ckey,
                },
            ) as resp:
                if resp.status != 200:
                    logger.error(f"API request failed: {resp.status}")
                    return []

                data = await resp.json()

                if not data.get("models"):
                    logger.warning("No models in response")
                    return []

                model = data["models"][0]
                if model.get("status") != "ok":
                    logger.error(f"API error: {model.get('error', 'unknown')}")
                    return []

                messages = model.get("data", {}).get("messages", [])
                logger.info(f"Fetched {len(messages)} messages from folder {folder_id}")
                return messages

        except Exception as e:
            logger.error(f"Failed to fetch messages: {e}")
            return []

    async def fetch_all_folders_messages(
        self,
        count_per_folder: int = 50,
    ) -> dict[int, list[dict[str, Any]]]:
        """Fetch messages from all configured folders.

        Args:
            count_per_folder: Number of messages to fetch per folder

        Returns:
            Dict mapping folder_id -> list of messages
        """
        results = {}
        for folder_name, folder_id in self.config.folder_ids.items():
            messages = await self.fetch_messages(
                folder_id=folder_id,
                count=count_per_folder,
            )
            results[folder_id] = messages
            logger.debug(f"Folder {folder_name} ({folder_id}): {len(messages)} messages")

        return results

    async def fetch_folders(self) -> list[dict[str, Any]]:
        """Fetch all folders from Web API.

        Returns:
            List of folder dicts with fid, name, count, etc.
        """
        ckey = await self._fetch_ckey()
        if not ckey:
            logger.error("No ckey available, cannot fetch folders")
            return []

        session = await self._get_session()
        try:
            async with session.post(
                f"https://{self.config.url_prefix}/touch/api/models",
                json={
                    "models": [{"name": "folders"}],
                    "_ckey": ckey,
                },
            ) as resp:
                if resp.status != 200:
                    logger.error(f"API request failed: {resp.status}")
                    return []

                data = await resp.json()

                if not data.get("models"):
                    return []

                model = data["models"][0]
                if model.get("status") != "ok":
                    logger.error(f"API error: {model.get('error', 'unknown')}")
                    return []

                folders = model.get("data", {}).get("folders", [])
                logger.info(f"Fetched {len(folders)} folders")
                return folders

        except Exception as e:
            logger.error(f"Failed to fetch folders: {e}")
            return []

    async def sync_mids_to_cache(
        self,
        account: str,
        imap_messages_by_folder: dict[str, list[dict[str, Any]]],
        count_per_folder: int = 50,
    ) -> int:
        """Sync MIDs from Web API to cache by matching with IMAP messages.

        Args:
            account: Account name
            imap_messages_by_folder: Dict mapping IMAP folder -> list of message dicts
                                     Each dict has: uid, subject, date
            count_per_folder: Number of messages to fetch per folder

        Returns:
            Number of MIDs synced to cache
        """
        from mcp_email_server import cache

        # First fetch folders to get all fids
        folders = await self.fetch_folders()
        if not folders:
            logger.warning("No folders fetched, cannot sync MIDs")
            return 0

        # Build fid -> folder_name mapping
        fid_to_name = {f["fid"]: f["name"] for f in folders}
        name_to_fid = {f["name"]: f["fid"] for f in folders}

        # Also map IMAP folder names to web folder names
        imap_to_web_folder = {
            "INBOX": "Inbox",
            "Отправленные": "Sent",
            "Спам": "Spam",
            "Удаленные": "Trash",
            "Черновики": "Drafts",
            "Архив": "Archive",
        }

        total_synced = 0
        messages_to_cache = []

        for imap_folder, imap_messages in imap_messages_by_folder.items():
            if not imap_messages:
                continue

            # Find the web folder fid
            web_folder_name = imap_to_web_folder.get(imap_folder, imap_folder)
            fid = name_to_fid.get(web_folder_name)

            # Try to find fid from config
            if fid is None:
                fid = self.config.folder_ids.get(imap_folder)

            if fid is None:
                logger.debug(f"No web folder fid for IMAP folder {imap_folder}, skipping")
                continue

            # Fetch web messages for this folder
            web_messages = await self.fetch_messages(
                folder_id=int(fid),
                count=count_per_folder,
            )

            if not web_messages:
                continue

            # Match IMAP to web messages
            uid_to_ids = match_imap_to_web(imap_messages, web_messages)

            # Prepare cache entries
            for imap_msg in imap_messages:
                uid = imap_msg.get("uid")
                if uid in uid_to_ids:
                    mid, tid = uid_to_ids[uid]
                    date = imap_msg.get("date")
                    internal_date = date.isoformat() if isinstance(date, datetime) else str(date)

                    messages_to_cache.append({
                        "folder": imap_folder,
                        "uid": uid,
                        "internal_date": internal_date,
                        "mid": mid,
                        "tid": tid,
                    })

            # Update folder reference with newest message
            if uid_to_ids:
                # Get the highest UID that was matched
                max_uid = max(uid_to_ids.keys())
                max_mid, _ = uid_to_ids[max_uid]
                cache.set_folder_reference(account, imap_folder, max_uid, max_mid)
                logger.debug(f"Set reference for {imap_folder}: uid={max_uid}, mid={max_mid}")

            total_synced += len(uid_to_ids)

        # Bulk insert to cache
        if messages_to_cache:
            cache.bulk_insert_messages(account, messages_to_cache)
            logger.info(f"Synced {total_synced} MIDs to cache for {account}")

        return total_synced

    async def close(self) -> None:
        """Close the aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()


def match_imap_to_web(
    imap_messages: list[dict[str, Any]],
    web_messages: list[dict[str, Any]],
) -> dict[int, tuple[int, int]]:
    """Match IMAP UIDs to Web MIDs and TIDs based on subject and date.

    Args:
        imap_messages: List of dicts with uid, subject, date
        web_messages: List of dicts from Yandex Web API with mid, tidRaw, subject, date

    Returns:
        Dict mapping IMAP UID -> (mid, tid) tuple
    """
    import re

    uid_to_ids: dict[int, tuple[int, int]] = {}

    # Build index of web messages by normalized subject
    web_by_subject: dict[str, list[dict]] = {}
    for msg in web_messages:
        subject = msg.get("subject", "").strip()
        # Normalize: remove Re:/Fwd: prefixes
        norm_subject = re.sub(r'^(Re:\s*|Fwd:\s*)+', '', subject, flags=re.I).strip().lower()
        if norm_subject not in web_by_subject:
            web_by_subject[norm_subject] = []
        web_by_subject[norm_subject].append(msg)

    for imap_msg in imap_messages:
        uid = imap_msg.get("uid")
        subject = imap_msg.get("subject", "").strip()
        norm_subject = re.sub(r'^(Re:\s*|Fwd:\s*)+', '', subject, flags=re.I).strip().lower()
        imap_date = imap_msg.get("date")

        # Find matching web messages by normalized subject
        candidates = web_by_subject.get(norm_subject, [])

        matched_msg = None
        if len(candidates) == 1:
            # Unique match by subject
            matched_msg = candidates[0]
        elif len(candidates) > 1:
            # Multiple matches - try to match by date
            if isinstance(imap_date, datetime):
                imap_ts = imap_date.timestamp() * 1000

                best_match = None
                best_diff = float("inf")

                for web_msg in candidates:
                    web_ts = web_msg.get("date", {}).get("timestamp", 0)
                    diff = abs(web_ts - imap_ts)
                    if diff < best_diff:
                        best_diff = diff
                        best_match = web_msg

                if best_match and best_diff < 120000:  # Within 2 minutes
                    matched_msg = best_match

        if matched_msg:
            mid = int(matched_msg["mid"])
            # tidRaw is the thread ID without 't' prefix, fallback to mid if not present
            tid_raw = matched_msg.get("tidRaw")
            tid = int(tid_raw) if tid_raw else mid
            uid_to_ids[uid] = (mid, tid)

    return uid_to_ids
