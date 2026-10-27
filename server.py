"""Local web server for the pricing graph.

Run:
    uvicorn server:app --reload --port 8000
Then open:
    http://localhost:8000         taxi fare advisor (independent drivers)
    http://localhost:8000/retail  retail/market pricing form
    http://localhost:8000/docs    API docs
"""
import asyncio
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

load_dotenv()

from graph.build import build_graph  # noqa: E402 - needs env loaded first
from graph.taxi_build import build_taxi_graph  # noqa: E402
from agents import route_collector  # noqa: E402
from mcp_server.client import TaxiSignalsClient, root_error  # noqa: E402
from storage import store  # noqa: E402

app = FastAPI(title="Adaptive Pricing Agent")
_graph = build_graph()
_taxi_graph = build_taxi_graph()
_taxi_signals = TaxiSignalsClient()
_WEB = Path(__file__).parent / "web"


class PricingRequest(BaseModel):
    product_name: str = "Fresh Organic Strawberries"
    base_price: float = Field(100.0, gt=0)
    cost_price: float = Field(95.0, ge=0)
    inventory_stock: int = Field(50, ge=0)
    competitor_price: float = Field(95.0, gt=0)
    expiry_date: date = Field(default_factory=lambda: date.today() + timedelta(days=5))
    city: str = "Coimbatore"


class PricingResponse(BaseModel):
    weather_signal: str
    rule_based_price: float
    recommendation: str
    recommendation_source: str  # "groq" or "mock"
    error: str | None = None  # why the LLM path failed, when source is "mock"


@app.get("/", response_class=HTMLResponse)
def taxi_page():
    return (_WEB / "taxi.html").read_text(encoding="utf-8")


@app.get("/retail", response_class=HTMLResponse)
def retail_page():
    return (_WEB / "retail.html").read_text(encoding="utf-8")


@app.post("/price", response_model=PricingResponse)
async def price(req: PricingRequest):
    initial_state = {
        **req.model_dump(exclude={"expiry_date"}),
        "expiry_date": req.expiry_date.isoformat(),
        "weather_signal": None,
        "rule_based_price": None,
        "llm_recommendation": None,
        "recommendation_source": None,
        "error": None,
    }
    result = await _graph.ainvoke(initial_state)
    return PricingResponse(
        weather_signal=result["weather_signal"],
        rule_based_price=result["rule_based_price"],
        recommendation=result["llm_recommendation"],
        recommendation_source=result["recommendation_source"],
        error=result.get("error"),
    )


class CompetitorFare(BaseModel):
    name: str = Field(min_length=1)
    fare: float = Field(gt=0)


class PlacePoint(BaseModel):
    lat: float
    lon: float
    name: str | None = None


class TaxiQuoteRequest(BaseModel):
    pickup: str = "Coimbatore Junction railway station"
    drop: str = "Coimbatore International Airport"
    pickup_point: PlacePoint | None = None  # set when the place was picked from suggestions
    drop_point: PlacePoint | None = None
    trip_time: datetime | None = None  # local time; defaults to now
    vehicle_type: Literal["hatchback", "sedan", "suv"] = "sedan"
    competitor_fares: list[CompetitorFare] = Field(default_factory=lambda: [
        CompetitorFare(name="Red Taxi", fare=600), CompetitorFare(name="Blue Taxi", fare=580)])
    taxi_supply: Literal["very_few", "some", "plenty"] = "some"
    weather_override: Literal["auto", "clear", "rain"] = "auto"
    fuel_price: float = Field(102.0, gt=0)  # Rs per litre
    mileage_kmpl: float = Field(15.0, gt=0)
    min_profit: float = Field(150.0, ge=0)
    return_empty: bool = True
    tolls_parking: float = Field(0.0, ge=0)
    language: Literal["en", "ta"] = "en"


_RESULT_KEYS = ("route", "weather", "quote", "advice", "advice_source", "error", "history")


def _initial_taxi_state(req: TaxiQuoteRequest) -> dict:
    trip_time = req.trip_time or datetime.now()
    return {
        **req.model_dump(exclude={"trip_time"}),
        "trip_time": trip_time.strftime("%Y-%m-%d %H:%M"),
        "hour": trip_time.hour,
        "history": None,
        "route": None,
        "weather": None,
        "quote": None,
        "advice": None,
        "advice_source": None,
        "error": None,
    }


def _save_quote(req: TaxiQuoteRequest, result: dict) -> str:
    q, r = result["quote"], result["route"]
    return store.save_quote(r["pickup"]["name"], r["drop"]["name"], r["distance_km"], result["advice"]["fare"],
                            q["anchor_name"], q["anchor_fare"], q["demand_pressure"])


def _quote_error(e: BaseException) -> str:
    # Most likely an unknown place name or a map service being down
    return f"Could not price this trip: {root_error(e)}"


@app.post("/taxi/quote")
async def taxi_quote(req: TaxiQuoteRequest):
    try:
        async with _taxi_signals.connect() as signals:
            result = await _taxi_graph.ainvoke(_initial_taxi_state(req), config={"configurable": {"signals": signals}})
    except Exception as e:
        raise HTTPException(status_code=422, detail=_quote_error(e))
    out = {k: result.get(k) for k in _RESULT_KEYS}
    out["quote_id"] = await asyncio.to_thread(_save_quote, req, out)
    return out


@app.post("/taxi/quote/stream")
async def taxi_quote_stream(req: TaxiQuoteRequest):
    """Same as /taxi/quote but streams one NDJSON line per finished graph node, so the UI can show
    the fare as soon as it's priced while the AI is still writing the pitch.

    Lines: {"type": "node", "node": <name>, "data": <state update>} ... then
    {"type": "done", "quote_id": ..., "result": {...}} or {"type": "error", "detail": ...}.
    """
    async def events():
        result = {}
        try:
            async with _taxi_signals.connect() as signals:
                async for chunk in _taxi_graph.astream(_initial_taxi_state(req), config={"configurable": {"signals": signals}},
                                                       stream_mode="updates"):
                    for node, update in chunk.items():
                        result.update(update or {})
                        yield json.dumps({"type": "node", "node": node, "data": update}) + "\n"
            out = {k: result.get(k) for k in _RESULT_KEYS}
            out_id = await asyncio.to_thread(_save_quote, req, out)
            yield json.dumps({"type": "done", "quote_id": out_id, "result": out}) + "\n"
        except Exception as e:
            yield json.dumps({"type": "error", "detail": _quote_error(e)}) + "\n"

    return StreamingResponse(events(), media_type="application/x-ndjson")


class OutcomeRequest(BaseModel):
    outcome: Literal["won", "lost"]
    final_fare: int = Field(gt=0)  # the fare actually offered (may be a non-recommended option)


@app.post("/taxi/quotes/{quote_id}/outcome")
async def taxi_quote_outcome(quote_id: str, req: OutcomeRequest):
    if not await asyncio.to_thread(store.set_outcome, quote_id, req.outcome, req.final_fare):
        raise HTTPException(status_code=404, detail="Quote not found")
    return {"ok": True, "stats": await asyncio.to_thread(store.recent_stats)}


@app.get("/taxi/quotes")
async def taxi_quotes(limit: int = Query(10, ge=1, le=50)):
    quotes, stats = await asyncio.gather(asyncio.to_thread(store.list_quotes, limit), asyncio.to_thread(store.recent_stats))
    return {"quotes": quotes, "stats": stats}


@app.get("/places")
async def places(q: str = Query(min_length=3, max_length=100)):
    """Search-as-you-type place suggestions (UI helper, not an agent signal, so no MCP hop)."""
    try:
        return await asyncio.to_thread(route_collector.search_places, q)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Place search unavailable: {e}")


@app.get("/places/reverse")
async def places_reverse(lat: float = Query(ge=-90, le=90), lon: float = Query(ge=-180, le=180)):
    try:
        return await asyncio.to_thread(route_collector.reverse_geocode, lat, lon)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Reverse geocoding unavailable: {e}")
