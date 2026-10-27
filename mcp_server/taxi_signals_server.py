"""MCP server exposing taxi trip signals and the fare engine as tools.

Run standalone for manual testing:
    mcp dev mcp_server/taxi_signals_server.py

Run for real (stdio transport, spawned by TaxiSignalsClient):
    python -m mcp_server.taxi_signals_server
"""
from mcp.server.fastmcp import FastMCP

from agents import route_collector, signal_collector
from adaptors import taxi_adaptors

mcp = FastMCP("taxi-signals")


@mcp.tool()
def get_route(pickup: str, drop: str, pickup_point: dict | None = None, drop_point: dict | None = None) -> dict:
    """Geocode pickup/drop and return driving distance (km), duration (min) and the route path.
    pickup_point/drop_point ({"lat", "lon", "name"}) skip geocoding when the caller already has coordinates."""
    return route_collector.get_route(pickup, drop, pickup_point, drop_point)


@mcp.tool()
def get_weather_conditions(lat: float, lon: float, api_key: str) -> dict:
    """Current weather at a coordinate, including whether it is raining and how hard."""
    return signal_collector.get_weather_conditions(lat, lon, api_key)


@mcp.tool()
def compute_taxi_fare(
    distance_km: float,
    duration_min: float,
    competitor_fares: list[dict],
    taxi_supply: str,
    is_raining: bool,
    rain_intensity: str,
    hour: int,
    vehicle_type: str,
    fuel_price: float,
    mileage_kmpl: float,
    min_profit: float,
    return_empty: bool,
    tolls_parking: float,
    history: dict | None = None,
) -> dict:
    """Rule-based fare that beats the cheapest competitor without going below the driver's cost floor.
    competitor_fares is a list of {"name": str, "fare": float}. taxi_supply is very_few | some | plenty.
    history is the driver's recent {"decided", "won", "lost"} outcome counts, used to tune the undercut."""
    return taxi_adaptors.compute_taxi_fare(
        distance_km=distance_km,
        duration_min=duration_min,
        competitor_fares=competitor_fares,
        taxi_supply=taxi_supply,
        is_raining=is_raining,
        rain_intensity=rain_intensity,
        hour=hour,
        vehicle_type=vehicle_type,
        fuel_price=fuel_price,
        mileage_kmpl=mileage_kmpl,
        min_profit=min_profit,
        return_empty=return_empty,
        tolls_parking=tolls_parking,
        history=history,
    )


if __name__ == "__main__":
    mcp.run()
