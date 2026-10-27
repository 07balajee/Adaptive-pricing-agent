"""Thin MCP client wrappers so callers don't deal with session/stdio boilerplate.

SignalsClient (retail) spawns its MCP server as a subprocess over stdio per call.
TaxiSignalsClient opens one subprocess + session per quote and reuses it for every tool call.
"""
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


class _StdioMCPClient:
    server_module: str

    def __init__(self):
        self._params = StdioServerParameters(
            command=sys.executable,
            args=["-m", self.server_module],
            cwd=str(_PROJECT_ROOT),
        )

    async def _call_tool(self, tool_name: str, arguments: dict):
        async with stdio_client(self._params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
        # Raise outside the stdio context: inside it, errors get wrapped in an ExceptionGroup
        if result.isError:
            raise RuntimeError(result.content[0].text if result.content else f"MCP tool '{tool_name}' failed")
        return result.content[0].text


class SignalsClient(_StdioMCPClient):
    server_module = "mcp_server.signals_server"

    async def get_weather(self, city: str, api_key: str) -> str:
        return await self._call_tool("get_weather", {"city": city, "api_key": api_key})

    async def get_vendor_price(
        self,
        base_price: float,
        inventory_stock: int,
        competitor_price: float,
        expiry_date: str,
    ) -> float:
        result = await self._call_tool(
            "get_vendor_price",
            {
                "base_price": base_price,
                "inventory_stock": inventory_stock,
                "competitor_price": competitor_price,
                "expiry_date": expiry_date,
            },
        )
        return float(result)


class TaxiSignals:
    """Taxi tool calls bound to one open MCP session (see TaxiSignalsClient.connect)."""

    def __init__(self, session: ClientSession):
        self._session = session

    async def _call(self, tool_name: str, arguments: dict) -> dict:
        result = await self._session.call_tool(tool_name, arguments)
        if result.isError:
            raise RuntimeError(result.content[0].text if result.content else f"MCP tool '{tool_name}' failed")
        return json.loads(result.content[0].text)

    async def get_route(self, pickup: str, drop: str, pickup_point: dict | None = None, drop_point: dict | None = None) -> dict:
        return await self._call("get_route", {"pickup": pickup, "drop": drop, "pickup_point": pickup_point, "drop_point": drop_point})

    async def get_weather_conditions(self, lat: float, lon: float, api_key: str) -> dict:
        return await self._call("get_weather_conditions", {"lat": lat, "lon": lon, "api_key": api_key})

    async def compute_taxi_fare(self, **fare_inputs) -> dict:
        return await self._call("compute_taxi_fare", fare_inputs)


class TaxiSignalsClient(_StdioMCPClient):
    """Unlike SignalsClient, keeps one server process + session open for a whole quote, since a
    quote makes several tool calls and spawning the server costs ~1.5s each time.

        async with TaxiSignalsClient().connect() as signals:
            route = await signals.get_route(...)
    """
    server_module = "mcp_server.taxi_signals_server"

    @asynccontextmanager
    async def connect(self):
        async with stdio_client(self._params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield TaxiSignals(session)


def root_error(exc: BaseException) -> BaseException:
    """Errors raised inside an open MCP session arrive wrapped in ExceptionGroups; dig out the real one."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc
