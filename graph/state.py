from typing import TypedDict, Optional


class PricingState(TypedDict):
    # Inputs
    product_name: str
    base_price: float
    cost_price: float
    inventory_stock: int
    competitor_price: float
    expiry_date: str  # ISO date (YYYY-MM-DD) so the state stays JSON-serializable
    city: str

    # Filled in by the graph
    weather_signal: Optional[str]
    rule_based_price: Optional[float]
    llm_recommendation: Optional[str]
    recommendation_source: Optional[str]  # "groq" or "mock"
    error: Optional[str]
