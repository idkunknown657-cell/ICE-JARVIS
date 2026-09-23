"""
Order workflow (spec §12/§18/§19): prepare→show→confirm→submit→verify→journal.
The critical guarantees under test:
  * default mode is PAPER and paper tickets never touch core/confirm;
  * LIVE preparation is blocked while risk config is incomplete;
  * LIVE submit reaches execution only through core.confirm.request's run
    callback (the HUD CONFIRM button) — mocked here as the user's press;
  * unconfigured platform automation refuses honestly, records nothing;
  * impulse-flagged orders force the gate even for paper;
  * tickets expire; nothing silently executes.
"""
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trading_env import TradingEnvMixin

from core.trading import journal as tjournal
from core.trading import language, orderflow, paper, settings as tsettings


_PENDING_MSG = "[CONFIRMATION_PENDING] waiting for on-screen confirm"


class PrepareTest(TradingEnvMixin, unittest.TestCase):
    def test_paper_ticket_shows_everything_and_needs_no_gate(self):
        r = orderflow.prepare("TEST", "buy", entry=100, stop=98, target=106,
                              quantity=5, reason="breakout above range")
        self.assertTrue(r["ok"], r)
        text = r["text"]
        self.assertIn("POSSIBLE SETUP — ORDER TICKET", text)
        self.assertIn("Symbol: TEST   Side: LONG", text)
        self.assertIn("Entry: 100", text)
        self.assertIn("Invalidation (stop): 98", text)
        self.assertIn("Target: 106", text)
        self.assertIn("Risk/Reward: 1:3", text)
        self.assertIn("Mode: PAPER", text)
        self.assertFalse(r["order"]["confirmation_required"])
        self.assertIn("paper ticket filled", text)
        self.assertEqual(language.violations(text), [])

    def test_prepare_never_submits(self):
        orderflow.prepare("TEST", "buy", entry=100, stop=98, target=106,
                          quantity=5)
        self.assertEqual(len(paper.positions()), 0)
        self.assertEqual(len(tjournal.trades()), 0)

    def test_auto_size_from_configured_risk_shows_sizing_math(self):
        tsettings.set_value("account_size", 10000)
        r = orderflow.prepare("TEST", "buy", entry=100, stop=98, target=106)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["order"]["quantity"], 50.0)      # §5 example
        self.assertIn("POSITION SIZING", r["text"])
        self.assertIn("Position size: 50 shares", r["text"])

    def test_prepare_requires_symbol_and_valid_side(self):
        self.assertFalse(orderflow.prepare("", "buy")["ok"])
        self.assertFalse(orderflow.prepare("TEST", "sideways")["ok"])

    def test_live_without_account_blocks_with_guardrail_report(self):
        tsettings.set_value("mode", "live")
        r = orderflow.prepare("TEST", "buy", entry=100, stop=98, target=106,
                              quantity=5)
        self.assertFalse(r["ok"])
        self.assertTrue(r.get("blocked"))
        self.assertIn("RISK GUARDRAILS", r["error"])
        self.assertIn("Account size is not configured", r["error"])
        self.assertEqual(orderflow.status()["prepared"], [])

    def test_live_without_stop_blocks(self):
        tsettings.set_value("mode", "live")
        tsettings.set_value("account_size", 10000)
        r = orderflow.prepare("TEST", "buy", entry=100, target=106, quantity=5)
        self.assertFalse(r["ok"])
        self.assertIn("No stop-loss provided", r["error"])

    def test_live_ready_ticket_requires_confirmation(self):
        tsettings.set_value("mode", "live")
        tsettings.set_value("account_size", 10000)
        r = orderflow.prepare("TEST", "buy", entry=100, stop=98, target=106,
                              quantity=5)
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["order"]["confirmation_required"])
        self.assertIn("CONFIRMATION NEEDED before submit: (LIVE", r["text"])

    def test_impulsive_request_forces_gate_even_on_paper(self):
        r = orderflow.prepare("TEST", "buy", entry=100, stop=98, target=106,
                              quantity=5, context="just go all in on this")
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["order"]["confirmation_required"])
        self.assertIn("HIGH-RISK FLAG", r["text"])
        self.assertIn("committing the entire balance", r["text"])
        self.assertIn("Nothing has been executed", r["text"])
        self.assertEqual(language.violations(r["text"]), [])


class SubmitTest(TradingEnvMixin, unittest.TestCase):
    def test_paper_submit_fills_verifies_and_journals_without_gate(self):
        prep = orderflow.prepare("TEST", "buy", entry=100, stop=98,
                                 target=106, quantity=5)
        oid = prep["order"]["id"]
        with mock.patch("core.confirm.request") as gate:
            r = orderflow.submit(oid)
        gate.assert_not_called()                       # paper skips the HUD
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["result"]["verified"])
        self.assertIn("PAPER order filled", r["result"]["text"])
        self.assertIn("VERIFY:", r["result"]["text"])
        self.assertIn("virtual money", r["result"]["text"])
        self.assertEqual(len(paper.positions()), 1)
        rows = tjournal.trades(include_open=True, mode="paper")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["mode"], "paper")
        # ticket consumed
        self.assertIsNone(orderflow.get(oid))

    def test_submit_without_id_explains_the_required_sequence(self):
        r = orderflow.submit("")
        self.assertFalse(r["ok"])
        self.assertIn("No prepared order", r["error"])
        self.assertIn("action=prepare", r["error"])

    def test_unknown_id_is_not_executed(self):
        r = orderflow.submit("ORD000000000")
        self.assertFalse(r["ok"])
        self.assertEqual(len(paper.positions()), 0)


class LiveGateTest(TradingEnvMixin, unittest.TestCase):
    def _live_prep(self, **kw):
        tsettings.set_value("mode", "live")
        tsettings.set_value("account_size", 10000)
        kw.setdefault("quantity", 5)
        r = orderflow.prepare("TEST", "buy", entry=100, stop=98,
                              target=106, **kw)
        self.assertTrue(r["ok"], r)
        return r["order"]

    def test_live_submit_waits_for_hud_confirm_and_runs_nothing_before(self):
        order = self._live_prep()
        executed = []

        def fake_request(key, title, detail, run):
            executed.append(run())                     # user pressed CONFIRM
            return _PENDING_MSG

        with mock.patch("core.confirm.request", side_effect=fake_request) as gate:
            r = orderflow.submit(order["id"])
        self.assertEqual(gate.call_count, 1)
        kwargs = gate.call_args.kwargs
        self.assertIn("Submit LIVE", kwargs["title"])
        self.assertIn("LIVE", kwargs["detail"])
        self.assertEqual(r["awaiting_confirmation"], True)
        self.assertIn("[CONFIRMATION_PENDING]", r["text"])
        self.assertEqual(len(executed), 1)
        # unconfigured automation → honest refusal, zero journal records
        self.assertIn("LIVE SUBMISSION NOT PERFORMED", executed[0]["text"])
        self.assertIn("platform_app", executed[0]["text"])
        self.assertIn("order_steps", executed[0]["text"])
        self.assertIn("Nothing was submitted", executed[0]["text"])
        self.assertEqual(tjournal.trades(mode="live"), [])
        self.assertEqual(len(paper.positions()), 0)

    def test_declined_confirmation_executes_nothing(self):
        order = self._live_prep()
        with mock.patch("core.confirm.request",
                        return_value="Confirmation declined — nothing submitted"):
            with mock.patch.object(orderflow, "_execute") as ex:
                r = orderflow.submit(order["id"])
        ex.assert_not_called()
        self.assertFalse(r["ok"])
        self.assertIn("declined", r["error"].lower())
        self.assertEqual(len(paper.positions()), 0)

    def test_pending_confirmation_ui_reports_wait_not_executes(self):
        order = self._live_prep()
        with mock.patch("core.confirm.request", return_value=_PENDING_MSG), \
             mock.patch.object(orderflow, "_execute") as ex:
            r = orderflow.submit(order["id"])
        ex.assert_not_called()
        self.assertTrue(r["ok"])
        self.assertTrue(r["awaiting_confirmation"])
        self.assertEqual(orderflow.get(order["id"])["status"],
                         "awaiting_confirmation")

    def test_impulse_paper_order_also_goes_through_the_gate(self):
        prep = orderflow.prepare("TEST", "buy", entry=100, stop=98,
                                 target=106, quantity=5,
                                 context="double my position now")
        oid = prep["order"]["id"]
        with mock.patch("core.confirm.request",
                        return_value="Confirmation declined"), \
             mock.patch.object(orderflow, "_execute") as ex:
            r = orderflow.submit(oid)
        ex.assert_not_called()
        self.assertFalse(r["ok"])
        self.assertEqual(len(paper.positions()), 0)


class StatusCancelTest(TradingEnvMixin, unittest.TestCase):
    def test_status_lists_prepared_tickets(self):
        prep = orderflow.prepare("TEST", "buy", entry=100, stop=98,
                                 target=106, quantity=5)
        st = orderflow.status()
        self.assertEqual(len(st["prepared"]), 1)
        self.assertEqual(st["prepared"][0]["id"], prep["order"]["id"])
        one = orderflow.status(prep["order"]["id"])
        self.assertTrue(one["ok"])
        self.assertFalse(orderflow.status("ORD404")["ok"])

    def test_cancel_removes_the_ticket(self):
        prep = orderflow.prepare("TEST", "buy", entry=100, stop=98,
                                 target=106, quantity=5)
        oid = prep["order"]["id"]
        c = orderflow.cancel(oid)
        self.assertTrue(c["ok"])
        self.assertIn("cancelled", c["text"])
        self.assertIsNone(orderflow.get(oid))
        self.assertFalse(orderflow.cancel(oid)["ok"])

    def test_format_prepared_empty_explains_how_to_prepare(self):
        text = orderflow.format_prepared([])
        self.assertIn("No prepared orders", text)
        self.assertIn("action=prepare", text)

    def test_tickets_expire_after_ten_minutes(self):
        prep = orderflow.prepare("TEST", "buy", entry=100, stop=98,
                                 target=106, quantity=5)
        oid = prep["order"]["id"]
        orderflow.get(oid)["created"] = time.time() - 700
        orderflow.status()                              # triggers prune
        self.assertIsNone(orderflow.get(oid))
        r = orderflow.submit(oid)
        self.assertFalse(r["ok"])
        self.assertIn("expire after", r["error"])


class ActionHandlerTest(TradingEnvMixin, unittest.TestCase):
    """The actions/trade_order.py front-end wiring."""

    def test_handler_prepare_submit_status_cancel(self):
        from actions.trade_order import trade_order
        out = trade_order({"action": "prepare", "symbol": "TEST", "side": "buy",
                           "entry": 100, "stop": 98, "target": 106,
                           "quantity": 5})
        self.assertIn("POSSIBLE SETUP", out)
        oid = orderflow.status()["prepared"][0]["id"]
        out2 = trade_order({"action": "status"})
        self.assertIn(oid, out2)
        out3 = trade_order({"action": "submit", "order_id": oid})
        self.assertIn("PAPER order filled", out3)
        out4 = trade_order({"action": "prepare", "symbol": "TEST2",
                            "side": "sell", "entry": 50, "stop": 51,
                            "quantity": 2})
        self.assertIn("POSSIBLE SETUP", out4)
        oid2 = [o for o in orderflow.status()["prepared"]
                if o["symbol"] == "TEST2"][0]["id"]
        out5 = trade_order({"action": "cancel", "order_id": oid2})
        self.assertIn("cancelled", out5)

    def test_handler_submit_without_id_points_at_prepare(self):
        from actions.trade_order import trade_order
        out = trade_order({"action": "submit"})
        self.assertIn("prepare", out)
        self.assertIn("confirm", out)


if __name__ == "__main__":
    unittest.main()
