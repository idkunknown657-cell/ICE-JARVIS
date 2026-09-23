"""
Indicator math (spec §2): known-value checks on every indicator plus the
structure functions (pivots, S/R, trend, MA cross, breakout, volatility).
Everything is backward-looking and NaN-padded — asserted where it matters.
"""
import math
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trading_env import TradingEnvMixin

from core.trading import indicators as ind


def _finite(a):
    return ind.last_finite(a)


class BasicIndicatorsTest(unittest.TestCase):
    def test_sma_known_values(self):
        out = ind.sma([1, 2, 3, 4, 5], 3)
        self.assertTrue(math.isnan(out[0]) and math.isnan(out[1]))
        self.assertAlmostEqual(out[2], 2.0)
        self.assertAlmostEqual(out[3], 3.0)
        self.assertAlmostEqual(out[4], 4.0)

    def test_sma_short_series_all_nan(self):
        self.assertTrue(np.all(np.isnan(ind.sma([1, 2], 5))))

    def test_ema_seeded_with_mean_and_correct_recursion(self):
        out = ind.ema([1, 2, 3, 4, 5], 3)     # alpha = 0.5
        self.assertAlmostEqual(out[2], 2.0)    # seed = mean(1,2,3)
        self.assertAlmostEqual(out[3], 3.0)    # .5*4 + .5*2
        self.assertAlmostEqual(out[4], 4.0)    # .5*5 + .5*3

    def test_rsi_all_gains_is_100(self):
        closes = list(range(100, 117))         # 16 closes, 15 gains
        out = ind.rsi(closes, 14)
        self.assertAlmostEqual(_finite(out), 100.0)

    def test_rsi_known_balance(self):
        # 7 gains of +1.0 and 7 losses of -0.5 over the first 14 deltas:
        # avg_gain .5, avg_loss .25 → RS 2 → RSI 66.667 (Wilder seed).
        closes = [100, 101, 100.5, 101.5, 101, 102, 101.5, 102.5, 102,
                  103, 102.5, 103.5, 103, 104, 103.5]
        out = ind.rsi(closes, 14)
        self.assertAlmostEqual(out[14], 66.6667, places=3)

    def test_macd_line_and_histogram_consistent(self):
        closes = [100 + i * (1 if i % 7 else -2) for i in range(80)]
        line, sig, hist = ind.macd(closes)
        self.assertTrue(math.isnan(line[24]))          # needs slow EMA (26)
        self.assertTrue(np.isfinite(line[-1]))
        self.assertAlmostEqual(hist[-1], line[-1] - sig[-1], places=9)

    def test_bollinger_on_constant_series_collapses(self):
        mid, up, dn, pctb, bw = ind.bollinger([5.0] * 25, 20, 2)
        self.assertAlmostEqual(_finite(up), 5.0)
        self.assertAlmostEqual(_finite(dn), 5.0)
        # upper == lower → percent-b is undefined (NaN), never a fake 0.5
        self.assertTrue(math.isnan(np.asarray(pctb)[-1]))

    def test_bollinger_known_standard_deviation(self):
        closes = [float(i) for i in range(20)]         # mean 9.5, pop-sd √33.25
        mid, up, dn, pctb, bw = ind.bollinger(closes, 20, 2)
        self.assertAlmostEqual(mid[19], 9.5)
        self.assertAlmostEqual(up[19], 9.5 + 2 * math.sqrt(33.25), places=6)
        self.assertAlmostEqual(dn[19], 9.5 - 2 * math.sqrt(33.25), places=6)

    def test_atr_constant_range_is_that_range(self):
        n = 30
        h = [10.0] * n
        l = [0.0] * n
        c = [5.0] * n
        self.assertAlmostEqual(_finite(ind.atr(h, l, c, 14)), 10.0)

    def test_true_range_first_bar_is_high_minus_low(self):
        tr = ind.true_range([12.0, 12.0], [10.0, 11.0], [11.0, 11.5])
        self.assertAlmostEqual(tr[0], 2.0)
        # next bar: max(h-l=1, |h-prev_close|=|12-11|=1, |l-prev_close|=|11-11|=0)
        self.assertAlmostEqual(tr[1], 1.0)

    def test_stochastic_constant_is_50_and_extremes_hit_100(self):
        k, d = ind.stochastic([5.0] * 20, [5.0] * 20, [5.0] * 20)
        self.assertAlmostEqual(_finite(k), 50.0)
        # closes riding the top of their range → %K 100
        h = [float(i + 1) for i in range(20)]
        l = [float(i) for i in range(20)]
        c = list(h)
        self.assertAlmostEqual(_finite(ind.stochastic(h, l, c)[0]), 100.0)

    def test_adx_trend_recognises_a_strong_drift(self):
        n = 60
        h = [10.0 + i for i in range(n)]
        l = [0.0 + i for i in range(n)]
        c = [5.0 + i for i in range(n)]
        adx, pdi, mdi = ind.adx(h, l, c, 14)
        self.assertGreater(_finite(adx), 50.0)
        self.assertGreater(_finite(pdi), _finite(mdi))

    def test_adx_too_short_returns_nan(self):
        adx, _, _ = ind.adx([1.0] * 5, [0.0] * 5, [0.5] * 5, 14)
        self.assertTrue(np.all(np.isnan(adx)))

    def test_vwap_constant_typical_price(self):
        h, l, c, v = [4.0] * 10, [2.0] * 10, [3.0] * 10, [10.0] * 10
        self.assertAlmostEqual(_finite(ind.vwap(h, l, c, v)), 3.0)

    def test_volume_stats_relative_volume(self):
        avg, rel = ind.volume_stats([1000.0] * 19 + [4000.0], 20)
        self.assertAlmostEqual(avg, 1000.0)
        self.assertAlmostEqual(rel, 4.0)


class StructureTest(unittest.TestCase):
    def test_pivots_finds_swing_high_and_low(self):
        h = [10, 11, 15, 11, 10, 11, 12]
        l = [8, 9, 14, 9, 7, 9, 10]
        highs, lows = ind.pivots(h, l, left=2, right=2)
        self.assertIn((2, 15.0), [(i, float(p)) for i, p in highs])
        self.assertIn((4, 7.0), [(i, float(p)) for i, p in lows])

    def test_support_resistance_split_around_price(self):
        # zigzag generating pivots below and above 100
        h = [100, 102, 101, 104, 102, 103, 106, 104, 105]
        l = [98, 99, 97, 100, 96, 98, 101, 99, 100]
        c = [99, 101, 98, 103, 97, 102, 105, 100, 104]
        sr = ind.support_resistance({"h": h, "l": l, "c": c}, price=102.0)
        for lv in sr["supports"]:
            self.assertLess(lv["price"], 102.0)
        for lv in sr["resistances"]:
            self.assertGreater(lv["price"], 102.0)
        self.assertFalse(sr["fallback"])

    def test_support_resistance_falls_back_to_series_extremes(self):
        monotonic = {"h": [float(i) for i in range(6)],
                     "l": [float(i) - 1 for i in range(6)],
                     "c": [float(i) - 0.5 for i in range(6)]}
        sr = ind.support_resistance(monotonic, price=3.0)
        self.assertTrue(sr["fallback"])

    def test_trend_structure_detects_higher_highs_and_lows(self):
        h = [10, 11, 12, 11, 10, 11, 13, 12, 11, 12, 14, 13, 12, 13, 15]
        l = [8, 9, 10, 9, 8, 9, 11, 10, 9, 10, 12, 11, 10, 11, 13]
        c = [(x + y) / 2 for x, y in zip(h, l)]
        out = ind.trend_structure(h, l, c)
        self.assertEqual(out["label"], "uptrend")
        self.assertTrue(any("higher highs" in e for e in out["evidence"]))

    def test_trend_structure_detects_lower_lows(self):
        h = [15, 14, 13, 14, 15, 14, 12, 13, 14, 13, 11, 12, 13, 12, 10]
        l = [13, 12, 10, 12, 13, 11, 10, 11, 12, 10, 9, 10, 11, 9, 8]
        c = [(x + y) / 2 for x, y in zip(h, l)]
        out = ind.trend_structure(h, l, c)
        self.assertEqual(out["label"], "downtrend")

    def test_previous_levels_uses_second_to_last_bar(self):
        payload = {"bars": [
            {"t": 1, "h": 10, "l": 8, "c": 9},
            {"t": 2, "h": 20, "l": 18, "c": 19},
            {"t": 3, "h": 30, "l": 28, "c": 29},
        ]}
        prev = ind.previous_levels(payload)
        self.assertEqual(prev["high"], 20)     # the still-developing bar is ignored

    def test_previous_levels_needs_two_bars(self):
        self.assertIsNone(ind.previous_levels({"bars": [{"t": 1, "h": 1, "l": 1, "c": 1}]}))

    def test_ma_cross_state_bullish_on_rising_series(self):
        out = ind.ma_cross_state(list(range(1, 30)), 5, 10)
        self.assertIn("bullish", out["state"])
        self.assertIsNone(out["cross_bars_ago"])

    def test_ma_cross_state_detects_recent_cross(self):
        series = [100 - i for i in range(40)] + [200.0]
        out = ind.ma_cross_state(series, 5, 10)
        self.assertEqual(out["cross_bars_ago"], 0)
        self.assertIn("golden", out["state"])

    def test_breakout_state_detects_a_wick_through_resistance(self):
        sr = {"supports": [{"price": 90, "touches": 2}],
              "resistances": [{"price": 110, "touches": 3}]}
        out = ind.breakout_state([100.0, 111.0, 108.0], sr, lookback=3)
        self.assertEqual(out["near_resistance"], 110)
        self.assertTrue(out["closed_above"])
        self.assertFalse(out["closed_below"])

    def test_volatility_and_atr_percent(self):
        flat = [100.0] * 30
        out = ind.volatility(flat, 20)
        self.assertAlmostEqual(out["returns_pct"], 0.0)
        h = [110.0] * 30
        l = [100.0] * 30          # constant 10-point range → ATR 10
        c = [105.0] * 30
        self.assertAlmostEqual(ind.atr_pct(h, l, c), 10 / 105 * 100, places=6)

    def test_last_finite_helpers(self):
        self.assertEqual(ind.last_finite([float("nan"), 3.5]), 3.5)
        self.assertIsNone(ind.last_finite([float("nan"), float("nan")]))
        self.assertIsNone(ind.last_finite([]))


class NoFutureDataTest(unittest.TestCase):
    def test_prefix_invariance_every_value_uses_only_past_bars(self):
        """Appending bars must never change earlier indicator values —
        the structural proof that nothing looks ahead (§2)."""
        rng = np.random.default_rng(7)
        base = list(100 + rng.normal(0, 1, size=120).cumsum())
        longer = base + [base[-1] + 500]     # a wild future bar

        def all_values(c):
            out = {}
            out["sma"] = ind.sma(c, 20)
            out["ema"] = ind.ema(c, 20)
            out["rsi"] = ind.rsi(c, 14)
            out["macd"] = ind.macd(c)[0]
            out["atr"] = ind.atr(c, c, c, 14)
            return out

        a, b = all_values(base), all_values(longer)
        for key in a:
            for i in range(len(base)):
                va, vb = a[key][i], b[key][i]
                if math.isnan(va):
                    self.assertTrue(math.isnan(vb), f"{key}[{i}] changed NaN↔value")
                else:
                    self.assertAlmostEqual(va, vb, places=9,
                                           msg=f"{key}[{i}] depends on future data")


if __name__ == "__main__":
    unittest.main()
