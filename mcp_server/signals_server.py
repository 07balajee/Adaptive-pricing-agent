"""MCP server exposing pricing signals and rule-based pricing as tools.

Run standalone for manual testing:
    mcp dev mcp_server/signals_server.py

Run for real (stdio transport, spawned by SignalsClient):
    python -m mcp_server.signals_server
"""
from datetime import datetime

from mcp.server.fastmcp import FastMCP

from agents import signal_collector
from adaptors import vendor_adaptors

mcp = FastMCP("pricing-signals")


@mcp.tool()
def get_weather(city: str, api_key: str) -> str:
    """Fetch a human-readable current weather signal for a city."""
    return signal_collector.get_weather(city, api_key)


@mcp.tool()
def get_vendor_price(
    base_price: float,
    inventory_stock: int,
    competitor_price: float,
    expiry_date: str,
) -> float:
    """Compute a rule-based baseline price. expiry_date is an ISO date string (YYYY-MM-DD)."""
    parsed_expiry = datetime.strptime(expiry_date, "%Y-%m-%d").date()
    return vendor_adaptors.get_vendor_price(
        base_price=base_price,
        inventory_stock=inventory_stock,
        competitor_price=competitor_price,
        expiry_date=parsed_expiry,
    )


if __name__ == "__main__":
    mcp.run()
