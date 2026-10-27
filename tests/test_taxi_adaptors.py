"""Run: python -m unittest discover tests"""
import unittest

from adaptors.taxi_adaptors import _charm_down, _charm_up, compute_taxi_fare, learned_adjustment

BASE = dict(
    distance_km=11.0, duration_min=15.0, taxi_supply="some", is_raining=False, rain_intensity="none",
    hour=14, vehicle_type="sedan", fuel_price=102, mileage_kmpl=15, min_profit=150,
    return_empty=True, tolls_parking=0,
)
QUOTES = [{"name": "Red Taxi", "fare": 600}, {"name": "Blue Taxi", "fare": 580}]


def quote(**overrides):
    return compute_taxi_fare(**{**BASE, "competitor_fares": QUOTES, **overrides})


class CharmPricing(unittest.TestCase):
    def test_charm_down(self):
        self.assertEqual(_charm_down(549.55), 549)
        self.assertEqual(_charm_down(562.6), 559)
        self.assertEqual(_charm_down(559), 559)
        self.assertEqual(_charm_down(558.9), 549)

    def test_charm_up(self):
        self.assertEqual(_charm_up(482.3), 489)
        self.assertEqual(_charm_up(489), 489)
        self.assertEqual(_charm_up(490), 499)


class TaxiFare(unittest.TestCase):
    def test_beats_cheapest_competitor(self):
        q = quote()
        self.assertEqual(q["anchor_name"], "Blue Taxi")
        self.assertLess(q["fare"], 580)
        self.assertTrue(q["can_beat_market"])
        self.assertEqual(q["fare"] % 10, 9)

    def test_user_example_rain_and_few_taxis(self):
        # Station -> airport, Red 600 / Blue 580, raining, very few taxis
        q = quote(is_raining=True, rain_intensity="light", taxi_supply="very_few")
        self.assertEqual(q["fare"], 549)
        self.assertEqual(q["savings_vs_cheapest"], 31)

    def test_high_demand_means_smaller_undercut(self):
        calm = quote(taxi_supply="plenty")
        busy = quote(taxi_supply="very_few", is_raining=True, rain_intensity="heavy", hour=23)
        self.assertGreater(busy["fare"], calm["fare"])
        self.assertLess(busy["undercut_pct"], calm["undercut_pct"])

    def test_never_below_cost_floor(self):
        # Competitors so cheap we can't beat them profitably
        q = quote(competitor_fares=[{"name": "Cheap Cabs", "fare": 200}])
        self.assertGreaterEqual(q["fare"], q["breakdown"]["cost_floor"])
        self.assertFalse(q["can_beat_market"])
        self.assertEqual(q["allowed_range"], [q["fare"], q["fare"]])

    def test_no_quotes_uses_market_estimate(self):
        q = quote(competitor_fares=[])
        self.assertEqual(q["market_source"], "estimate")
        self.assertLess(q["fare"], q["anchor_fare"])

    def test_empty_return_raises_cost(self):
        self.assertGreater(quote(return_empty=True)["breakdown"]["fuel_cost"],
                           quote(return_empty=False)["breakdown"]["fuel_cost"])

    def test_options_are_ordered_and_profitable(self):
        q = quote(is_raining=True, rain_intensity="light", taxi_supply="very_few")
        quick, rec, mx = (o["fare"] for o in q["options"])
        self.assertLessEqual(quick, rec)
        self.assertLessEqual(rec, mx)
        self.assertLess(mx, q["anchor_fare"])
        self.assertTrue(all(o["fare"] >= q["floor_fare"] for o in q["options"]))
        self.assertEqual(rec, q["fare"])

    def test_eta_includes_traffic_at_peak(self):
        self.assertGreater(quote(hour=18)["eta_min"], quote(hour=23)["eta_min"])


class Learning(unittest.TestCase):
    def test_needs_five_outcomes(self):
        self.assertEqual(learned_adjustment({"decided": 4, "won": 4, "lost": 0}), 0)
        self.assertEqual(learned_adjustment(None), 0)

    def test_winning_streak_charges_more(self):
        base = quote()
        hot = quote(history={"decided": 10, "won": 10, "lost": 0})
        self.assertGreater(hot["fare"], base["fare"])
        self.assertTrue(any(f["key"] == "learned" for f in hot["factors"]))

    def test_losing_streak_prices_sharper(self):
        base = quote()
        cold = quote(history={"decided": 10, "won": 1, "lost": 9})
        self.assertLess(cold["fare"], base["fare"])
        self.assertGreaterEqual(cold["fare"], cold["floor_fare"])

    def test_middle_win_rate_changes_nothing(self):
        self.assertEqual(learned_adjustment({"decided": 10, "won": 6, "lost": 4}), 0)


if __name__ == "__main__":
    unittest.main()
