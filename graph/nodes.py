"""One function per graph node. Each takes the current PricingState and returns only the
keys it updates; LangGraph merges them into the state."""
import os

from agents.pricing_strategist import (
    build_pricing_prompt,
    generate_mock_pricing_decision,
    get_groq_recommendation,
)
from mcp_server.client import SignalsClient

from .state import PricingState

_signals_client = SignalsClient()


async def fetch_signals(state: PricingState) -> dict:
    # A missing weather signal shouldn't block pricing, so record the failure as the signal
    try:
        weather_signal = await _signals_client.get_weather(state["city"], os.getenv("OPENWEATHER_API_KEY"))
    except Exception as e:
        weather_signal = f"Could not fetch weather data: {e}"
    return {"weather_signal": weather_signal}


async def compute_rule_based_price(state: PricingState) -> dict:
    rule_based_price = await _signals_client.get_vendor_price(
        base_price=state["base_price"],
        inventory_stock=state["inventory_stock"],
        competitor_price=state["competitor_price"],
        expiry_date=state["expiry_date"],
    )
    return {"rule_based_price": rule_based_price}


def call_llm(state: PricingState) -> dict:
    groq_api_key = os.getenv("GROQ_API_KEY")
    if not groq_api_key:
        return {"error": "GROQ_API_KEY is missing in .env"}

    prompt = build_pricing_prompt(
        product_name=state["product_name"],
        base_price=state["base_price"],
        cost_price=state["cost_price"],
        inventory_stock=state["inventory_stock"],
        competitor_price=state["competitor_price"],
        expiry_date=state["expiry_date"],
        weather_signal=state["weather_signal"],
        rule_based_price=state["rule_based_price"],
    )
    try:
        return {"llm_recommendation": get_groq_recommendation(prompt, groq_api_key), "recommendation_source": "groq"}
    except Exception as e:
        return {"error": f"Groq API call failed: {e}"}


def fallback_mock(state: PricingState) -> dict:
    print(f"[Warning] {state['error']}. Falling back to local AI Pricing Strategist simulation...")
    recommendation = generate_mock_pricing_decision(
        state["product_name"],
        state["base_price"],
        state["cost_price"],
        state["inventory_stock"],
        state["competitor_price"],
        state["expiry_date"],
        state["city"],
        state["weather_signal"],
        state["rule_based_price"],
    )
    return {"llm_recommendation": recommendation, "recommendation_source": "mock"}
