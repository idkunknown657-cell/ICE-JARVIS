"""
Tool front-end contracts for all 11 trading tools (spec §20 wiring):
valid OBJECT schemas with ARRAY items, no collisions with reserved inline
tool names, handler signatures the loader accepts, descriptions free of
forbidden certainty language, and smoke runs of each handler with the
network fully patched or untouched.
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trading_env import TradingEnvMixin

from core.trading import language

TRADING_TOOLS = [
    "market_data", "market_analysis", "risk_calculator", "trade_order",
    "paper_trading", "trading_journal", "backtest_strategy", "trade_alerts",
    "trade_monitor", "chart_analysis", "trading_settings",
]

RESERVED_INLINE = {  # names owned by main.py TOOL_DECLARATIONS (must not collide)
    "save_memory", "system_status", "add_monitor", "undo", "camera",
    "screen_process", "close_camera", "manage_monitor", "shutdown_jarvis",
    "remove_monitor", "list_monitors",
}

_MODULES = {}


def _load():
    if not _MODULES:
        import importlib
        for name in TRADING_TOOLS:
            _MODULES[name] = importlib.import_module(f"actions.{name}")
    return _MODULES


class SchemaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mods = _load()

    def test_every_tool_exposes_a_tool_dict(self):
        for name, mod in self.mods.items():
            self.assertTrue(hasattr(mod, "TOOL"), name)
            tool = mod.TOOL
            self.assertEqual(tool["name"], name)
            self.assertTrue(tool["description"].strip())
            self.assertTrue(callable(tool["handler"]))

    def test_no_collisions_with_reserved_inline_tools(self):
        for name in TRADING_TOOLS:
            self.assertNotIn(name, RESERVED_INLINE, name)

    def test_parameters_are_objects_with_typed_properties(self):
        for name, mod in self.mods.items():
            params = mod.TOOL["parameters"]
            self.assertEqual(params["type"], "OBJECT", name)
            self.assertIsInstance(params.get("properties"), dict, name)
            self.assertIsInstance(params.get("required"), list, name)
            for pname, pspec in params["properties"].items():
                self.assertIn("type", pspec, f"{name}.{pname}")
                self.assertTrue(pspec.get("description", "").strip(),
                                f"{name}.{pname} missing description")

    def test_arrays_declare_items(self):
        for name, mod in self.mods.items():
            for pname, pspec in mod.TOOL["parameters"]["properties"].items():
                if pspec["type"] != "ARRAY":
                    continue
                item_type = pspec.get("items", {}).get("type")
                if pname == "targets":
                    self.assertEqual(item_type, "NUMBER",
                                     f"{name}.{pname} targets")
                else:
                    self.assertEqual(item_type, "STRING",
                                     f"{name}.{pname} ARRAY without string items")

    def test_descriptions_contain_no_forbidden_phrases(self):
        for name, mod in self.mods.items():
            desc = mod.TOOL["description"]
            self.assertEqual(language.violations(desc), [], name)
            # route examples required for §20 tool selection (§20 spec text
            # lives in descriptions, not in a separate index)
            self.assertGreater(len(desc), 60, name)

    def test_handler_signatures_accept_parameters_kwarg(self):
        import inspect
        for name, mod in self.mods.items():
            sig = inspect.signature(mod.TOOL["handler"])
            first = list(sig.parameters)[0]
            self.assertEqual(first, "parameters", name)


class LoaderWiringTest(unittest.TestCase):
    def test_all_11_discover_as_valid_actions(self):
        import main as M
        from core.action_loader import discover_actions
        reg = discover_actions(Path(__file__).resolve().parent.parent / "actions",
                               reserved_names={t["name"]
                                               for t in M.TOOL_DECLARATIONS},
                               logger=lambda m: None)
        for name in TRADING_TOOLS:
            self.assertIn(name, reg.names(), name)


class HandlerSmokeTest(TradingEnvMixin, unittest.TestCase):
    """Each handler returns a string, never raises, never hits the network."""

    @classmethod
    def setUpClass(cls):
        cls.mods = _load()

    def test_market_data_unknown_kind_lists_options(self):
        out = self.mods["market_data"].market_data({"symbol": "TEST",
                                                    "data": "telepathy"})
        self.assertIn("unknown data", out)
        self.assertIn("quote, candles, news, earnings, calendar", out)

    def test_market_data_quote_reports_honestly_when_feed_down(self):
        from core.trading import data as tdata
        with mock.patch.object(tdata, "_http_get",
                               side_effect=tdata.DataError("HTTP 500")):
            out = self.mods["market_data"].market_data({"symbol": "TEST"})
        self.assertIn("Market data unavailable", out)
        self.assertIn("HTTP 500", out)

    def test_market_data_news_failure_never_invents(self):
        from core.trading import data as tdata
        with mock.patch.object(tdata, "fetch_news",
                               return_value=[]):
            out = self.mods["market_data"].market_data({"symbol": "TEST",
                                                        "data": "news",
                                                        "query": "TEST news"})
        self.assertIn("No recent headlines", out)
        self.assertIn("instead of inventing news", out)

    def test_market_analysis_rejects_unknown_timeframe_before_fetch(self):
        out = self.mods["market_analysis"].market_analysis(
            {"symbol": "TEST", "timeframes": "1D,7h"})
        self.assertIn("Unknown timeframe '7h'", out)
        self.assertIn("Supported:", out)

    def test_market_analysis_requires_symbol(self):
        out = self.mods["market_analysis"].market_analysis({})
        self.assertIn("symbol is required", out)

    def test_market_analysis_full_is_scenario_language(self):
        from core.trading import analysis as an
        from core.trading import data as tdata

        def _candles(sym, tf="1D", period=None):
            bars = [{"t": 1700000000 + i * 3600, "o": 99.7 + i * .5,
                     "h": 101.5 + i * .5, "l": 98.2 + i * .5,
                     "c": 100.4 + i * .5, "v": 1000} for i in range(90)]
            return {"symbol": sym, "timeframe": tf, "bars": bars, "meta": {},
                    "provider": "FakeFeed", "data_asof": bars[-1]["t"],
                    "delayed": False}

        quote = {"symbol": "TEST", "price": 145.4, "prev_close": 145.0,
                 "change": .4, "change_pct": .28, "open": 145.0,
                 "high": 146.0, "low": 144.5, "close": 145.4, "volume": 1,
                 "timeframe": "1D", "currency": "USD", "exchange": "X",
                 "name": "Test", "instrument": "EQUITY",
                 "provider": "FakeFeed", "data_asof": 1700000000,
                 "fetched_at": 1700000000, "delayed": False}
        with mock.patch.object(an, "get_quote", return_value=quote), \
             mock.patch.object(an, "get_candles", side_effect=_candles), \
             mock.patch.object(an, "fetch_news", return_value=[]):
            out = self.mods["market_analysis"].market_analysis(
                {"symbol": "TEST", "mode": "full"})
        self.assertIn("TRADE PLAN", out)
        self.assertIn("INVALIDATION", out)
        self.assertEqual(language.violations(out), [])
        self.assertIn("PAPER TRADING / ANALYSIS MODE", out)

    def test_risk_calculator_size_requires_entry_and_stop(self):
        out = self.mods["risk_calculator"].risk_calculator({"action": "size"})
        self.assertIn("entry and stop", out)
        out2 = self.mods["risk_calculator"].risk_calculator(
            {"action": "size", "account": 10000, "entry": 100})
        self.assertIn("entry and stop", out2)

    def test_risk_calculator_needs_account_or_config(self):
        out = self.mods["risk_calculator"].risk_calculator(
            {"action": "size", "entry": 100, "stop": 98})
        self.assertIn("Account size is not configured", out)

    def test_risk_calculator_size_works_and_shows_math(self):
        out = self.mods["risk_calculator"].risk_calculator(
            {"action": "size", "account": 10000, "risk_pct": 1,
             "entry": 100, "stop": 98})
        self.assertIn("Position size: 50 shares", out)
        self.assertIn("Maximum risk: $100.00", out)

    def test_risk_calculator_context_triggers_impulse_block(self):
        out = self.mods["risk_calculator"].risk_calculator(
            {"action": "size", "account": 10000, "risk_pct": 1,
             "entry": 100, "stop": 98, "context": "go all in"})
        self.assertIn("HIGH-RISK REQUEST", out)
        self.assertIn("Nothing has been executed", out)
        self.assertEqual(language.violations(out), [])

    def test_paper_trading_status_and_validation(self):
        out = self.mods["paper_trading"].paper_trading({"action": "status"})
        self.assertIn("PAPER TRADING", out)
        out2 = self.mods["paper_trading"].paper_trading({"action": "close"})
        self.assertIn("needs the symbol", out2)
        out3 = self.mods["paper_trading"].paper_trading(
            {"action": "open", "symbol": "", "side": "buy", "entry": 1})
        self.assertIn("refused", out3)

    def test_trading_journal_summary_and_record_validation(self):
        out = self.mods["trading_journal"].trading_journal({"action": "summary"})
        self.assertIn("No closed trades", out)
        self.assertIn(language.DISCLAIMER_JOURNAL, out)
        out2 = self.mods["trading_journal"].trading_journal({"action": "record"})
        self.assertIn("needs a symbol", out2)
        out3 = self.mods["trading_journal"].trading_journal(
            {"action": "list"})
        self.assertIn("No journal entries", out3)

    def test_backtest_strategy_validation_messages(self):
        out = self.mods["backtest_strategy"].backtest_strategy({})
        self.assertIn("symbol is required", out)
        out2 = self.mods["backtest_strategy"].backtest_strategy(
            {"symbol": "TST"})
        self.assertIn("entry_rules required", out2)
        with mock.patch("core.trading.data.get_candles",
                        side_effect=Exception("offline")):
            out3 = self.mods["backtest_strategy"].backtest_strategy(
                {"symbol": "TST", "entry_rules": ["close > ema(20)"],
                 "timeframe": "1D", "period": "1y"})
        self.assertIn("could not load", out3.lower())   # no network in tests

    def test_trade_alerts_list_empty_and_unknown_type(self):
        out = self.mods["trade_alerts"].trade_alerts({"action": "list"})
        self.assertIn("No trading alerts registered", out)
        out2 = self.mods["trade_alerts"].trade_alerts(
            {"action": "add", "type": "moon", "symbol": "T", "level": 1})
        self.assertIn("not registered", out2)
        out3 = self.mods["trade_alerts"].trade_alerts(
            {"action": "add", "type": "economic_event", "symbol": "T",
             "topic": "CPI"})
        self.assertIn("economic-calendar", out3)

    def test_trade_monitor_list_and_status(self):
        out = self.mods["trade_monitor"].trade_monitor({"action": "list"})
        self.assertIn("No trades being watched", out)
        out2 = self.mods["trade_monitor"].trade_monitor({"action": "status"})
        self.assertIn("needs a symbol", out2)
        out3 = self.mods["trade_monitor"].trade_monitor({"action": "watch"})
        self.assertIn("needs a symbol", out3)

    def test_chart_analysis_reports_unavailable_honestly(self):
        out = self.mods["chart_analysis"].chart_analysis(
            {"image_path": str(self.tmp / "nope.png")})
        self.assertTrue("unavailable" in out.lower() or "failed" in out.lower(),
                        out)

    def test_trading_settings_get_smoke(self):
        out = self.mods["trading_settings"].trading_settings({"action": "get"})
        self.assertIn("PAPER TRADING / ANALYSIS MODE", out)
        self.assertIn("risk_per_trade_pct", out)


if __name__ == "__main__":
    unittest.main()
