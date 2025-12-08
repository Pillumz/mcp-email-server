"""IMAP connection manager with connection reuse and folder state tracking."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Awaitable, Callable, TypeVar

import aioimaplib

from mcp_email_server.log import logger

if TYPE_CHECKING:
    from mcp_email_server.config import EmailServer

T = TypeVar("T")


class IMAPConnectionManager:
    """Manages a single reusable IMAP connection with folder state tracking.

    Features:
    - Lazy connection (connects on first use)
    - Automatic reconnection on errors
    - Folder state tracking (avoids unnecessary SELECT commands)
    - Configurable timeout (default 30s)
    """

    DEFAULT_TIMEOUT = 30.0

    def __init__(
        self,
        email_server: "EmailServer",
        timeout: float | None = None,
    ):
        self.email_server = email_server
        self.timeout = timeout or self.DEFAULT_TIMEOUT
        self._imap: aioimaplib.IMAP4 | aioimaplib.IMAP4_SSL | None = None
        self._current_folder: str | None = None
        self._connected: bool = False
        self._lock = asyncio.Lock()

    @property
    def imap_class(self) -> type:
        """Return the appropriate IMAP class based on SSL setting."""
        return aioimaplib.IMAP4_SSL if self.email_server.use_ssl else aioimaplib.IMAP4

    async def _connect(self) -> None:
        """Establish connection and authenticate."""
        logger.debug(
            f"Connecting to IMAP server {self.email_server.host}:{self.email_server.port}"
        )

        self._imap = self.imap_class(
            self.email_server.host,
            self.email_server.port,
            timeout=self.timeout,
        )

        await self._imap._client_task
        await self._imap.wait_hello_from_server()
        await self._imap.login(self.email_server.user_name, self.email_server.password)

        # Try to send ID command (optional, some servers support it)
        try:
            await self._imap.id(name="mcp-email-server", version="1.0.0")
        except Exception as e:
            logger.debug(f"IMAP ID command failed (optional): {e}")

        self._connected = True
        self._current_folder = None
        logger.debug("IMAP connection established")

    async def _disconnect(self) -> None:
        """Disconnect from the server."""
        if self._imap is not None:
            try:
                await self._imap.logout()
            except Exception as e:
                logger.debug(f"Error during IMAP logout: {e}")
            finally:
                self._imap = None
                self._connected = False
                self._current_folder = None

    async def ensure_connected(self) -> aioimaplib.IMAP4 | aioimaplib.IMAP4_SSL:
        """Ensure we have an active connection, reconnecting if necessary."""
        async with self._lock:
            if not self._connected or self._imap is None:
                await self._connect()
            return self._imap  # type: ignore

    async def select_folder(self, folder: str) -> None:
        """Select a folder if not already selected."""
        imap = await self.ensure_connected()

        if self._current_folder != folder:
            from mcp_email_server.emails.yandex_links import encode_imap_utf7

            imap_folder = encode_imap_utf7(folder)
            result = await imap.select(imap_folder)
            if result.result != "OK":
                raise RuntimeError(f"Failed to select folder {folder}: {result}")
            self._current_folder = folder
            logger.debug(f"Selected folder: {folder}")

    async def execute(
        self,
        operation: Callable[[aioimaplib.IMAP4 | aioimaplib.IMAP4_SSL], Awaitable[T]],
        folder: str | None = None,
    ) -> T:
        """Execute an operation on the IMAP connection.

        Args:
            operation: Async callable that takes an IMAP connection
            folder: Optional folder to select before operation

        Returns:
            Result of the operation
        """
        imap = await self.ensure_connected()

        if folder is not None:
            await self.select_folder(folder)

        return await operation(imap)

    async def execute_with_retry(
        self,
        operation: Callable[[aioimaplib.IMAP4 | aioimaplib.IMAP4_SSL], Awaitable[T]],
        folder: str | None = None,
        max_retries: int = 2,
    ) -> T:
        """Execute an operation with automatic reconnection on failure.

        Args:
            operation: Async callable that takes an IMAP connection
            folder: Optional folder to select before operation
            max_retries: Number of retry attempts on connection failure

        Returns:
            Result of the operation
        """
        last_error: Exception | None = None

        for attempt in range(max_retries + 1):
            try:
                return await self.execute(operation, folder)

            except (
                ConnectionError,
                asyncio.TimeoutError,
                aioimaplib.AioImapException,
                OSError,  # Includes BrokenPipeError, ConnectionResetError
            ) as e:
                last_error = e
                logger.warning(f"IMAP operation failed (attempt {attempt + 1}): {e}")

                # Reset connection state for retry
                self._connected = False
                self._current_folder = None

                if attempt < max_retries:
                    logger.info("Retrying with fresh connection...")
                    await self._disconnect()
                    continue

        raise last_error  # type: ignore

    async def close(self) -> None:
        """Close the connection."""
        await self._disconnect()

    async def __aenter__(self) -> "IMAPConnectionManager":
        await self.ensure_connected()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()
