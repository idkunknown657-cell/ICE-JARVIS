"""
Structured analysis (spec §2/§3/§4/§6/§7/§8/§16): required report sections,
FACT/INTERPRETATION split, disclaimers, no-false-certainty guard. Network
patched at the analysis module's own name bindings (it imports them directly).
"""
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trading_env import TradingEnvMixin

from core.trading import analysis, language


def _candles(tf="1D", n=80):
    bars = []
    for i in range(n):
        base = 100.0 + i * 0.6 + (2.5 if i % 9 == 4 else 0.0) - (2.5 if i % 9 == 7 else 0.0)
        bars.append({"t": 1700000000 + i * 3600, "o": base - 0.3,
                     "h": base + 1.8, "l": base - 1.8, "c": base,
                     "v": 1000 + (300 if i % 9 == 4 else 0)})
    return {"symbol": "TEST", "timeframe": tf, "bars": bars, "meta": {},
            "provider": "FakeFeed", "fetched_at": time.time(),
            "data_asof": bars[-1]["t"], "delayed": False}


def _quote(delayed=False):
    return {"symbol": "TEST", "price": 147.3, "prev_close": 145.0,
            "change": 2.3, "change_pct": 1.586, "open": 145.5, "high": 148.0,
            "low": 144.2, "close": 147.0, "volume": 1234567, "timeframe": "1D",
            "currency": "USD", "exchange": "FakeEx", "name": "Test Corp",
            "instrument": "EQUITY", "provider": "FakeFeed",
            "data_asof": time.time(), "fetched_at": time.time(),
            "delayed": delayed}


_NEWS = [{"title": "Test Corp expands factory", "source": "WireCo",
          "url": "https://example.invalid/n", "date": "2026-09-22", "snippet": "..."}]


def _patch_full(news=None, news_exc=None):
    news_mock = mock.Mock(side_effect=news_exc) if news_exc else mock.Mock(return_value=news if news is not None else _NEWS)
    return (mock.patch("core.trading.analysis.get_quote", return_value=_quote()),
            mock.patch("core.trading.analysis.get_candles",
                       side_effect=lambda s, tf="1D", **kw: _candles(tf)),
            mock.patch("core.trading.analysis.fetch_news", news_mock))


class FullReportTest(TradingEnvMixin, unittest.TestCase):
    def _analyze(self, **kw):
        p_quote, p_candles, p_news = _patch_full(**kw)
        with p_quote, p_candles, p_news:
            return analysis.analyze("TEST", mode="full")

    def test_full_report_contains_every_required_section(self):
        r = self._analyze()
        self.assertTrue(r["ok"], r)
        text = r["text"]
        for section in ("CURRENT MARKET STATE", "BULLISH SCENARIO",
                        "BEARISH SCENARIO", "INVALIDATION", "RISK",
                        "TRADE PLAN", "POSSIBLE SETUP", "CONFIRMATION NEEDED",
                        "NEWS CHECK", "FACT:", "INTERPRETATION:"):
            self.assertIn(section, text)

    def test_report_is_scenario_language_never_certain(self):
        text = self._analyze()["text"]
        self.assertEqual(language.violations(text), [])
        self.assertIn(language.DISCLAIMER_ANALYSIS, text)
        self.assertIn("scenario", text.lower())
        self.assertIn("OBSERVATION:", text)          # observations labeled
        self.assertNotIn("guaranteed", text.lower())

    def test_report_carries_quote_provenance_and_mode_banner(self):
        text = self._analyze()["text"]
        self.assertIn("PAPER TRADING / ANALYSIS MODE", text)
        self.assertIn("Data provider: FakeFeed", text)
        self.assertIn("Data timestamp:", text)
        self.assertIn("Change:", text)

    def test_news_block_separates_fact_from_interpretation(self):
        text = self._analyze(news=_NEWS)["text"]
        self.assertIn("FACT: Test Corp expands factory (Source: WireCo, 2026-09-22)", text)
        self.assertIn("INTERPRETATION:", text)
        self.assertNotIn("will rise", text.lower())

    def test_news_failure_says_so_instead_of_inventing(self):
        from core.trading.data import DataError
        text = self._analyze(news_exc=DataError("offline"))["text"]
        self.assertIn("news lookup failed", text)
        self.assertIn("no news is reported rather than guessed", text)
        self.assertEqual(language.violations(text), [])

    def test_empty_news_results_honestly(self):
        text = self._analyze(news=[])["text"]
        self.assertIn("returned no recent headlines", text)

    def test_trade_plan_levels_are_arithmetic_positive_rr(self):
        r = self._analyze()
        long_lv = r["levels"]["long"]
        self.assertLess(long_lv["entry_lo"], long_lv["entry_hi"])
        self.assertGreater(long_lv["risk_per_unit"], 0)
        self.assertGreater(long_lv["t1"], long_lv["invalidation"])   # long target above stop
        self.assertGreater(long_lv["rr"]["t1"], 0)
        text = r["text"]
        self.assertIn("R:R 1:", text)
        self.assertIn("Target 1:", text)
        self.assertIn("Invalidation: below", text)

    def test_stack_defaults_and_overrides(self):
        p_quote, p_candles, p_news = _patch_full()
        with p_quote, p_candles, p_news:
            r = analysis.analyze("TEST", mode="full", timeframes=["1D", "1h", "15m"])
        self.assertEqual(r["stack"], ["1D", "1h", "15m"])

    def test_full_stack_requires_price_or_errors(self):
        with mock.patch("core.trading.analysis.get_quote",
                        side_effect=RuntimeError("feed down")):
            r = analysis.analyze("TEST", mode="full")
        self.assertFalse(r["ok"])
        self.assertIn("Could not price", r["error"])

    def test_symbol_required(self):
        r = analysis.analyze("", mode="full")
        self.assertFalse(r["ok"])
        self.assertIn("No symbol", r["error"])


class QuickReportTest(TradingEnvMixin, unittest.TestCase):
    def test_quick_contains_indicator_readout_and_defaults_to_1h(self):
        with mock.patch("core.trading.analysis.get_quote", return_value=_quote()) as pq, \
             mock.patch("core.trading.analysis.get_candles",
                        side_effect=lambda s, tf="1D", **kw: _candles(tf)) as pc:
            r = analysis.analyze("TEST", mode="quick")
        self.assertTrue(r["ok"], r)
        self.assertIn("QUICK ANALYSIS", r["text"])
        self.assertIn("RSI(14):", r["text"])
        self.assertIn("Trend structure:", r["text"])
        self.assertIn("Support:", r["text"])
        self.assertIn(language.DISCLAIMER_ANALYSIS, r["text"])
        self.assertEqual(language.violations(r["text"]), [])
        pc.assert_called_with("TEST", "1h")

    def test_quick_uses_requested_timeframe(self):
        with mock.patch("core.trading.analysis.get_quote", return_value=_quote()), \
             mock.patch("core.trading.analysis.get_candles",
                        side_effect=lambda s, tf="1D", **kw: _candles(tf)) as pc:
            analysis.analyze("TEST", mode="quick", timeframes=["15m"])
        pc.assert_called_with("TEST", "15m")

    def test_snapshot_exposes_every_indicator_family(self):
        snap = analysis.snapshot(_candles("1h"))
        for key in ("rsi", "macd_hist", "bb_pctb", "atr", "atr_pct", "adx",
                    "stoch_k", "relative_volume", "sr", "trend", "previous",
                    "ma_cross", "breakout", "vwap", "sma200", "ema200",
                    "returns_pct"):
            self.assertIn(key, snap)
        self.assertGreaterEqual(snap["rsi"], 0)
        self.assertLessEqual(snap["rsi"], 100)
        self.assertIn(snap["trend"]["label"].split(" ")[0],
                      ("uptrend", "downtrend", "range"))


class LanguageGuardTest(unittest.TestCase):
    def test_violations_detects_certainty_phrases(self):
        self.assertIn("guaranteed profit", language.violations("Guaranteed Profit!"))
        self.assertIn("definitely buy", language.violations("I should definitely buy"))
        self.assertIn("100% win", language.violations("this is a 100% win"))
        self.assertEqual(language.violations("Two scenarios are plausible; "
                                             "the downside risk is the stop."), [])

    def test_ensure_safe_appends_correction_only_when_needed(self):
        bad = "Guaranteed profit incoming!"
        fixed = language.ensure_safe(bad)
        self.assertIn("[CORRECTION]", fixed)
        self.assertIn("were auto-rejected", fixed)
        clean = "The setup becomes invalid below 98."
        self.assertEqual(language.ensure_safe(clean), clean)

    def test_ensure_safe_never_raises(self):
        self.assertIsInstance(language.ensure_safe(None), str)

    def test_disclaimers_exist_for_each_report_type(self):
        for d in (language.DISCLAIMER_ANALYSIS, language.DISCLAIMER_BACKTEST,
                  language.DISCLAIMER_JOURNAL, language.DISCLAIMER_DELAY):
            self.assertTrue(d.strip())
        self.assertIn("does not guarantee future results",
                      language.DISCLAIMER_BACKTEST)
        self.assertIn("do not guarantee future performance",
                      language.DISCLAIMER_JOURNAL)
        self.assertIn("may be delayed", language.DISCLAIMER_DELAY)


if __name__ == "__main__":
    unittest.main()
