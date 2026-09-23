"""
Alert engine + position watching (spec §9/§10): type registry, one-shot
firing, watch auto-registration of stop/target alerts, honest refusals
(economic calendars), P/L status math, and the drain queue used by the
main-loop delivery task. Worker disabled via configure(False) in setUp;
all network through patched core.trading.data functions.
"""
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trading_env import TradingEnvMixin

from core.trading import alerts, data as tdata


def _quote(price=105.0, delayed=False):
    return {"symbol": "TEST", "price": price, "prev_close": price,
            "change": 0.0, "change_pct": 0.0, "provider": "FakeFeed",
            "data_asof": time.time(), "fetched_at": time.time(),
            "delayed": delayed}


def _candles(bars):
    return {"symbol": "TEST", "timeframe": "15m", "bars": bars,
            "provider": "FakeFeed", "data_asof": bars[-1]["t"],
            "delayed": False}


def _bar(c, i, v=100):
    return {"t": 1700000000 + i * 900, "o": c, "h": c + 1, "l": c - 1,
            "c": c, "v": v}


class RegistryTest(TradingEnvMixin, unittest.TestCase):
    def test_add_requires_symbol(self):
        r = alerts.add(kind="price_above", symbol="", level=110)
        self.assertFalse(r["ok"])
        self.assertIn("symbol", r["error"])

    def test_unknown_type_lists_supported(self):
        r = alerts.add(kind="moon_phase", symbol="TEST", level=1)
        self.assertFalse(r["ok"])
        self.assertIn("price_above", r["error"])

    def test_economic_event_honestly_refused_until_provider_exists(self):
        r = alerts.add(kind="economic_event", symbol="TEST", topic="CPI")
        self.assertFalse(r["ok"])
        self.assertIn("economic-calendar", r["error"])

    def test_news_requires_topic(self):
        r = alerts.add(kind="news", symbol="TEST")
        self.assertFalse(r["ok"])
        self.assertIn("topic", r["error"])

    def test_list_format_empty_and_populated(self):
        self.assertIn("No trading alerts registered", alerts.format_alerts([]))
        r = alerts.add(kind="price_above", symbol="TEST", level=110)
        text = alerts.format_alerts([r["alert"]])
        self.assertIn(r["alert"]["id"], text)
        self.assertIn("price_above", text)
        self.assertIn("TEST", text)
        self.assertIn("(fires once)", text)

    def test_remove_existing_and_missing(self):
        r = alerts.add(kind="price_above", symbol="TEST", level=110)
        ok = alerts.remove(r["alert"]["id"])
        self.assertTrue(ok["ok"])
        self.assertEqual(len(alerts.list_alerts()), 0)
        bad = alerts.remove("A9999")
        self.assertFalse(bad["ok"])

    def test_duplicate_add_creates_independent_alerts(self):
        a = alerts.add(kind="price_above", symbol="TEST", level=110)["alert"]
        b = alerts.add(kind="price_above", symbol="TEST", level=110)["alert"]
        self.assertNotEqual(a["id"], b["id"])
        self.assertEqual(len(alerts.list_alerts()), 2)


class PriceAlertTest(TradingEnvMixin, unittest.TestCase):
    def _add(self, **kw):
        defaults = {"kind": "price_above", "symbol": "TEST", "level": 110}
        defaults.update(kw)
        r = alerts.add(**defaults)
        self.assertTrue(r["ok"], r)
        return r["alert"]

    def test_not_yet_crossed_stays_silent(self):
        self._add()
        with mock.patch.object(tdata, "get_quote", return_value=_quote(105)):
            fired = alerts.evaluate_once()
        self.assertEqual(fired, [])
        self.assertEqual(len(alerts.list_alerts()), 1)

    def test_price_above_fires_once_with_provider_and_is_removed(self):
        self._add()
        with mock.patch.object(tdata, "get_quote", return_value=_quote(112)):
            fired = alerts.evaluate_once()
        self.assertEqual(len(fired), 1)
        self.assertIn("[TRADE_ALERT]", fired[0])
        self.assertIn("TEST", fired[0])
        self.assertIn("crossed ABOVE 110", fired[0])
        self.assertIn("data from FakeFeed", fired[0])   # §1 provenance on fire
        self.assertEqual(len(alerts.list_alerts()), 0)  # one-shot (§9)

    def test_recurring_alert_stays_registered(self):
        self._add(once=False)
        with mock.patch.object(tdata, "get_quote", return_value=_quote(112)):
            first = alerts.evaluate_once()
            second = alerts.evaluate_once()
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)
        self.assertEqual(len(alerts.list_alerts()), 1)

    def test_price_below_crossing(self):
        self._add(kind="price_below", level=90)
        with mock.patch.object(tdata, "get_quote", return_value=_quote(85)):
            fired = alerts.evaluate_once()
        self.assertEqual(len(fired), 1)
        self.assertIn("crossed BELOW 90", fired[0])

    def test_pct_move_with_explicit_reference(self):
        self._add(kind="pct_move", level=5, ref_price=100)
        with mock.patch.object(tdata, "get_quote", return_value=_quote(107)):
            fired = alerts.evaluate_once()
        self.assertEqual(len(fired), 1)
        self.assertIn("+7.00%", fired[0])

    def test_pct_move_feed_failure_keeps_alert_pending_and_is_honest(self):
        # reference price captured at add time while the feed is reachable
        with mock.patch.object(tdata, "get_quote", return_value=_quote(100)):
            self._add(kind="pct_move", level=5)
        alerts.add_watch("TEST", "long", entry=100)
        with mock.patch.object(tdata, "get_quote",
                               side_effect=Exception("feed down")):
            fired = alerts.evaluate_once()
        self.assertEqual(fired, [])                      # never a fabricated fire
        self.assertEqual(len(alerts.list_alerts()), 1)   # stays pending
        # the honest error surfaces on status instead
        with mock.patch.object(tdata, "get_quote",
                               side_effect=Exception("feed down")):
            st = alerts.watch_status("TEST")
        self.assertFalse(st["ok"])
        self.assertIn("Could not price", st["error"])

    def test_breakout_above_level(self):
        self._add(kind="breakout", level=70000)
        with mock.patch.object(tdata, "get_quote",
                               return_value=_quote(price=71000)):
            fired = alerts.evaluate_once()
        self.assertEqual(len(fired), 1)
        self.assertIn("breakout", fired[0].lower())

    def test_stop_and_target_alert_types_fire(self):
        alerts.add(kind="stop_level", symbol="TEST", level=95)
        alerts.add(kind="target_level", symbol="TEST", level=120)
        with mock.patch.object(tdata, "get_quote", return_value=_quote(94)):
            fired = alerts.evaluate_once()
        self.assertEqual(len(fired), 1)
        self.assertIn("stop", fired[0].lower())
        with mock.patch.object(tdata, "get_quote", return_value=_quote(121)):
            fired = alerts.evaluate_once()
        self.assertEqual(len(fired), 1)
        self.assertIn("target", fired[0].lower())


class IndicatorAlertTest(TradingEnvMixin, unittest.TestCase):
    def test_rsi_cross_above_fires_when_threshold_is_crossed(self):
        # 15 steadily falling closes (RSI 0), then a jump → RSI ~27.8 > 20.
        closes = [100 - i for i in range(15)] + [91.0]
        bars = [_bar(c, i) for i, c in enumerate(closes)]
        alerts.add(kind="rsi_level", symbol="TEST", level=20, direction="above")
        with mock.patch.object(tdata, "get_candles",
                               return_value=_candles(bars)):
            fired = alerts.evaluate_once()
        self.assertEqual(len(fired), 1)
        self.assertIn("RSI(14)", fired[0])
        self.assertIn("crossed above 20", fired[0])

    def test_rsi_not_crossed_stays_silent(self):
        closes = [100 - i for i in range(30)]          # RSI stays 0
        bars = [_bar(c, i) for i, c in enumerate(closes)]
        alerts.add(kind="rsi_level", symbol="TEST", level=20, direction="above")
        with mock.patch.object(tdata, "get_candles",
                               return_value=_candles(bars)):
            self.assertEqual(alerts.evaluate_once(), [])

    def test_ma_cross_fires_on_the_cross_bar(self):
        # 100 falling closes (SMA20 < SMA50), then one spike: cross at bar 100.
        closes = [1000 - i for i in range(100)] + [2000.0]
        bars = [_bar(c, i) for i, c in enumerate(closes)]
        alerts.add(kind="ma_cross", symbol="TEST", level=20)
        with mock.patch.object(tdata, "get_candles",
                               return_value=_candles(bars)):
            fired = alerts.evaluate_once()
        self.assertEqual(len(fired), 1)
        self.assertIn("SMA20/50 cross", fired[0])

    def test_volume_spike(self):
        vols = [1000.0] * 19 + [4000.0]
        bars = [_bar(100.5, i, v=v) for i, v in enumerate(vols)]
        alerts.add(kind="volume_spike", symbol="TEST", multiplier=2.0)
        with mock.patch.object(tdata, "get_candles",
                               return_value=_candles(bars)):
            fired = alerts.evaluate_once()
        self.assertEqual(len(fired), 1)
        self.assertIn("volume spike", fired[0])
        self.assertIn("4.0×", fired[0])                # 4000 vs 1000 average


class NewsAlertTest(TradingEnvMixin, unittest.TestCase):
    def test_news_topic_fires_once_then_quiet_until_new_news(self):
        alerts.add(kind="news", symbol="", topic="FOMC decision")
        with mock.patch.object(tdata, "fetch_news",
                               return_value=[{"title": "Fed holds rates",
                                              "source": "WireCo",
                                              "date": "2026-09-23"}]):
            first = alerts.evaluate_once()
            second = alerts.evaluate_once()          # fired alert is gone
        self.assertEqual(len(first), 1)
        self.assertIn("Fed holds rates", first[0])
        self.assertIn("WireCo", first[0])
        self.assertEqual(len(second), 0)
        self.assertEqual(len(alerts.list_alerts()), 0)   # fired (once default)

    def test_recurring_news_waits_for_a_new_headline(self):
        alerts.add(kind="news", symbol="", topic="FOMC decision", once=False)
        with mock.patch.object(tdata, "fetch_news",
                               return_value=[{"title": "Fed holds rates"}]):
            first = alerts.evaluate_once()
            self.assertEqual(len(first), 1)
            self.assertEqual(alerts.evaluate_once(), [])   # same headline
            self.assertEqual(alerts.evaluate_once(), [])
            self.assertEqual(len(alerts.list_alerts()), 1)  # still registered


class WatchTest(TradingEnvMixin, unittest.TestCase):
    def test_watch_registers_stop_and_target_alerts(self):
        r = alerts.add_watch("TEST", "long", entry=100, stop=95,
                             targets=[110, 120], quantity=5)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["watching"], "TEST")
        self.assertEqual(len(r["auto_alerts"]), 3)      # stop + 2 targets
        kinds = {a["type"] for a in alerts.list_alerts()}
        self.assertIn("stop_level", kinds)
        self.assertIn("target_level", kinds)
        self.assertEqual(len(alerts.list_watches()), 1)

    def test_watch_status_reports_pnl_and_distances(self):
        alerts.add_watch("TEST", "long", entry=100, stop=95,
                         targets=[110, 120], quantity=5)
        with mock.patch.object(tdata, "get_quote", return_value=_quote(110)):
            st = alerts.watch_status("TEST")
        self.assertTrue(st["ok"])
        self.assertEqual(st["current"], 110)
        self.assertEqual(st["unrealized_pnl"], 50.0)    # (110-100)*5
        self.assertAlmostEqual(st["unrealized_pnl_pct"], 10.0)
        self.assertAlmostEqual(st["distance_to_stop_pct"],
                               abs(110 - 95) / 110 * 100, places=3)
        self.assertIn("min", st["time_in_trade"])
        self.assertEqual(st["provider"], "FakeFeed")

    def test_format_watch_shows_the_required_readout(self):
        alerts.add_watch("TEST", "long", entry=100, stop=95, targets=[110],
                         quantity=5)
        with mock.patch.object(tdata, "get_quote", return_value=_quote(110)):
            text = alerts.format_watch(alerts.watch_status("TEST"))
        for needle in ("WATCHING TEST", "Entry: 100", "Current: 110",
                       "Stop: 95", "Targets: 110", "Unrealized P/L: $50.00",
                       "(+10.00%)", "Data: FakeFeed", "as of",
                       "Time in trade:"):
            self.assertIn(needle, text)

    def test_watch_status_without_price_is_honest(self):
        alerts.add_watch("TEST", "long", entry=100)
        with mock.patch.object(tdata, "get_quote",
                               side_effect=Exception("feed down")):
            st = alerts.watch_status("TEST")
        self.assertFalse(st["ok"])
        self.assertIn("Could not price", st["error"])

    def test_unknown_symbol_points_at_the_tool(self):
        st = alerts.watch_status("GHOST")
        self.assertFalse(st["ok"])
        self.assertIn("trade_monitor", st["error"])

    def test_remove_watch_drops_its_auto_alerts(self):
        alerts.add_watch("TEST", "long", entry=100, stop=95, targets=[110])
        before = len(alerts.list_alerts())
        self.assertGreaterEqual(before, 2)
        r = alerts.remove_watch("TEST")
        self.assertTrue(r["ok"])
        self.assertEqual(len(alerts.list_watches()), 0)
        self.assertLess(len(alerts.list_alerts()), before)

    def test_watch_validation(self):
        self.assertFalse(alerts.add_watch("", "long", 100)["ok"])
        self.assertFalse(alerts.add_watch("T", "long", 100, stop="x")["ok"])
        self.assertFalse(alerts.add_watch("T", "long", -1)["ok"])
        self.assertFalse(
            alerts.add_watch("T", "long", 100, targets=["abc"])["ok"])


class DrainQueueTest(TradingEnvMixin, unittest.TestCase):
    def test_drain_returns_in_order_and_clears(self):
        alerts._queue.append("alert one")
        alerts._queue.append("alert two")
        out = alerts.drain()
        self.assertEqual(out, ["alert one", "alert two"])
        self.assertEqual(alerts.drain(), [])

    def test_notifier_round_trip(self):
        seen = []
        alerts.set_notifier(seen.append)
        try:
            # the worker's per-message delivery path, without the thread
            alerts._notifier("x")
            self.assertEqual(seen, ["x"])
        finally:
            alerts.set_notifier(None)

    def test_configure_toggles_autostart_flag(self):
        alerts.configure(auto_start=False)
        self.assertFalse(alerts._AUTO_START)
        alerts.configure(auto_start=True)
        self.assertTrue(alerts._AUTO_START)


if __name__ == "__main__":
    unittest.main()
