"""The realtime channel: an Azure Web PubSub socket that Toshiba's units publish to."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import asyncio
import json
import logging
import time
from typing import Any

import aiohttp

from .errors import ToshibaAuthError, ToshibaBlockedError, ToshibaError
from .guard import jitter

_LOGGER = logging.getLogger(__name__)

SUBPROTOCOL = "json.webpubsub.azure.v1"

# Sends a ping, and treats a missing answer as a lost connection. Without this,
# a silently dead socket looks the same as units that have nothing to say.
PING_S = 55

RETRY_MIN_S = 10
RETRY_MAX_S = 15 * 60

# A connection that lasted this long worked, so the next wait starts over
STABLE_S = 5 * 60

type MessageHandler = Callable[[str, dict[str, Any]], None]


# ---------------------------
#   ToshibaRealtime
# ---------------------------
class ToshibaRealtime:
    """Keep one socket open, and pass on what the units publish."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        get_url: Callable[[], Awaitable[str]],
        on_message: MessageHandler,
        on_connection: Callable[[bool], None],
    ) -> None:
        """Store what the channel needs."""
        self._session = session
        self._get_url = get_url
        self._on_message = on_message
        self._on_connection = on_connection
        self.connected = False
        self._reported_lost = False

    async def async_run(self) -> None:
        """Connect and reconnect until cancelled, or until the token is rejected."""
        wait = RETRY_MIN_S
        while True:
            started = time.monotonic()
            try:
                await self._async_connect_once()
            except ToshibaAuthError:
                raise
            except ToshibaBlockedError as ex:
                self._report_lost()
                _LOGGER.warning(
                    "Waiting %d seconds before the realtime channel: %s",
                    ex.retry_after,
                    ex,
                )
                await asyncio.sleep(ex.retry_after)
                continue
            except (aiohttp.ClientError, TimeoutError, ToshibaError) as ex:
                _LOGGER.debug("Realtime channel failed: %s", type(ex).__name__)

            self._report_lost()
            if time.monotonic() - started >= STABLE_S:
                wait = RETRY_MIN_S
            await asyncio.sleep(jitter(wait / 2, wait))
            wait = min(wait * 2, RETRY_MAX_S)

    async def _async_connect_once(self) -> None:
        """Hold one connection for as long as it lasts.

        An address works for one connection: Toshiba's service answers 401 to a second
        one, even before it expires. So each attempt asks for a new address.
        """
        url = await self._get_url()
        async with self._session.ws_connect(
            url, protocols=(SUBPROTOCOL,), heartbeat=PING_S
        ) as socket:
            self._set_connected(True)
            try:
                async for message in socket:
                    if message.type is aiohttp.WSMsgType.TEXT:
                        self._handle(message.data)
            finally:
                self._set_connected(False)
            _LOGGER.debug("Realtime channel closed, code %s", socket.close_code)

    def _set_connected(self, connected: bool) -> None:
        """Report a change of the connection."""
        if connected and self._reported_lost:
            self._reported_lost = False
            _LOGGER.info("The Toshiba realtime channel is available again")
        if connected != self.connected:
            self.connected = connected
            self._on_connection(connected)

    def _report_lost(self) -> None:
        """Log the loss once, however many attempts fail. Not called on a stop."""
        if not self._reported_lost:
            self._reported_lost = True
            _LOGGER.info(
                "The Toshiba realtime channel is not available, and it retries by itself"
            )

    def _handle(self, text: str) -> None:
        """Pass on a frame published by a unit. A bad frame must not end the channel."""
        try:
            frame = json.loads(text)
            data = frame.get("data") if frame.get("type") == "message" else None
            if isinstance(data, dict) and isinstance(data.get("sourceId"), str):
                self._on_message(data["sourceId"], data)
        except (AttributeError, ValueError):
            _LOGGER.debug("Ignoring a malformed realtime frame")
