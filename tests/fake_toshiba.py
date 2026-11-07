"""A local stand-in for the Toshiba cloud, which counts every request it receives."""

from __future__ import annotations

import base64
from collections import Counter
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
import json
import time
from typing import Any
from unittest.mock import patch

from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestServer

UNIT_ID = "11111111-1111-1111-1111-111111111111"
UNIT_STATE = "3144114131640010171bfe0b00001002000000"


# ---------------------------
#   make_jwt
# ---------------------------
def make_jwt(lifetime_s: float = 730 * 86400) -> str:
    """Return a token shaped like Toshiba's, which expires after the given time."""

    def part(value: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    claims = {"exp": time.time() + lifetime_s}
    return f"{part({'alg': 'none'})}.{part(claims)}.signature"


# ---------------------------
#   FakeToshiba
# ---------------------------
class FakeToshiba:
    """Answer as Toshiba does, unless a test sets a status for an endpoint."""

    def __init__(self) -> None:
        """Start with nothing received."""
        self.requests: Counter[str] = Counter()
        self.statuses: dict[str, int] = {}
        self.headers: dict[str, str] = {}
        self.commands: list[dict[str, Any]] = []
        self.command_answer: dict[str, Any] = {"Status": "Success"}
        self.passwords: list[str] = []
        self.good_password = "secret"
        self.token = make_jwt()
        self.units: list[dict[str, Any]] = [self.unit()]
        self.sockets: list[web.WebSocketResponse] = []
        self.refuse_sockets = False
        self.base_url = ""

    @staticmethod
    def unit(model: str = "2", merit: str = "2c00") -> dict[str, Any]:
        """Return a unit as the mapping endpoint lists it."""
        return {
            "Id": "ac-id",
            "DeviceUniqueId": UNIT_ID,
            "Name": "Living room",
            "ACStateData": UNIT_STATE,
            "FirmwareVersion": "5.0.00",
            "MeritFeature": merit,
            "ACModelId": model,
        }

    @property
    def total(self) -> int:
        """Return the number of requests of all kinds, sockets excluded."""
        return sum(self.requests.values())

    def _refusal(self, name: str) -> web.Response | None:
        """Count a request, and return the configured refusal."""
        self.requests[name] += 1
        if status := self.statuses.get(name):
            return web.Response(status=status, headers=self.headers)
        return None

    @staticmethod
    def _answer(result: Any) -> web.Response:
        """Return Toshiba's envelope."""
        return web.json_response(
            {"IsSuccess": True, "StatusCode": "Success", "ResObj": result}
        )

    async def login(self, request: web.Request) -> web.Response:
        """Handle a login."""
        if refusal := self._refusal("login"):
            return refusal
        body = await request.json()
        self.passwords.append(body["Password"])
        if body["Password"] != self.good_password:
            return web.json_response(
                {
                    "IsSuccess": False,
                    "StatusCode": "Failed",
                    "Message": "Wrong password",
                }
            )
        return self._answer(
            {
                "access_token": self.token,
                "token_type": "Bearer",
                "consumerId": "consumer-1",
            }
        )

    async def mapping(self, _request: web.Request) -> web.Response:
        """Handle the list of units."""
        return self._refusal("mapping") or self._answer([{"ACList": self.units}])

    async def channel(self, _request: web.Request) -> web.Response:
        """Hand out a socket address with Toshiba's date format."""
        if refusal := self._refusal("channel"):
            return refusal
        expires = datetime.now(UTC) + timedelta(minutes=15)
        return self._answer(
            {
                "Url": self.base_url.replace("http", "ws") + "/socket?token=secret-url",
                "ExpiresAt": expires.isoformat().replace("+00:00", "1+00:00"),
                "Groups": [UNIT_ID],
            }
        )

    async def command(self, request: web.Request) -> web.Response:
        """Handle a command."""
        if refusal := self._refusal("command"):
            return refusal
        self.commands.append(await request.json())
        return self._answer(self.command_answer)

    async def energy(self, _request: web.Request) -> web.Response:
        """Handle the energy of a year."""
        return self._refusal("energy") or self._answer(
            [
                {
                    "ACDeviceUniqueId": UNIT_ID,
                    "EnergyConsumption": [{"Energy": "1500"}, {"Energy": "500"}],
                }
            ]
        )

    async def socket(self, request: web.Request) -> web.StreamResponse:
        """Hold a realtime socket."""
        if self.refuse_sockets:
            return web.Response(status=401)
        socket = web.WebSocketResponse(protocols=("json.webpubsub.azure.v1",))
        await socket.prepare(request)
        self.sockets.append(socket)
        await socket.send_json(
            {"type": "system", "event": "connected", "connectionId": "c"}
        )
        async for message in socket:
            if message.type is WSMsgType.ERROR:
                break
        return socket

    async def publish(self, cmd: str, payload: dict[str, Any]) -> None:
        """Send a message of the unit, shaped as captured from the real channel."""
        await self.sockets[-1].send_json(
            {
                "type": "message",
                "from": "group",
                "fromUserId": None,
                "group": UNIT_ID,
                "dataType": "json",
                "data": {
                    "sourceId": UNIT_ID,
                    "messageId": "0000000117",
                    "cmd": cmd,
                    "timeStamp": "006aef17ba",
                    "payload": payload,
                },
            }
        )


# ---------------------------
#   run_fake_toshiba
# ---------------------------
@asynccontextmanager
async def run_fake_toshiba() -> AsyncIterator[FakeToshiba]:
    """Serve a FakeToshiba on a local port, with the client pointed at it."""
    fake = FakeToshiba()
    app = web.Application()
    app.router.add_post("/api/Consumer/Login", fake.login)
    app.router.add_get("/api/AC/GetConsumerACMapping", fake.mapping)
    app.router.add_post("/api/RealTime/GetWebPubSubToken", fake.channel)
    app.router.add_post("/api/IoTHub/SendCommand", fake.command)
    app.router.add_post("/api/AC/GetGroupACEnergyConsumption", fake.energy)
    app.router.add_get("/socket", fake.socket)
    server = TestServer(app)
    await server.start_server()
    fake.base_url = str(server.make_url("")).rstrip("/")
    try:
        with (
            patch(
                "custom_components.toshiba_ac_link.toshiba.api.BASE_URL",
                fake.base_url,
            ),
            patch(
                "custom_components.toshiba_ac_link.toshiba.api.REQUEST_SPACING_S",
                0,
            ),
        ):
            yield fake
    finally:
        for socket in fake.sockets:
            await socket.close()
        await server.close()
