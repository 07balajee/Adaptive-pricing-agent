"""Rule-based fare engine for independent taxi drivers.

Strategy: find the cheapest fare a customer can get from the branded operators (Red Taxi,
Blue Taxi, Rapido, ...) and come in just under it, without ever dropping below what the trip
actually costs the driver plus their minimum profit.

How far under the market we go depends on demand pressure. When it's raining, taxis are
scarce, or it's peak/night time, customers have few options — a small undercut is enough to
win them, so the driver keeps more money. When taxis are plentiful, we undercut harder.
"""
import math

# Undercut vs the cheapest competitor at zero demand pressure, and at full pressure
MAX_UNDERCUT = 0.12
MIN_UNDERCUT = 0.03

# Used only when the driver enters no competitor quotes: rough Coimbatore-area rates
MARKET_ESTIMATE = {
    "hatchback": {"base": 90, "per_km": 16, "per_min": 1.0},
    "sedan": {"base": 100, "per_km": 19, "per_min": 1.0},
    "suv": {"base": 150, "per_km": 25, "per_min": 1.5},
}

# Fare options offered alongside the recommendation (undercut vs cheapest competitor)
QUICK_WIN_EXTRA = 0.04  # recommended undercut + 4 points: grab the ride fast
MAX_EARNINGS_UNDERCUT = 0.015  # barely under the market: most money, still cheapest

# OSRM durations assume empty roads
TRAFFIC_FACTOR = {"peak": 1.35, "normal": 1.15, "night": 1.0}

SUPPLY_PRESSURE = {"very_few": 0.40, "some": 0.15, "plenty": 0.0}
SUPPLY_LABEL = {"very_few": "Very few taxis nearby", "some": "Some taxis nearby", "plenty": "Plenty of taxis nearby"}


def _time_band(hour: int) -> str:
    if hour >= 22 or hour < 5:
        return "night"
    if 7 <= hour < 10 or 17 <= hour < 21:
        return "peak"
    return "normal"


def _charm_down(amount: float) -> int:
    """562.6 -> 559: the largest fare ending in 9 that is <= amount."""
    return max(9, int((amount + 1) // 10) * 10 - 1)


def _charm_up(amount: float) -> int:
    """482.3 -> 489: the smallest fare ending in 9 that is >= amount."""
    return math.ceil((amount + 1) / 10) * 10 - 1


def estimate_market_fare(distance_km: float, duration_min: float, vehicle_type: str,
                         is_raining: bool, taxi_supply: str) -> float:
    rates = MARKET_ESTIMATE.get(vehicle_type, MARKET_ESTIMATE["sedan"])
    fare = rates["base"] + rates["per_km"] * distance_km + rates["per_min"] * duration_min
    # Branded operators surge in rain and when supply is thin
    if is_raining:
        fare *= 1.2
    if taxi_supply == "very_few":
        fare *= 1.15
    return round(fare)


def learned_adjustment(stats: dict | None) -> float:
    """Undercut correction (fraction, + = undercut more) from the driver's recent booked/lost quotes.

    Winning almost every quote means we're leaving money on the table -> undercut less.
    Losing most of them means the price isn't attractive enough -> undercut more.
    Needs at least 5 outcomes before it kicks in.
    """
    if not stats or stats.get("decided", 0) < 5:
        return 0.0
    rate = stats["won"] / stats["decided"]
    if rate >= 0.75:
        return -min(0.02, (rate - 0.75) * 0.08)
    if rate <= 0.40:
        return min(0.03, (0.40 - rate) * 0.075)
    return 0.0


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
    # 1. What the trip really costs the driver
    dead_km = distance_km if return_empty else 0.0
    fuel_cost = (distance_km + dead_km) / mileage_kmpl * fuel_price
    cost_floor = fuel_cost + tolls_parking + min_profit

    # 2. The market price we have to beat
    quotes = [c for c in competitor_fares if c.get("fare", 0) > 0]
    if quotes:
        cheapest = min(quotes, key=lambda c: c["fare"])
        anchor_name, anchor_fare = cheapest["name"], float(cheapest["fare"])
        market_source = "quotes"
    else:
        anchor_name = "Typical market fare"
        anchor_fare = estimate_market_fare(distance_km, duration_min, vehicle_type, is_raining, taxi_supply)
        market_source = "estimate"

    # 3. Demand pressure decides how much of an undercut we need
    factors = []
    pressure = 0.0
    if is_raining:
        rain_p = 0.5 if rain_intensity == "heavy" else 0.35
        pressure += rain_p
        factors.append({"key": "rain", "label": "Raining" if rain_intensity != "heavy" else "Heavy rain",
                        "detail": "Riders need a cab now and will book fast", "direction": "up"})
    supply_p = SUPPLY_PRESSURE.get(taxi_supply, 0.15)
    pressure += supply_p
    factors.append({"key": "supply", "label": SUPPLY_LABEL.get(taxi_supply, taxi_supply),
                    "detail": "Less competition, small undercut wins" if supply_p >= 0.4
                    else "More competition, undercut harder" if supply_p == 0 else "Normal competition",
                    "direction": "up" if supply_p >= 0.4 else "down" if supply_p == 0 else "flat"})
    band = _time_band(hour)
    if band == "peak":
        pressure += 0.15
        factors.append({"key": "time", "label": "Peak hours", "detail": "Office/train rush keeps demand high", "direction": "up"})
    elif band == "night":
        pressure += 0.20
        factors.append({"key": "time", "label": "Night trip", "detail": "Fewer cabs, riders value safety and reliability", "direction": "up"})
    else:
        factors.append({"key": "time", "label": "Off-peak", "detail": "Regular demand", "direction": "flat"})
    if return_empty:
        factors.append({"key": "return", "label": "Empty return counted", "detail": f"{dead_km:.1f} km back without a passenger", "direction": "up"})
    pressure = min(pressure, 1.0)

    undercut = MAX_UNDERCUT * (1 - pressure) + MIN_UNDERCUT * pressure

    # 4. Learn from the driver's own booked/lost history
    adjustment = learned_adjustment(history)
    if adjustment:
        undercut = min(max(undercut + adjustment, 0.01), 0.15)
        rate = round(history["won"] / history["decided"] * 100)
        factors.append({
            "key": "learned",
            "label": f"You won {rate}% of your last {history['decided']} quotes",
            "detail": "Charging a bit more — riders are saying yes" if adjustment < 0 else "Pricing sharper — too many riders said no",
            "direction": "up" if adjustment < 0 else "down",
        })

    # 5. Final fare: charm-priced, under the market, never below the cost floor
    floor_fare = _charm_up(cost_floor)

    def priced(cut: float) -> int:
        return max(_charm_down(anchor_fare * (1 - cut)), floor_fare)

    fare = priced(undercut)
    can_beat_market = fare < anchor_fare

    options = [
        {"key": "quick", "label": "Quick win", "hint": "Lowest price, grab the ride fast", "fare": priced(undercut + QUICK_WIN_EXTRA)},
        {"key": "recommended", "label": "Recommended", "hint": "Best balance of winning and earning", "fare": fare},
        {"key": "max", "label": "Max earnings", "hint": "Just under the cheapest brand", "fare": max(priced(MAX_EARNINGS_UNDERCUT), fare)},
    ]
    for o in options:
        o["savings"] = round(anchor_fare - o["fare"])
        o["profit"] = round(o["fare"] - fuel_cost - tolls_parking)

    profit = fare - fuel_cost - tolls_parking
    return {
        "fare": fare,
        "options": options,
        "anchor_name": anchor_name,
        "anchor_fare": anchor_fare,
        "market_source": market_source,
        "savings_vs_cheapest": round(anchor_fare - fare),
        "average_market_fare": round(sum(c["fare"] for c in quotes) / len(quotes)) if quotes else anchor_fare,
        "undercut_pct": round(undercut * 100, 1),
        "learned_adjustment_pct": round(adjustment * 100, 1),
        "demand_pressure": round(pressure, 2),
        "time_band": band,
        "eta_min": round(duration_min * TRAFFIC_FACTOR[band]),
        "can_beat_market": can_beat_market,
        "floor_fare": floor_fare,
        # Lowest and highest fares that are both profitable and still under the market
        "allowed_range": [floor_fare, _charm_down(anchor_fare - 1)] if can_beat_market else [floor_fare, floor_fare],
        "breakdown": {
            "trip_km": round(distance_km, 1),
            "empty_return_km": round(dead_km, 1),
            "fuel_cost": round(fuel_cost),
            "tolls_parking": round(tolls_parking),
            "min_profit": round(min_profit),
            "cost_floor": round(cost_floor),
            "driver_profit": round(profit),
        },
        "factors": factors,
    }
