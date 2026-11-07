"""HTTPS requests to the Toshiba cloud. Each call makes one request, and never retries."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from datetime import datetime
import json
import logging
import time
from typing import Any

import aiohttp

from .errors import (
    ToshibaAuthError,
    ToshibaBlockedError,
    ToshibaCommandError,
    ToshibaConnectionError,
)
from .guard import ToshibaGuard

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://mobileapi.toshibahomeaccontrols.com"
LOGIN_PATH = "/api/Consumer/Login"
MAPPING_PATH = "/api/AC/GetConsumerACMapping"
CHANNEL_PATH = "/api/RealTime/GetWebPubSubToken"
COMMAND_PATH = "/api/IoTHub/SendCommand"
ENERGY_PATH = "/api/AC/GetGroupACEnergyConsumption"

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30, connect=10)
REQUEST_SPACING_S = 0.25

# The app's WAF rules reject a browser-like User-Agent on some endpoints
BASE_HEADERS = {"Content-Type": "application/json", "User-Agent": ""}

# The WAF answers with 403, and the rate limiter with 429
BLOCK_STATUSES = frozenset({403, 429})


# ---------------------------
#   LoginResult
# ---------------------------
@dataclass(frozen=True)
class LoginResult:
    """What a login gives."""

    access_token: str
    token_type: str
    consumer_id: str


# ---------------------------
#   UnitInfo
# ---------------------------
@dataclass(frozen=True)
class UnitInfo:
    """One air conditioner of the account."""

    ac_id: str
    ac_unique_id: str
    name: str
    state: str
    firmware_version: str
    merit_feature: str
    ac_model_id: str


# ---------------------------
#   token_expiry
# ---------------------------
def token_expiry(token: str) -> float | None:
    """Return when an access token expires, or None if it does not say.

    The token is a JWT. Reading its payload needs no key, and nothing here
    relies on the signature: Toshiba checks that.
    """
    try:
        payload = token.split(".")[1]
        claims = json.loads(
            base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        )
        return float(claims["exp"])
    except (IndexError, KeyError, TypeError, ValueError):
        return None


# ---------------------------
#   parse_units
# ---------------------------
def parse_units(groups: list[dict[str, Any]]) -> tuple[list[UnitInfo], int]:
    """Return the units in an answer of the mapping endpoint, and how many were skipped.

    One unit with unexpected data must not cost the others.
    """
    units: list[UnitInfo] = []
    skipped = 0
    for group in groups:
        try:
            group_units = list(group["ACList"])
        except (KeyError, TypeError):
            skipped += 1
            continue
        for unit in group_units:
            try:
                if not isinstance(unit["DeviceUniqueId"], str):
                    raise TypeError
                units.append(
                    UnitInfo(
                        unit["Id"],
                        unit["DeviceUniqueId"],
                        unit["Name"],
                        unit["ACStateData"],
                        unit["FirmwareVersion"],
                        unit["MeritFeature"],
                        unit["ACModelId"],
                    )
                )
            except (KeyError, TypeError):
                skipped += 1
    return units, skipped


# ---------------------------
#   _async_send
# ---------------------------
async def _async_send(
    session: aiohttp.ClientSession,
    method: str,
    path: str,
    headers: dict[str, str],
    **kwargs: Any,
) -> tuple[int, str, str | None]:
    """Make the request, and return status, body and Retry-After."""
    try:
        async with session.request(
            method,
            BASE_URL + path,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
            **kwargs,
        ) as response:
            return (
                response.status,
                await response.text(),
                response.headers.get("Retry-After"),
            )
    except (aiohttp.ClientError, TimeoutError) as ex:
        # The exception can hold the URL, and no secret, but it can be long
        raise ToshibaConnectionError(f"{path}: {type(ex).__name__}") from ex


# ---------------------------
#   _parse_retry_after
# ---------------------------
def _parse_retry_after(value: str | None) -> float | None:
    """Return seconds from a Retry-After header. Its date form is not worth handling."""
    try:
        return float(value) if value else None
    except ValueError:
        return None


# ---------------------------
#   _parse_body
# ---------------------------
def _parse_body(path: str, text: str) -> dict[str, Any]:
    """Return the JSON object of an answer."""
    try:
        body = json.loads(text)
    except ValueError as ex:
        raise ToshibaConnectionError(f"{path}: the answer is not JSON") from ex
    if not isinstance(body, dict):
        raise ToshibaConnectionError(f"{path}: unexpected answer")
    return body


# ---------------------------
#   async_login
# ---------------------------
async def async_login(
    session: aiohttp.ClientSession,
    guard: ToshibaGuard,
    username: str,
    password: str,
    device_id: str,
) -> LoginResult:
    """Log in once. Only the form the user sends calls this, nothing automatic does."""
    guard.check_login(username)
    await guard.async_before_request()
    status, text, retry_after = await _async_send(
        session,
        "POST",
        LOGIN_PATH,
        {**BASE_HEADERS, "Device-ID": device_id},
        json={"Username": username, "Password": password},
    )

    if status in BLOCK_STATUSES:
        wait = await guard.async_record_block(_parse_retry_after(retry_after))
        raise ToshibaBlockedError(f"Toshiba answered {status} to the login", wait)

    if status == 401:
        await guard.async_record_login(username, success=False)
        raise ToshibaAuthError("Toshiba rejected the login")

    if status != 200:
        # Any other refusal may count toward a lockout too. A 5xx is Toshiba's own
        # failure, and 403 and 429 were handled as blocks above.
        if 400 <= status < 500:
            await guard.async_record_login(username, success=False)
        raise ToshibaConnectionError(f"{LOGIN_PATH}: HTTP {status}")

    body = _parse_body(LOGIN_PATH, text)
    if not body.get("IsSuccess"):
        await guard.async_record_login(username, success=False)
        message = str(body.get("Message") or "")
        # A wrong password with a valid username has another StatusCode
        if body.get("StatusCode") == "InvalidUserNameorPassword" or any(
            marker in message.lower() for marker in ("password", "lock")
        ):
            raise ToshibaAuthError("Toshiba rejected the login")
        raise ToshibaConnectionError(f"{LOGIN_PATH}: {body.get('StatusCode')}")

    try:
        data = body["ResObj"]
        result = LoginResult(
            data["access_token"], data["token_type"], data["consumerId"]
        )
    except (KeyError, TypeError) as ex:
        raise ToshibaConnectionError(f"{LOGIN_PATH}: unexpected answer") from ex

    await guard.async_record_login(username, success=True)
    await guard.async_accept_token(result.access_token)
    await guard.async_record_success()
    return result


# ---------------------------
#   ToshibaApi
# ---------------------------
class ToshibaApi:
    """Requests of an account that is logged in."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        guard: ToshibaGuard,
        device_id: str,
        login: LoginResult,
    ) -> None:
        """Store the session of the account."""
        self._session = session
        self._guard = guard
        self._login = login
        self._headers = {
            **BASE_HEADERS,
            "Device-ID": device_id,
            "Authorization": f"{login.token_type} {login.access_token}",
        }
        self._spacing = asyncio.Lock()

    def check_token(self) -> None:
        """Raise if the token cannot work, without asking Toshiba."""
        if self._guard.is_token_rejected(self._login.access_token):
            raise ToshibaAuthError("Toshiba already rejected this token")
        expiry = token_expiry(self._login.access_token)
        if expiry is not None and expiry <= time.time():
            raise ToshibaAuthError("The token expired")

    async def _async_request(self, method: str, path: str, **kwargs: Any) -> Any:
        """Make one request, and return the result object of its answer."""
        self.check_token()
        async with self._spacing:
            await self._guard.async_before_request()
            answer = await _async_send(
                self._session, method, path, self._headers, **kwargs
            )
            await asyncio.sleep(REQUEST_SPACING_S)
        status, text, retry_after = answer

        if status == 401:
            await self._guard.async_reject_token(self._login.access_token)
            raise ToshibaAuthError("Toshiba rejected the token")
        if status in BLOCK_STATUSES:
            wait = await self._guard.async_record_block(_parse_retry_after(retry_after))
            raise ToshibaBlockedError(f"Toshiba answered {status}", wait)
        if status != 200:
            raise ToshibaConnectionError(f"{path}: HTTP {status}")

        body = _parse_body(path, text)
        if not body.get("IsSuccess"):
            raise ToshibaConnectionError(f"{path}: {body.get('StatusCode')}")
        await self._guard.async_record_success()
        return body.get("ResObj")

    async def async_get_groups(self) -> list[dict[str, Any]]:
        """Return the groups of units, with the current state of each unit."""
        groups = await self._async_request(
            "GET", MAPPING_PATH, params={"consumerId": self._login.consumer_id}
        )
        if not isinstance(groups, list):
            raise ToshibaConnectionError(f"{MAPPING_PATH}: unexpected answer")
        return groups

    async def async_get_channel_url(self) -> str:
        """Return a new address for the realtime socket, good for one connection."""
        data = await self._async_request("POST", CHANNEL_PATH, json={})
        url = data.get("Url") if isinstance(data, dict) else None
        if not isinstance(url, str):
            raise ToshibaConnectionError(f"{CHANNEL_PATH}: unexpected answer")
        return url

    async def async_send_command(self, message: dict[str, Any]) -> None:
        """Hand a command to Toshiba. Sending twice would repeat it, so this never retries."""
        data = await self._async_request("POST", COMMAND_PATH, json=message)
        # Toshiba already accepted it, so an answer of another shape names no failure
        answer = data if isinstance(data, dict) else {}
        undelivered = sum(
            len(targets)
            for targets in (
                answer.get("FailedTargetIds"),
                answer.get("UnauthorizedTargetIds"),
            )
            if isinstance(targets, list)
        )
        if undelivered:
            raise ToshibaCommandError(
                f"Toshiba did not deliver the command to {undelivered} unit(s)"
            )

    async def async_get_energy(self, unit_ids: list[str]) -> dict[str, int]:
        """Return the watt-hours each unit used this year."""
        year = datetime.now().year
        answer = await self._async_request(
            "POST",
            ENERGY_PATH,
            json={
                "ACDeviceUniqueIdList": unit_ids,
                "FromUtcTime": str(year),
                "Timezone": "UTC",
                "ToUtcTime": str(year + 1),
                "Type": "EnergyYear",
            },
        )
        energy: dict[str, int] = {}
        for unit in answer or []:
            try:
                if unit["EnergyConsumption"]:
                    energy[unit["ACDeviceUniqueId"]] = sum(
                        int(item["Energy"]) for item in unit["EnergyConsumption"]
                    )
            except (KeyError, TypeError, ValueError):
                _LOGGER.debug("Skipping an unexpected energy answer")
        return energy
