"""Graph nodes for taxi fare quotes. Each returns only the state keys it updates.

MCP-backed nodes take the open session from config["configurable"]["signals"] (a TaxiSignals
from TaxiSignalsClient.connect()), so one quote reuses one MCP server process.
"""
import asyncio
import os

from langchain_core.runnables import RunnableConfig

from agents.pricing_strategist import get_groq_recommendation
from agents.taxi_strategist import build_taxi_prompt, generate_fallback_advice, parse_taxi_advice
from storage import store

from .taxi_state import TaxiState


def _signals(config: RunnableConfig):
    return config["configurable"]["signals"]


async def load_history(state: TaxiState) -> dict:
    return {"history": await asyncio.to_thread(store.recent_stats)}


async def fetch_route(state: TaxiState, config: RunnableConfig) -> dict:
    # No route means no fare, so let failures propagate to the caller
    route = await _signals(config).get_route(state["pickup"], state["drop"], state.get("pickup_point"), state.get("drop_point"))
    return {"route": route}


async def fetch_weather(state: TaxiState, config: RunnableConfig) -> dict:
    override = state["weather_override"]
    if override == "rain":
        return {"weather": {"temp_c": None, "condition": "Rain", "description": "raining (reported by driver)",
                            "is_raining": True, "rain_intensity": "light", "source": "driver"}}
    if override == "clear":
        return {"weather": {"temp_c": None, "condition": "Clear", "description": "clear (reported by driver)",
                            "is_raining": False, "rain_intensity": "none", "source": "driver"}}

    pickup = state["route"]["pickup"]
    try:
        weather = await _signals(config).get_weather_conditions(pickup["lat"], pickup["lon"], os.getenv("OPENWEATHER_API_KEY"))
        weather["source"] = "live"
    except Exception as e:
        # Weather is a demand signal, not a requirement: price as if it's dry
        weather = {"temp_c": None, "condition": "Unknown", "description": f"unavailable ({e})",
                   "is_raining": False, "rain_intensity": "none", "source": "unavailable"}
    return {"weather": weather}


async def price_trip(state: TaxiState, config: RunnableConfig) -> dict:
    route, weather = state["route"], state["weather"]
    quote = await _signals(config).compute_taxi_fare(
        distance_km=route["distance_km"],
        duration_min=route["duration_min"],
        competitor_fares=state["competitor_fares"],
        taxi_supply=state["taxi_supply"],
        is_raining=weather["is_raining"],
        rain_intensity=weather["rain_intensity"],
        hour=state["hour"],
        vehicle_type=state["vehicle_type"],
        fuel_price=state["fuel_price"],
        mileage_kmpl=state["mileage_kmpl"],
        min_profit=state["min_profit"],
        return_empty=state["return_empty"],
        tolls_parking=state["tolls_parking"],
        history=state.get("history"),
    )
    return {"quote": quote}


async def call_llm(state: TaxiState) -> dict:
    groq_api_key = os.getenv("GROQ_API_KEY")
    if not groq_api_key:
        return {"error": "GROQ_API_KEY is missing in .env"}
    try:
        # requests is blocking; run it off the event loop so streaming stays responsive
        raw = await asyncio.to_thread(get_groq_recommendation, build_taxi_prompt(state), groq_api_key, True, "low")
        advice = parse_taxi_advice(raw, state["quote"], state.get("language", "en"))
        return {"advice": advice, "advice_source": "groq"}
    except Exception as e:
        return {"error": f"AI advice failed: {e}"}


def fallback_advice(state: TaxiState) -> dict:
    print(f"[Warning] {state['error']}. Using template advice instead.")
    return {"advice": generate_fallback_advice(state), "advice_source": "fallback"}
