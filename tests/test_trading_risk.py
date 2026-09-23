"""
Risk engine (spec §5/§17/§18/§19): the worked §5 sizing example verbatim,
guardrail violations that stop trades, impulse detection answered with calm
arithmetic, proof that no path raises a configured limit, and the
trading_settings handler wiring.
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trading_env import TradingEnvMixin

from core.trading import journal as tjournal
from core.trading import risk, settings as tsettings
from actions.trading_settings import trading_settings


class PositionSizeTest(unittest.TestCase):
    def test_spec_example_is_exact(self):
        # Account $10,000 · risk 1% → $100 · entry $100 · stop $98 → $2/share
        # → position size 50 shares (§5's worked example).
        r = risk.position_size(10000, 1, 100, 98)
        self.assertTrue(r["ok"])
        self.assertEqual(r["budget"], 100.0)
        self.assertEqual(r["dist"], 2.0)
        self.assertEqual(r["shares"], 50.0)
        self.assertEqual(r["risk_at_size"], 100.0)

    def test_format_size_shows_every_line_of_the_calculation(self):
        text = risk.format_size(risk.position_size(10000, 1, 100, 98))
        for line in ("Account: $10,000", "Risk: 1%", "Maximum risk: $100.00",
                     "Entry: 100", "Stop: 98", "Risk per unit: $2",
                     "Position size: 50 shares"):
            self.assertIn(line, text)

    def test_risk_at_size_shows_when_rounding_down(self):
        text = risk.format_size(risk.position_size(10000, 1, 100, 99))
        # $100 budget / $1 distance would allow 100, floor stays exact — use a
        # distance that forces rounding: $100 / $3 → 33 units → $99 risk.
        r = risk.position_size(10000, 1, 100, 97)
        self.assertEqual(r["shares"], 33.0)
        self.assertIn("rounding down keeps you inside", risk.format_size(r))

    def test_stop_equals_entry_is_rejected(self):
        r = risk.position_size(10000, 1, 100, 100)
        self.assertFalse(r["ok"])
        self.assertIn("undefined", r["error"])

    def test_invalid_inputs_reported_precisely(self):
        self.assertIn("positive", risk.position_size(0, 1, 100, 98)["error"])
        self.assertIn("greater than 0", risk.position_size(10000, 0, 100, 98)["error"])
        self.assertIn("positive prices", risk.position_size(10000, 1, 0, 98)["error"])
        self.assertIn("must all be numbers", risk.position_size("x", 1, 100, 98)["error"])

    def test_fractional_sizes_allowed_for_small_units(self):
        r = risk.position_size(100, 1, 100000, 99000)   # budget $1 / dist $1000
        self.assertTrue(r["ok"])
        self.assertAlmostEqual(r["shares"], 0.001, places=6)

    def test_budget_too_small_for_any_unit_fails(self):
        r = risk.position_size(0.01, 1, 100000001, 1)   # budget $0.0001 / dist 1e8
        self.assertFalse(r["ok"])
        self.assertIn("smaller than", r["error"])


class GuardrailTest(TradingEnvMixin, unittest.TestCase):
    def test_configured_trade_passes(self):
        tsettings.set_value("account_size", 10000)
        r = risk.check_trade("TEST", "buy", 100, 98, 50, target=106)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["side"], "long")
        self.assertIn("risk_budget", r["checks"])
        self.assertIn("$100.00", r["checks"]["risk_budget"])
        self.assertIn("planned_loss", r["checks"])
        self.assertIn("risk_reward", r["checks"])
        self.assertEqual(r["violations"], [])

    def test_over_budget_stops_the_trade_with_arithmetic(self):
        tsettings.set_value("account_size", 10000)
        r = risk.check_trade("TEST", "buy", 100, 98, 60, target=106)
        self.assertFalse(r["ok"])
        joined = " ".join(r["violations"])
        self.assertIn("above your 1% budget of $100.00", joined)
        self.assertIn("Maximum size", joined)

    def test_missing_stop_is_a_violation_for_live(self):
        tsettings.set_value("account_size", 10000)
        r = risk.check_trade("TEST", "buy", 100, None, 50)
        self.assertFalse(r["ok"])
        self.assertIn("No stop-loss provided", " ".join(r["violations"]))

    def test_missing_account_blocks_live_with_honest_reason(self):
        r = risk.check_trade("TEST", "buy", 100, 98, 50)
        self.assertFalse(r["ok"])
        self.assertIn("Account size is not configured", " ".join(r["violations"]))

    def test_leverage_cap_violation(self):
        tsettings.set_value("account_size", 10000)     # max_leverage default 1.0
        r = risk.check_trade("TEST", "buy", 100, 98, 300)
        self.assertFalse(r["ok"])
        self.assertIn("leverage cap", " ".join(r["violations"]))

    def test_daily_loss_limit_from_journal_stops_new_trade(self):
        tsettings.set_value("account_size", 10000)     # 3% daily cap = -$300
        tjournal.record(asset="OLD", direction="sell", entry=1, exit=2,
                        quantity=1, pnl=-350)
        r = risk.check_trade("TEST", "buy", 100, 98, 50)
        self.assertFalse(r["ok"])
        self.assertIn("Daily loss limit reached", " ".join(r["violations"]))
        self.assertIn("will not bypass it", " ".join(r["violations"]))

    def test_daily_trade_cap_from_journal(self):
        tsettings.set_value("account_size", 10000)
        tsettings.set_value("max_trades_per_day", 1)
        tjournal.record(asset="EARLY", direction="buy", entry=1, exit=2,
                        quantity=1, pnl=10)
        r = risk.check_trade("TEST", "buy", 100, 98, 50)
        self.assertFalse(r["ok"])
        self.assertIn("Daily trade cap reached: 1/1", " ".join(r["violations"]))

    def test_same_symbol_and_correlated_exposure_warn(self):
        tsettings.set_value("account_size", 10000)
        r = risk.check_trade("BTC-USD", "buy", 100, 98, 10,
                             existing_positions=[{"symbol": "BTC-USD", "quantity": 5},
                                                 {"symbol": "ETH-USD", "quantity": 2}])
        self.assertTrue(r["ok"])
        joined = " ".join(r["warnings"])
        self.assertIn("already hold", joined)
        self.assertIn("correlated", joined)

    def test_crypto_cluster_match_for_dollar_pairs(self):
        self.assertEqual(risk._cluster("BTC-USD"), "crypto")
        self.assertEqual(risk._cluster("ETH-USD"), "crypto")
        self.assertEqual(risk._cluster("TEST"), None)

    def test_rr_below_preference_is_a_warning_not_a_block(self):
        tsettings.set_value("account_size", 10000)
        r = risk.check_trade("TEST", "buy", 100, 98, 10, target=101)  # 1:0.5
        self.assertTrue(r["ok"])
        self.assertIn("below your 1:1.5 preference", " ".join(r["warnings"]))

    def test_format_check_reads_as_a_gate(self):
        tsettings.set_value("account_size", 10000)
        ok = risk.format_check(risk.check_trade("TEST", "buy", 100, 98, 50))
        self.assertIn("within your configured limits", ok)
        tsettings.set_value("account_size", 0.0001)    # budget far too small
        bad = risk.format_check(risk.check_trade("TEST", "buy", 100, 98, 50))
        self.assertIn("STOP:", bad)
        self.assertIn("BLOCKED", bad)
        self.assertIn("Nothing will be prepared or submitted", bad)


class ImpulseProtectionTest(TradingEnvMixin, unittest.TestCase):
    def test_impulse_phrases_detected(self):
        self.assertIn("committing the entire balance",
                      risk.impulse_flags("just go all in on this"))
        self.assertIn("doubling exposure",
                      risk.impulse_flags("double my position right now"))
        self.assertIn("revenge trading",
                      risk.impulse_flags("I lost money, make it back"))
        self.assertIn("maximum leverage", risk.impulse_flags("use maximum leverage"))
        self.assertEqual(risk.impulse_flags("analyze the 4h chart please"), [])

    def test_impulse_response_shows_numbers_and_requires_confirmation(self):
        text = risk.impulse_response(["committing the entire balance"],
                                     account=10000, risk_pct=1)
        self.assertIn("HIGH-RISK REQUEST", text)
        self.assertIn("$100.00", text)              # 1% of 10,000
        self.assertIn("100×", text)                 # all-in vs the rule
        self.assertIn("explicit confirmation", text)
        self.assertIn("Nothing has been executed", text)
        self.assertIn("will not raise them for you", text)
        self.assertEqual(
            [w for w in ("stupid", "foolish", "greedy", "idiot", "ridiculous",
                         "why would you", "don't be so")
             if w in text.lower()], [],
            "impulse reply must never shame the user (§19)")

    def test_impulse_response_without_account_still_works(self):
        text = risk.impulse_response(["doubling exposure"], account=None, risk_pct=1)
        self.assertIn("100%", text)                 # all-in as a share of balance
        self.assertIn("100×", text)

    def test_impulse_response_never_contains_certainty_language(self):
        from core.trading import language
        text = risk.impulse_response(["maximum leverage"], account=5000, risk_pct=2)
        self.assertEqual(language.violations(text), [])


class LimitsNeverAutoRaisedTest(TradingEnvMixin, unittest.TestCase):
    def test_nothing_but_settings_writes_risk_limits(self):
        tsettings.set_value("account_size", 10000)
        tsettings.set_value("risk_per_trade_pct", 0.5)
        path = self.tmp / "trading_settings.json"
        before = path.read_text(encoding="utf-8")

        risk.check_trade("TEST", "buy", 100, 98, 1000)         # runs guardrails
        risk.impulse_response(["committing the entire balance"], account=None)
        risk.format_check(risk.check_trade("TEST", "sell", 1, 2, 5))

        after = path.read_text(encoding="utf-8")
        self.assertEqual(before, after, "risk config changed outside settings tool")
        self.assertEqual(tsettings.get("risk_per_trade_pct"), 0.5)

    def test_fresh_defaults_are_conservative_paper_mode(self):
        self.assertEqual(tsettings.get("mode"), "paper")
        self.assertFalse(tsettings.is_live())
        self.assertEqual(tsettings.mode_label(), "PAPER TRADING / ANALYSIS MODE")
        self.assertEqual(tsettings.get("risk_per_trade_pct"), 1.0)
        self.assertEqual(tsettings.get("max_leverage"), 1.0)


class SettingsHandlerTest(TradingEnvMixin, unittest.TestCase):
    def test_get_prints_effective_config_with_mode_banner(self):
        out = trading_settings({"action": "get"})
        self.assertIn("PAPER TRADING / ANALYSIS MODE", out)
        self.assertIn("account_size", out)
        self.assertIn("provider", out)

    def test_set_returns_old_to_new(self):
        out = trading_settings({"action": "set", "key": "account_size",
                                "value": "10000"})
        self.assertIn("account_size", out)
        self.assertIn("10000.0", out)
        self.assertEqual(tsettings.get("account_size"), 10000.0)

    def test_raising_risk_is_called_out_as_explicit_choice(self):
        tsettings.set_value("risk_per_trade_pct", 1)
        out = trading_settings({"action": "set", "key": "risk_per_trade_pct",
                                "value": "3"})
        self.assertIn("Recorded as your explicit choice", out)
        self.assertIn("will not change it again", out)
        self.assertEqual(tsettings.get("risk_per_trade_pct"), 3.0)

    def test_switching_to_live_states_what_it_enables(self):
        out = trading_settings({"action": "set", "key": "mode", "value": "live"})
        self.assertIn("LIVE TRADING MODE enabled", out)
        self.assertIn("on-screen confirmation", out)
        self.assertTrue(tsettings.is_live())

    def test_switching_back_to_paper(self):
        tsettings.set_value("mode", "live")
        out = trading_settings({"action": "set", "key": "mode", "value": "paper"})
        self.assertIn("PAPER TRADING / ANALYSIS MODE", out)
        self.assertFalse(tsettings.is_live())

    def test_invalid_mode_rejected_with_reason(self):
        out = trading_settings({"action": "set", "key": "mode", "value": "yolo-mode"})
        self.assertIn("mode must be 'paper' or 'live'", out)
        self.assertEqual(tsettings.get("mode"), "paper")

    def test_unknown_key_lists_valid_keys(self):
        out = trading_settings({"action": "set", "key": "moon_budget", "value": "1"})
        self.assertIn("Unknown setting 'moon_budget'", out)
        self.assertIn("risk_per_trade_pct", out)

    def test_zero_risk_rejected(self):
        out = trading_settings({"action": "set", "key": "risk_per_trade_pct",
                                "value": "0"})
        self.assertIn("must be greater than 0", out)

    def test_numeric_validation(self):
        out = trading_settings({"action": "set", "key": "account_size",
                                "value": "lots"})
        self.assertIn("must be a number", out)

    def test_set_without_key_explains(self):
        out = trading_settings({"action": "set"})
        self.assertIn("needs key=", out)


if __name__ == "__main__":
    unittest.main()
