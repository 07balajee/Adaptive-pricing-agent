"""LLM layer for taxi fares: explains the rule-based fare, may fine-tune it inside a safe range,
and writes a customer-facing pitch the driver can send (WhatsApp, SMS, a sign at the stand).

The rule engine (adaptors/taxi_adaptors.py) owns the maths. The LLM can only move the fare
within `allowed_range` — profitable and still under the cheapest competitor — so a bad
completion can never make the driver lose money or lose to the market.

The pitch is a template with {FARE}, {SAVINGS} and {COMPETITOR} placeholders so the UI can
fill it for whichever fare option (quick win / recommended / max earnings) the driver picks.
"""
import json
import re

_VEHICLE_LABEL = {"hatchback": "Hatchback", "sedan": "Sedan", "suv": "SUV"}
_LANGUAGE = {"en": "English", "ta": "Tamil"}
# How to phrase the saving, per pitch language (shown to the LLM as an example)
_SAVINGS_EXAMPLE = {
    "en": "₹{SAVINGS} less than {COMPETITOR}",
    "ta": "{COMPETITOR}-ஐ விட ₹{SAVINGS} குறைவு",
}
_LANGUAGE_RULE = {
    "en": "Write the whole message in simple English.",
    "ta": "Write the WHOLE message in Tamil script (தமிழ்). Only brand names, 'WhatsApp' and the placeholders stay in English.",
}


def build_taxi_prompt(state: dict) -> str:
    q = state["quote"]
    route = state["route"]
    weather = state["weather"]
    competitors = "\n".join(f"  - {c['name']}: ₹{c['fare']:.0f}" for c in state["competitor_fares"]) or \
        f"  - (no quotes entered; estimated market fare ₹{q['anchor_fare']:.0f})"
    factors = "\n".join(f"  - {f['label']}: {f['detail']}" for f in q["factors"])
    lo, hi = q["allowed_range"]
    lang = state.get("language", "en") if state.get("language") in _LANGUAGE else "en"
    language = _LANGUAGE[lang]
    savings_rule = (
        f"Start with the price ₹{{FARE}}, then mention the saving early, phrased naturally like: \"{_SAVINGS_EXAMPLE[lang]}\"."
        if q["can_beat_market"] else
        "Start with the price ₹{FARE}. We cannot beat the market price profitably, so do NOT claim savings and do NOT use {SAVINGS}; sell reliability and service."
    )

    return f"""
You are a pricing and marketing strategist for an INDEPENDENT taxi driver in India — no brand,
no app, just their own car and phone number. They compete against branded operators.

Trip:
- From: {route['pickup']['name']}  ->  To: {route['drop']['name']}
- Distance: {route['distance_km']:.1f} km, about {q['eta_min']} min with traffic
- Vehicle: {_VEHICLE_LABEL.get(state['vehicle_type'], state['vehicle_type'])}
- Trip time: {state['trip_time']}
- Weather: {weather['description']}, {weather['temp_c']} C (raining: {weather['is_raining']})

Competitor fares for the same trip:
{competitors}

Demand signals:
{factors}

Rule-based fare from the pricing engine: ₹{q['fare']} (undercuts {q['anchor_name']} by ₹{q['savings_vs_cheapest']}).
Driver's cost floor (fuel incl. empty return + tolls + minimum profit): ₹{q['floor_fare']}.
Can we beat the market profitably: {q['can_beat_market']}.

Your job:
1. Choose the final fare. It MUST be a whole number between ₹{lo} and ₹{hi} inclusive.
   Prefer the rule-based fare unless you have a clear reason; prices ending in 9 work well.
2. Explain the decision in 3-4 short bullet points (English) a driver can understand.
3. Write a short, warm customer pitch in {language} (max 60 words, 1-2 emojis) the driver can
   send on WhatsApp. {_LANGUAGE_RULE[lang]}
   It is a TEMPLATE: write the fare as ₹{{FARE}}, the saving as ₹{{SAVINGS}} and the competitor's
   name as {{COMPETITOR}} — literally, with the curly braces — never as numbers. {savings_rule}
   Highlight what an independent driver offers that brands don't: fixed price with no surge,
   direct contact with the driver, pickup without app hassle. Mention rain/night if relevant.
   Do not invent a name, phone number, or features like "AC" unless given.
4. List 3 short selling points in English (max 5 words each).

Respond with ONLY a JSON object, exactly these keys:
{{"fare": <int>, "headline": "<one line, English, max 12 words>", "reasoning": ["..."], "customer_pitch": "...", "selling_points": ["...", "...", "..."]}}
"""


def _templatize(pitch: str, quote: dict, fare: int) -> str:
    """Turns literal numbers back into placeholders if the LLM ignored the template instruction."""
    for amount in {fare, quote["fare"]}:
        pitch = pitch.replace(f"₹{amount}", "₹{FARE}").replace(f"Rs {amount}", "₹{FARE}").replace(f"Rs. {amount}", "₹{FARE}")
    if quote["savings_vs_cheapest"] > 0:
        pitch = pitch.replace(f"₹{quote['savings_vs_cheapest']}", "₹{SAVINGS}")
    return pitch


def _tamil_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    return sum("஀" <= c <= "௿" for c in letters) / len(letters) if letters else 0.0


def parse_taxi_advice(raw: str, quote: dict, language: str = "en") -> dict:
    """Parses the LLM JSON, clamps its fare into the allowed range and checks the pitch template.
    Raises on malformed output so the graph falls back to template advice."""
    data = json.loads(raw)
    lo, hi = quote["allowed_range"]
    fare = min(max(int(round(float(data["fare"]))), lo), hi)
    pitch = _templatize(str(data["customer_pitch"]).strip(), quote, fare)
    advice = {
        "fare": fare,
        "headline": str(data["headline"]).strip(),
        "reasoning": [str(r).strip() for r in data["reasoning"]][:5],
        "customer_pitch": pitch,
        "selling_points": [str(p).strip() for p in data.get("selling_points", [])][:4],
    }
    if not advice["reasoning"] or "{FARE}" not in pitch:
        raise ValueError("LLM pitch is missing the {FARE} placeholder or reasoning is empty")
    if language == "ta":
        # The model sometimes keeps the savings phrase in English around the placeholders
        pitch = re.sub(r"save\s+₹\{SAVINGS\}\s+(?:vs\.?|than|compared to)\s+\{COMPETITOR\}",
                       _SAVINGS_EXAMPLE["ta"], pitch, flags=re.IGNORECASE)
        pitch = re.sub(r"₹\{SAVINGS\}\s+less than\s+\{COMPETITOR\}", _SAVINGS_EXAMPLE["ta"], pitch, flags=re.IGNORECASE)
        advice["customer_pitch"] = pitch
    if language == "ta" and _tamil_ratio(pitch) < 0.6:
        raise ValueError("LLM pitch was requested in Tamil but is mostly not Tamil")
    return advice


_FALLBACK_PITCH = {
    "en": {
        "beat": "🚖 {pickup} → {drop} for just ₹{{FARE}} — save ₹{{SAVINGS}} vs {{COMPETITOR}}! Fixed price, no surge, no app needed. Call or WhatsApp me directly and I'll be there.",
        "service": "🚖 {pickup} → {drop} for ₹{{FARE}}, fixed. No surge, no app, no hidden charges — call or WhatsApp me directly and talk to your driver, not a call centre.",
        "rain": " Rain? No problem — I'm ready to pick you up. ☔",
    },
    "ta": {
        "beat": "🚖 {pickup} → {drop} வெறும் ₹{{FARE}} மட்டுமே — {{COMPETITOR}}-ஐ விட ₹{{SAVINGS}} குறைவு! நிலையான கட்டணம், surge இல்லை, ஆப் தேவையில்லை. நேரடியாக அழைக்கவும் அல்லது WhatsApp செய்யவும்.",
        "service": "🚖 {pickup} → {drop} ₹{{FARE}} நிலையான கட்டணம். Surge இல்லை, ஆப் இல்லை, மறைமுக கட்டணம் இல்லை — டிரைவரிடம் நேரடியாகப் பேசுங்கள்.",
        "rain": " மழையா? கவலை வேண்டாம் — உடனே வருகிறேன். ☔",
    },
}


def generate_fallback_advice(state: dict) -> dict:
    """Template-based advice used when the LLM is unavailable."""
    q = state["quote"]
    route = state["route"]
    weather = state["weather"]
    fare = q["fare"]
    pickup, drop = route["pickup"]["name"], route["drop"]["name"]
    texts = _FALLBACK_PITCH.get(state.get("language", "en"), _FALLBACK_PITCH["en"])

    if q["can_beat_market"]:
        headline = f"₹{fare} — ₹{q['savings_vs_cheapest']} cheaper than {q['anchor_name']}"
    else:
        headline = f"₹{fare} is your lowest profitable fare — compete on service"

    reasoning = [
        f"Cheapest branded option is {q['anchor_name']} at ₹{q['anchor_fare']:.0f}; "
        + (f"you undercut it by {q['undercut_pct']}%." if q["can_beat_market"] else "you can't go lower without losing money."),
        f"Your trip costs about ₹{q['breakdown']['fuel_cost']} in fuel "
        f"({q['breakdown']['trip_km'] + q['breakdown']['empty_return_km']:.1f} km incl. empty return), "
        f"so you keep about ₹{q['breakdown']['driver_profit']}.",
    ]
    ups = [f["label"].lower() for f in q["factors"] if f["direction"] == "up" and f["key"] not in ("return", "learned")]
    if ups:
        reasoning.append(f"Demand is high ({', '.join(ups)}), so a small discount is enough to win the ride.")
    else:
        reasoning.append("Demand is normal, so a bigger discount helps you stand out.")
    if q.get("learned_adjustment_pct"):
        reasoning.append(next(f["label"] + " — " + f["detail"].lower() for f in q["factors"] if f["key"] == "learned") + ".")

    pitch = texts["beat" if q["can_beat_market"] else "service"].format(pickup=pickup, drop=drop)
    if weather["is_raining"]:
        pitch += texts["rain"]

    return {
        "fare": fare,
        "headline": headline,
        "reasoning": reasoning,
        "customer_pitch": pitch,
        "selling_points": ["Fixed price, no surge", "Direct driver contact", "No app needed"],
    }
