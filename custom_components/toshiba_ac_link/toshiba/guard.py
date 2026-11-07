"""Limits that protect a Toshiba account, kept on disk to survive restarts.

Toshiba's real limits are unknown. These are deliberately far below normal use
would need, and hold even if Home Assistant restarts in a loop, because nothing
here lives only in memory. A request is counted, and saved, before it is sent.
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import time
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util.hass_dict import HassKey

from .errors import ToshibaBlockedError, ToshibaLoginLimitError

STORAGE_VERSION = 1
STORAGE_KEY = "toshiba_ac_link.guard"

# Normal use is a few requests per hour plus the commands that the user sends
HOURLY_REQUEST_BUDGET = 120
HOUR_S = 3600

# After a 403 or a 429: 15 minutes, doubling up to one day
BLOCK_BASE_S = 15 * 60
BLOCK_MAX_S = 24 * 3600
BLOCK_JITTER = 0.2

# Toshiba counts failed logins toward an account lockout
MAX_FAILED_LOGINS = 3
FAILED_LOGIN_WINDOW_S = 3600

MAX_REJECTED_TOKENS = 20

_RANDOM = secrets.SystemRandom()

_DATA_GUARD: HassKey[ToshibaGuard] = HassKey("toshiba_ac_link_guard")
_DATA_GUARD_LOCK: HassKey[asyncio.Lock] = HassKey("toshiba_ac_link_guard_lock")


# ---------------------------
#   jitter
# ---------------------------
def jitter(low: float, high: float) -> float:
    """Return a random wait, so that many clients do not retry at the same moment."""
    return _RANDOM.uniform(low, high)


# ---------------------------
#   _digest
# ---------------------------
def _digest(value: str) -> str:
    """Return a value that identifies a secret without revealing it."""
    return hashlib.sha256(value.encode()).hexdigest()


# ---------------------------
#   ToshibaGuard
# ---------------------------
class ToshibaGuard:
    """Decide whether a request may be sent, and remember what happened."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Prepare the storage, which async_load reads."""
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, STORAGE_KEY, private=True
        )
        self._data: dict[str, Any] = {}

    async def async_load(self) -> None:
        """Read what earlier runs saved."""
        self._data = await self._store.async_load() or {}

    async def _async_save(self) -> None:
        """Write now. A delayed save would lose what a crash needs to remember."""
        await self._store.async_save(self._data)

    # Requests and blocks

    async def async_before_request(self) -> None:
        """Count one request, or raise if it must not be sent."""
        now = time.time()
        # The limit guards against a clock that was wrong when this was saved
        remaining = min(self._data.get("blocked_until", 0) - now, BLOCK_MAX_S * 2)
        if remaining > 0:
            raise ToshibaBlockedError("Toshiba asked to slow down", remaining)

        started = self._data.get("hour_started", 0)
        if not 0 <= now - started < HOUR_S:
            started = now
            self._data["hour_started"] = now
            self._data["hour_requests"] = 0
        if self._data.get("hour_requests", 0) >= HOURLY_REQUEST_BUDGET:
            raise ToshibaBlockedError(
                "The hourly request budget is used up",
                started + HOUR_S - now + jitter(0, 60),
            )

        self._data["hour_requests"] = self._data.get("hour_requests", 0) + 1
        await self._async_save()

    async def async_record_block(self, retry_after: float | None = None) -> float:
        """Stop all requests after a 403 or a 429, and return for how many seconds."""
        count = self._data.get("block_count", 0) + 1
        wait: float = min(BLOCK_BASE_S * 2 ** min(count - 1, 16), BLOCK_MAX_S)
        wait *= 1 + jitter(0, BLOCK_JITTER)
        if retry_after is not None:
            wait = max(wait, min(retry_after, BLOCK_MAX_S))
        self._data["block_count"] = count
        self._data["blocked_until"] = time.time() + wait
        await self._async_save()
        return wait

    async def async_record_success(self) -> None:
        """Forget earlier blocks after an accepted request."""
        if self._data.get("block_count"):
            self._data["block_count"] = 0
            await self._async_save()

    # Logins, which only the user starts

    def check_login(self, username: str) -> None:
        """Raise if another login could bring the account closer to a lockout."""
        failures = self._recent_failures(username)
        if len(failures) >= MAX_FAILED_LOGINS:
            raise ToshibaLoginLimitError(
                "Too many failed logins",
                failures[0] + FAILED_LOGIN_WINDOW_S - time.time(),
            )

    def _recent_failures(self, username: str) -> list[float]:
        """Return the times of the account's recent failed logins, oldest first."""
        failed: dict[str, list[float]] = self._data.get("failed_logins", {})
        now = time.time()
        return [
            moment
            for moment in failed.get(_digest(username.lower()), [])
            if 0 <= now - moment < FAILED_LOGIN_WINDOW_S
        ]

    async def async_record_login(self, username: str, *, success: bool) -> None:
        """Remember the result of a login attempt."""
        failed = self._data.setdefault("failed_logins", {})
        key = _digest(username.lower())
        if success:
            failed.pop(key, None)
        else:
            failed[key] = [*self._recent_failures(username), time.time()]
        await self._async_save()

    # Tokens

    def is_token_rejected(self, token: str) -> bool:
        """Return whether Toshiba already refused this token."""
        return _digest(token) in self._data.get("rejected_tokens", [])

    async def async_reject_token(self, token: str) -> None:
        """Never send this token again, not even after a restart."""
        rejected = self._data.setdefault("rejected_tokens", [])
        if (digest := _digest(token)) not in rejected:
            rejected.append(digest)
            del rejected[:-MAX_REJECTED_TOKENS]
            await self._async_save()

    async def async_accept_token(self, token: str) -> None:
        """Clear a rejection, in case a new login returns the same token."""
        rejected = self._data.get("rejected_tokens", [])
        if (digest := _digest(token)) in rejected:
            rejected.remove(digest)
            await self._async_save()

    # Answers that a restart can reuse

    def cached(self, kind: str, account: str) -> dict[str, Any] | None:
        """Return a saved answer of this account."""
        cached: dict[str, Any] | None = (
            self._data.get("cache", {}).get(kind, {}).get(account)
        )
        return cached

    async def async_cache(self, kind: str, account: str, value: dict[str, Any]) -> None:
        """Save an answer of this account."""
        self._data.setdefault("cache", {}).setdefault(kind, {})[account] = value
        await self._async_save()

    def diagnostics(self) -> dict[str, Any]:
        """Return the counters, which hold no secret."""
        now = time.time()
        return {
            "blocked_for_s": max(0, round(self._data.get("blocked_until", 0) - now)),
            "block_count": self._data.get("block_count", 0),
            "requests_this_hour": self._data.get("hour_requests", 0),
            "hourly_budget": HOURLY_REQUEST_BUDGET,
        }


# ---------------------------
#   async_get_guard
# ---------------------------
async def async_get_guard(hass: HomeAssistant) -> ToshibaGuard:
    """Return the one guard of this Home Assistant.

    It is shared by all accounts, since the WAF may count per address too.
    """
    lock = hass.data.setdefault(_DATA_GUARD_LOCK, asyncio.Lock())
    async with lock:
        if _DATA_GUARD not in hass.data:
            guard = ToshibaGuard(hass)
            await guard.async_load()
            hass.data[_DATA_GUARD] = guard
    return hass.data[_DATA_GUARD]
