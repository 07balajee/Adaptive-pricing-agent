"""Run: python -m unittest discover tests"""
import json
import tempfile
import unittest
from pathlib import Path

from adaptors.taxi_adaptors import compute_taxi_fare
from agents.taxi_strategist import generate_fallback_advice, parse_taxi_advice
from storage import store

QUOTE = compute_taxi_fare(
    distance_km=11.0, duration_min=15.0, competitor_fares=[{"name": "Blue Taxi", "fare": 580}],
    taxi_supply="very_few", is_raining=True, rain_intensity="light", hour=14, vehicle_type="sedan",
    fuel_price=102, mileage_kmpl=15, min_profit=150, return_empty=True, tolls_parking=0,
)


def llm(**overrides):
    data = {"fare": 549, "headline": "Beat Blue Taxi", "reasoning": ["Cheaper"],
            "customer_pitch": "₹{FARE} — ₹{SAVINGS} less than {COMPETITOR}!", "selling_points": ["No surge"]}
    return json.dumps({**data, **overrides})


class ParseAdvice(unittest.TestCase):
    def test_valid_template_passes(self):
        a = parse_taxi_advice(llm(), QUOTE)
        self.assertEqual(a["fare"], 549)
        self.assertIn("{FARE}", a["customer_pitch"])

    def test_fare_is_clamped_into_allowed_range(self):
        lo, hi = QUOTE["allowed_range"]
        self.assertEqual(parse_taxi_advice(llm(fare=10), QUOTE)["fare"], lo)
        self.assertEqual(parse_taxi_advice(llm(fare=9999), QUOTE)["fare"], hi)

    def test_literal_numbers_are_turned_into_placeholders(self):
        a = parse_taxi_advice(llm(customer_pitch=f"Only ₹{QUOTE['fare']}, save ₹{QUOTE['savings_vs_cheapest']}!"), QUOTE)
        self.assertIn("₹{FARE}", a["customer_pitch"])
        self.assertIn("₹{SAVINGS}", a["customer_pitch"])

    def test_pitch_without_fare_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_taxi_advice(llm(customer_pitch="Book me, I'm cheap!"), QUOTE)

    def test_malformed_json_is_rejected(self):
        with self.assertRaises(Exception):
            parse_taxi_advice("not json", QUOTE)

    def test_english_pitch_rejected_when_tamil_requested(self):
        with self.assertRaises(ValueError):
            parse_taxi_advice(llm(), QUOTE, language="ta")

    def test_english_savings_phrase_is_translated_in_tamil(self):
        pitch = "save ₹{SAVINGS} vs {COMPETITOR} – ₹{FARE} மட்டும்! மழை வந்தாலும் நிலையான விலை, ஆப் இல்லாமல் நேரடி டிரைவர்."
        a = parse_taxi_advice(llm(customer_pitch=pitch), QUOTE, language="ta")
        self.assertNotIn("save", a["customer_pitch"])
        self.assertIn("{COMPETITOR}-ஐ விட ₹{SAVINGS} குறைவு", a["customer_pitch"])


class FallbackAdvice(unittest.TestCase):
    STATE = {"quote": QUOTE, "weather": {"is_raining": True},
             "route": {"pickup": {"name": "Junction"}, "drop": {"name": "Airport"}}}

    def test_templates_have_placeholders_in_both_languages(self):
        for lang in ("en", "ta"):
            a = generate_fallback_advice({**self.STATE, "language": lang})
            self.assertIn("{FARE}", a["customer_pitch"])
            self.assertIn("{SAVINGS}", a["customer_pitch"])
            self.assertEqual(a["fare"], QUOTE["fare"])


class Store(unittest.TestCase):
    def setUp(self):
        self._orig = store.DB_PATH
        self._tmp = tempfile.TemporaryDirectory()
        store.DB_PATH = Path(self._tmp.name) / "test.db"

    def tearDown(self):
        store.DB_PATH = self._orig
        self._tmp.cleanup()

    def test_outcomes_feed_stats(self):
        ids = [store.save_quote("A", "B", 10, 549, "Blue Taxi", 580, 0.7) for _ in range(3)]
        store.set_outcome(ids[0], "won", 549)
        store.set_outcome(ids[1], "won", 569)
        store.set_outcome(ids[2], "lost", 549)
        stats = store.recent_stats()
        self.assertEqual((stats["decided"], stats["won"], stats["lost"]), (3, 2, 1))
        self.assertEqual(stats["earned"], 549 + 569)
        self.assertFalse(store.set_outcome("missing", "won", 1))

    def test_cache_roundtrip(self):
        self.assertIsNone(store.cache_get("k"))
        store.cache_set("k", {"a": 1})
        self.assertEqual(store.cache_get("k"), {"a": 1})


if __name__ == "__main__":
    unittest.main()
