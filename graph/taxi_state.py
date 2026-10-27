from typing import TypedDict, Optional


class TaxiState(TypedDict):
    # Inputs
    pickup: str
    drop: str
    pickup_point: Optional[dict]  # {"lat", "lon", "name"} when picked from suggestions
    drop_point: Optional[dict]
    trip_time: str  # ISO datetime, local time of the trip
    hour: int
    vehicle_type: str  # hatchback | sedan | suv
    competitor_fares: list[dict]  # [{"name": "Red Taxi", "fare": 600}, ...]
    taxi_supply: str  # very_few | some | plenty
    weather_override: str  # auto | clear | rain
    fuel_price: float
    mileage_kmpl: float
    min_profit: float
    return_empty: bool
    tolls_parking: float
    language: str  # pitch language: en | ta

    # Filled in by the graph
    history: Optional[dict]  # recent booked/lost stats for this driver
    route: Optional[dict]
    weather: Optional[dict]
    quote: Optional[dict]  # rule-based fare + breakdown
    advice: Optional[dict]  # final fare, reasoning, customer pitch
    advice_source: Optional[str]  # "groq" or "fallback"
    error: Optional[str]
