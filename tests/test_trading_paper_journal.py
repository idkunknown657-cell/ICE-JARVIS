"""
Paper trading (spec §13) and the journal (spec §14): P/L arithmetic, virtual
balance bookkeeping, mode labels that never blend, summary statistics
(win rate, drawdown, avg R:R), persistence, and the mandatory past≠future
disclaimer.
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trading_env import TradingEnvMixin

from core.trading import journal as tjournal
from core.trading import paper, language


class PaperAccountTest(TradingEnvMixin, unittest.TestCase):
    def test_fresh_account_is_paper_with_default_balance(self):
        st = paper.status()
        self.assertEqual(st["balance"], 10000.0)
        self.assertEqual(st["mode"], "paper")
        self.assertEqual(st["open_positions"], 0)
        self.assertEqual(st["closed_trades"], 0)

    def test_open_close_long_pnl_flows_to_balance(self):
        r = paper.open_position("TEST", "buy", entry=100, quantity=5,
                                stop=98, target=110, note="breakout")
        self.assertTrue(r["ok"], r)
        pos = r["position"]
        self.assertEqual(pos["id"], "P0001")
        self.assertEqual(pos["mode"], "paper")
        self.assertEqual(len(paper.positions()), 1)

        c = paper.close_position("TEST", exit_price=110)
        self.assertTrue(c["ok"], c)
        self.assertEqual(c["closed"]["pnl"], 50.0)         # (110-100) * 5
        self.assertAlmostEqual(c["closed"]["pnl_pct"], 10.0)
        self.assertEqual(c["closed"]["result"], "win")
        self.assertEqual(c["balance"], 10050.0)
        self.assertEqual(len(paper.positions()), 0)
        self.assertEqual(len(paper.history()), 1)

    def test_short_pnl_sign(self):
        paper.open_position("TEST", "sell", entry=100, quantity=5)
        c = paper.close_position("TEST", exit_price=90)
        self.assertEqual(c["closed"]["pnl"], 50.0)         # (90-100) * 5 * -1

    def test_loss_reduces_balance_and_marks_loss(self):
        paper.open_position("TEST", "buy", entry=100, quantity=10)
        c = paper.close_position("TEST", exit_price=98)
        self.assertEqual(c["closed"]["pnl"], -20.0)
        self.assertEqual(c["closed"]["result"], "loss")
        self.assertEqual(c["balance"], 9980.0)

    def test_close_is_journaled_as_mode_paper(self):
        paper.open_position("TEST", "buy", entry=100, quantity=5)
        paper.close_position("TEST", exit_price=110)
        rows = tjournal.trades(mode="paper")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["mode"], "paper")
        self.assertEqual(rows[0]["result"], "win")
        self.assertEqual(rows[0]["pnl"], 50.0)

    def test_close_unknown_position_is_an_honest_error(self):
        r = paper.close_position("GHOST", exit_price=5)
        self.assertFalse(r["ok"])
        self.assertIn("No open paper position", r["error"])

    def test_open_validates_inputs(self):
        self.assertFalse(paper.open_position("", "buy", 1, 1)["ok"])
        self.assertFalse(paper.open_position("T", "buy", -1, 1)["ok"])
        self.assertFalse(paper.open_position("T", "buy", 1, 0)["ok"])
        self.assertFalse(paper.open_position("T", "sideways", 1, 1)["ok"])
        self.assertFalse(paper.open_position("T", "buy", "x", 1)["ok"])

    def test_unrealized_is_pure_math(self):
        long_pos = {"entry": 100, "quantity": 5, "side": "long"}
        short_pos = {"entry": 100, "quantity": 5, "side": "short"}
        self.assertEqual(paper.unrealized(long_pos, 110)["pnl"], 50.0)
        self.assertEqual(paper.unrealized(short_pos, 110)["pnl"], -50.0)
        self.assertAlmostEqual(paper.unrealized(long_pos, 90)["pnl_pct"], -10.0)

    def test_status_text_is_unmistakably_paper(self):
        text = paper.format_status(paper.status())
        self.assertIn("PAPER TRADING — virtual money", text)
        self.assertIn("$10,000.00", text)
        self.assertIn("key=mode value=live", text)

    def test_state_persists_to_the_gitignored_file(self):
        paper.open_position("TEST", "buy", entry=100, quantity=5)
        path = self.tmp / "paper_account.json"
        self.assertTrue(path.exists())
        import json
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(len(data["positions"]), 1)


class JournalSummaryTest(TradingEnvMixin, unittest.TestCase):
    def _seed(self):
        tjournal.record(mode="paper", asset="AAA", direction="buy", entry=100,
                        exit=110, quantity=1, pnl=100, rr_planned=2)
        tjournal.record(mode="paper", asset="BBB", direction="sell", entry=50,
                        exit=48, quantity=1, pnl=-50, rr_planned=1)
        tjournal.record(mode="paper", asset="CCC", direction="buy", entry=10,
                        exit=12, quantity=1, pnl=50, rr_planned=3)

    def test_stats_block_matches_the_spec_list(self):
        self._seed()
        d = tjournal.summary()
        self.assertEqual(d["total_trades"], 3)
        self.assertEqual(d["winning_trades"], 2)
        self.assertEqual(d["losing_trades"], 1)
        self.assertAlmostEqual(d["win_rate"], 66.666, places=1)
        self.assertEqual(d["avg_win"], 75.0)
        self.assertEqual(d["avg_loss"], -50.0)
        self.assertEqual(d["net_pnl"], 100.0)
        self.assertEqual(d["max_drawdown"], 50.0)     # +100 peak, dip to +50
        self.assertEqual(d["avg_rr"], 2.0)

    def test_formatted_summary_shows_figures_and_disclaimer(self):
        self._seed()
        text = tjournal.format_summary(tjournal.summary())
        for needle in ("Total trades: 3", "Winning trades: 2", "Losing trades: 1",
                       "Win rate: 66.7%", "Average win: $75.00",
                       "Average loss: -$50.00", "Maximum drawdown: $50.00",
                       "Average risk/reward (planned): 1:2.00",
                       "Total P/L: $100.00"):
            self.assertIn(needle, text)
        self.assertIn(language.DISCLAIMER_JOURNAL, text)
        self.assertEqual(language.violations(text), [])

    def test_empty_summary_still_carries_the_disclaimer(self):
        text = tjournal.format_summary(tjournal.summary())
        self.assertIn("No closed trades match", text)
        self.assertIn(language.DISCLAIMER_JOURNAL, text)

    def test_paper_and_live_never_blend(self):
        self._seed()
        tjournal.record(mode="live", asset="LIVE", direction="buy", entry=1,
                        exit=2, quantity=1, pnl=999)
        tjournal.record(mode="paper", asset="OPEN", direction="buy", entry=1)  # open
        self.assertEqual(tjournal.summary()["total_trades"], 4)      # 3 paper + 1 live
        self.assertEqual(tjournal.summary()["open_trades"], 1)
        self.assertEqual(tjournal.summary(mode="paper")["total_trades"], 3)
        self.assertEqual(tjournal.summary(mode="live")["total_trades"], 1)
        self.assertEqual(tjournal.summary(mode="live")["net_pnl"], 999.0)

    def test_month_and_date_filters(self):
        from datetime import datetime
        this_month = datetime.now().strftime("%Y-%m")
        self._seed()
        self.assertEqual(tjournal.summary(month=this_month)["total_trades"], 3)
        self.assertEqual(tjournal.summary(month="1999-01")["total_trades"], 0)
        today = datetime.now().strftime("%Y-%m-%d")
        self.assertEqual(tjournal.summary(since=today)["total_trades"], 3)
        self.assertEqual(tjournal.summary(until="2000-01-01")["total_trades"], 0)

    def test_stats_today_feeds_guardrails(self):
        self.assertEqual(tjournal.stats_today(), {"trades": 0, "pnl": 0.0})
        tjournal.record(asset="X", direction="buy", entry=1, exit=2,
                        quantity=1, pnl=-30)
        tjournal.record(asset="Y", direction="buy", entry=1)   # open → excluded
        self.assertEqual(tjournal.stats_today(), {"trades": 1, "pnl": -30.0})

    def test_record_never_raises_even_if_storage_fails(self):
        with mock.patch.object(tjournal, "_save", side_effect=RuntimeError("disk full")):
            rec = tjournal.record(asset="X", direction="buy", entry=1, exit=2)
        self.assertIn("disk full", rec.get("error", ""))

    def test_record_roundtrip_persists(self):
        tjournal.record(mode="live", asset="XYZ", direction="sell", entry=5,
                        exit=4, quantity=2, pnl=2, reason="setup broke")
        import json
        data = json.loads((self.tmp / "trading_journal.json")
                          .read_text(encoding="utf-8"))
        self.assertEqual(data["trades"][0]["asset"], "XYZ")
        self.assertEqual(data["trades"][0]["reason"], "setup broke")

    def test_list_filter_by_asset_and_include_open(self):
        tjournal.record(asset="AAA", direction="buy", entry=1, exit=2, pnl=1)
        tjournal.record(asset="BBB", direction="buy", entry=1)
        self.assertEqual(len(tjournal.trades(asset="AAA")), 1)
        self.assertEqual(len(tjournal.trades()), 1)                    # open hidden
        self.assertEqual(len(tjournal.trades(include_open=True)), 2)


if __name__ == "__main__":
    unittest.main()
