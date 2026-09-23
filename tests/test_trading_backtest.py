"""
Backtesting engine (spec §15): rule parsing, next-bar-open entry discipline,
stop-first tie handling, fees/slippage, the full §15 stat block, honest
zero-trade and failure results, and the historical-results disclaimer on
every output. Data comes from a patched core.trading.data.get_candles.
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trading_env import TradingEnvMixin

from core.trading import backtest, language


def _rising(n=120, start=100.0):
    return [{"t": 1700000000 + i * 86400, "o": start + i - 0.5,
             "h": start + i + 0.5, "l": start + i - 1.0, "c": start + i,
             "v": 1000} for i in range(n)]


def _falling(n=120, start=220.0):
    return [{"t": 1700000000 + i * 86400, "o": start - i + 0.5,
             "h": start - i + 1.0, "l": start - i - 0.5, "c": start - i,
             "v": 1000} for i in range(n)]


def _parabola(n=120, peak=60, start=100.0):
    out = []
    for i in range(n):
        c = start + (i if i < peak else max(2 * peak - i, 1))
        out.append({"t": 1700000000 + i * 86400, "o": c - 0.5, "h": c + 0.5,
                    "l": c - 1.0, "c": c, "v": 1000})
    return out


def _data(bars):
    return {"symbol": "TST", "timeframe": "1D", "bars": bars, "meta": {},
            "provider": "Fake", "data_asof": bars[-1]["t"], "delayed": False}


ENTRY = ["close > ema(20)"]
EXIT = ["close < ema(20)"]


class HappyPathTest(TradingEnvMixin, unittest.TestCase):
    def _run(self, bars=None, **kw):
        payload = _data(bars if bars is not None else _rising())
        with mock.patch("core.trading.data.get_candles",
                        return_value=payload, create=True):
            return backtest.run_backtest(symbol="TST", **kw)

    def test_trend_following_rule_produces_a_trade_and_stats(self):
        r = self._run(entry_rules=ENTRY, exit_rules=EXIT)
        self.assertNotIn("error", r)
        self.assertGreaterEqual(r["total_trades"], 1)
        self.assertGreater(r["net_return_pct"], 0)
        self.assertGreaterEqual(r["max_drawdown"], 0.0)
        self.assertLess(r["max_drawdown"], r["net_pnl"])   # one dip at entry only
        for key in ("net_pnl", "win_rate", "avg_trade", "profit_factor",
                    "max_losing_streak", "max_winning_streak", "exit_reasons",
                    "sample_trades", "costs_note", "disclaimer", "side",
                    "bars", "period", "timeframe"):
            self.assertIn(key, r)
        self.assertIn("fees", r["costs_note"])
        self.assertIn(language.DISCLAIMER_BACKTEST, r["disclaimer"])

    def test_format_result_includes_all_stats_and_disclaimer(self):
        r = self._run(entry_rules=ENTRY, exit_rules=EXIT)
        text = backtest.format_result(r)
        for needle in ("BACKTEST — TST", "Bars:", "Net P/L:", "Max drawdown:",
                       "Win rate:", "Profit factor:",
                       "Sharpe (per-trade, not annualised)",
                       "Losing streak (max)", "Winning streak (max)",
                       "Exits:", "Costs:"):
            self.assertIn(needle, text)
        self.assertIn(language.DISCLAIMER_BACKTEST, text)
        self.assertEqual(language.violations(text), [])

    def test_profit_factor_is_none_and_reported_when_no_losses(self):
        r = self._run(entry_rules=ENTRY, exit_rules=EXIT)
        self.assertIsNone(r["profit_factor"])
        self.assertIn("no losing trades", backtest.format_result(r))

    def test_fees_reduce_net_return(self):
        free = self._run(entry_rules=ENTRY, exit_rules=EXIT)
        paid = self._run(entry_rules=ENTRY, exit_rules=EXIT,
                         fees_pct=1.0, slippage_pct=0.1)
        self.assertLess(paid["net_return_pct"], free["net_return_pct"])

    def test_slippage_alone_also_costs(self):
        clean = self._run(entry_rules=ENTRY, exit_rules=EXIT)
        slipped = self._run(entry_rules=ENTRY, exit_rules=EXIT,
                            slippage_pct=0.5)
        self.assertLess(slipped["net_return_pct"], clean["net_return_pct"])

    def test_stop_loss_exits_after_the_reversal(self):
        r = self._run(_parabola(), entry_rules=ENTRY, exit_rules=[],
                      stop_loss_pct=1.0)
        self.assertGreaterEqual(r["exit_reasons"].get("stop-loss", 0), 1)
        self.assertLess(r["net_return_pct"], 0)   # buying into the arc, stopped on the way down

    def test_take_profit_exits_on_the_way_up(self):
        r = self._run(_parabola(), entry_rules=ENTRY, exit_rules=[],
                      take_profit_pct=0.3)
        self.assertGreaterEqual(r["exit_reasons"].get("take-profit", 0), 1)

    def test_end_of_data_close_is_reported_as_such(self):
        r = self._run(_rising(60), entry_rules=ENTRY, exit_rules=EXIT)
        self.assertEqual(r["exit_reasons"].get("end of data", 0),
                         r["total_trades"])

    def test_short_side(self):
        r = self._run(_falling(), side="short",
                      entry_rules=["close < ema(20)"],
                      exit_rules=["close > ema(20)"])
        self.assertNotIn("error", r)
        self.assertEqual(r["side"], "short")
        self.assertGreaterEqual(r["total_trades"], 1)
        self.assertGreater(r["net_return_pct"], 0)

    def test_fixed_quantity_sizing_is_used_for_every_trade(self):
        r = self._run(entry_rules=ENTRY, exit_rules=EXIT, fixed_quantity=7)
        self.assertTrue(r["sample_trades"])
        for t in r["sample_trades"]:
            self.assertEqual(t["qty"], 7)

    def test_risk_pct_sizing_uses_configured_account_and_stop(self):
        from core.trading import settings as tsettings
        tsettings.set_value("account_size", 10000)
        # budget $100 / (first close $100 × 1% stop distance) = 100 units
        r = self._run(entry_rules=ENTRY, exit_rules=EXIT,
                      stop_loss_pct=1.0, risk_pct=1.0)
        self.assertNotIn("error", r)
        self.assertTrue(r["sample_trades"])
        self.assertEqual(r["sample_trades"][0]["qty"], 100.0)


class RuleAndFailureTest(TradingEnvMixin, unittest.TestCase):
    def _run(self, bars=None, **kw):
        payload = _data(bars if bars is not None else _rising())
        with mock.patch("core.trading.data.get_candles",
                        return_value=payload, create=True):
            return backtest.run_backtest(symbol="TST", **kw)

    def test_unknown_operand_lists_the_supported_set(self):
        r = self._run(entry_rules=["frobnicate > 3"])
        self.assertIn("error", r)
        self.assertIn("Unknown rule operand 'frobnicate'", r["error"])
        self.assertIn("Supported:", r["error"])
        self.assertIn("ema(", r["error"])

    def test_unparseable_rule(self):
        r = self._run(entry_rules=["just go long when it feels right"])
        self.assertIn("Cannot parse rule", r["error"])

    def test_cross_operator_parses(self):
        r = self._run(entry_rules=["close cross_above ema(20)"],
                      exit_rules=["close cross_below ema(20)"])
        self.assertNotIn("error", r)

    def test_missing_entry_rules_rejected(self):
        r = self._run(entry_rules=[])
        self.assertIn("No entry rules", r["error"])

    def test_bad_side_rejected(self):
        r = self._run(entry_rules=["close > 1"], side="sideways")
        self.assertIn("side must be", r["error"])

    def test_too_few_bars(self):
        r = self._run(bars=_rising(10), entry_rules=["close > ema(5)"])
        self.assertIn("too few", r["error"])

    def test_provider_failure_is_surfaced_verbatim(self):
        with mock.patch("core.trading.data.get_candles",
                        side_effect=Exception("HTTP 502 from endpoint")):
            r = backtest.run_backtest(symbol="TST", entry_rules=ENTRY)
        self.assertIn("Could not load historical data", r["error"])
        self.assertIn("HTTP 502 from endpoint", r["error"])

    def test_format_result_for_errors_says_not_run(self):
        text = backtest.format_result({"symbol": "TST", "error": "boom happened"})
        self.assertIn("Backtest not run:", text)
        self.assertIn("boom happened", text)
        self.assertEqual(language.violations(text), [])

    def test_zero_trades_reports_as_a_result_with_disclaimer(self):
        r = self._run(entry_rules=["close > 1e12"])
        self.assertEqual(r["total_trades"], 0)
        self.assertNotIn("error", r)
        text = backtest.format_result(r)
        self.assertIn("No trades matched", text)
        self.assertIn(language.DISCLAIMER_BACKTEST, text)

    def test_sharpe_requires_multiple_trades_and_says_otherwise(self):
        r = self._run(_rising(60), entry_rules=ENTRY, exit_rules=EXIT)
        self.assertLessEqual(r["total_trades"], 1)
        self.assertIsNone(r["sharpe"])
        self.assertIn("Sharpe (per-trade, not annualised): n/a",
                      backtest.format_result(r))


class ParserTest(unittest.TestCase):
    @staticmethod
    def _arr(closes):
        bars = [{"t": i, "o": c - 1.0, "h": c + 1.0, "l": c - 1.0,
                 "c": c, "v": 10} for i, c in enumerate(closes)]
        return backtest._build_arrays({"bars": bars})

    def test_operand_resolves_columns_constants_and_calls(self):
        arr = self._arr([float(i) for i in range(40)])
        closes = backtest._operand("close", arr)
        self.assertAlmostEqual(float(closes[35]), 35.0)
        self.assertAlmostEqual(backtest._operand("3.5", arr), 3.5)
        ema = backtest._operand("ema(20)", arr)
        self.assertEqual(len(ema), 40)
        self.assertNotEqual(ema[35], arr["close"][35])   # it is an average
        rsi = backtest._operand("rsi(14)", arr)
        self.assertEqual(len(rsi), 40)
        with self.assertRaises(ValueError):
            backtest._operand("frobnicate", arr)

    def test_sma_window_is_backward_looking(self):
        closes = [float(i) for i in range(40)]
        arr = self._arr(closes)
        sma = backtest._operand("sma(20)", arr)
        import math
        self.assertTrue(math.isnan(sma[18]))                 # prefix is NaN-padded
        self.assertAlmostEqual(float(sma[19]), sum(closes[:20]) / 20)

    def test_rule_eval_respects_and_semantics(self):
        arr = self._arr([float(i) for i in range(40)])
        backtest._operand("ema(20)", arr)                    # populate cache
        ev = backtest._parse(["close > ema(20)"], arr)
        self.assertTrue(all(backtest._eval(c, 35) for c in ev))
        ev2 = backtest._parse(["close > ema(20)", "close > 1e12"], arr)
        # all() semantics — one false rule means no entry
        self.assertFalse(all(backtest._eval(c, 35) for c in ev2))

    def test_cross_requires_previous_bar_on_or_below(self):
        a, b = [1.0, 3.0], [2.0, 2.0]
        self.assertTrue(backtest._cross(a, b, 1))            # 1<=2, 3>2
        self.assertFalse(backtest._cross(a, b, 0))           # no previous bar
        c = [3.0, 4.0]
        self.assertFalse(backtest._cross(c, b, 1))           # already above

    def test_build_arrays_requires_ohlc_keys(self):
        with self.assertRaises(KeyError):
            backtest._build_arrays({"bars": [{"c": 1.0}]})


if __name__ == "__main__":
    unittest.main()
