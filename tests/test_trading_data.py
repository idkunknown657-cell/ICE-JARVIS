"""
Market-data provider layer (spec §1/§7/§8): parsing, provenance on every
payload, stale-data labeling, honest provider failures, caching. The network
is replaced wholesale through tdata._http_get — no test touches the wire.
"""
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trading_env import TradingEnvMixin

from core.trading import data as tdata
from core.trading import language


def _payload(closes=None, price_ts=None, start_ts=1440000, step=86400):
    closes = closes or [100.0, 101.0, 102.0, 103.0, 104.0]
    ts = [start_ts + i * step for i in range(len(closes))]
    return {
        "chart": {
            "result": [{
                "meta": {
                    "regularMarketPrice": 104.5,
                    "chartPreviousClose": 100.0,
                    "currency": "USD",
                    "fullExchangeName": "NasdaqGS",
                    "instrumentType": "EQUITY",
                    "shortName": "Test Corp",
                    "regularMarketTime": price_ts if price_ts is not None else ts[-1],
                },
                "timestamp": ts,
                "indicators": {"quote": [{
                    "open": [c - 0.5 for c in closes],
                    "high": [c + 1.0 for c in closes],
                    "low": [c - 1.5 for c in closes],
                    "close": list(closes),
                    "volume": [1000 + i * 100 for i in range(len(closes))],
                }]},
            }],
            "error": None,
        }
    }


class QuoteAndProvenanceTest(TradingEnvMixin, unittest.TestCase):
    def test_quote_carries_price_change_ohlc_volume_provider_timestamp(self):
        with mock.patch.object(tdata, "_http_get",
                               return_value=_payload(price_ts=time.time())):
            q = tdata.get_quote("TEST")
        self.assertEqual(q["price"], 104.5)
        self.assertAlmostEqual(q["change"], 4.5)
        self.assertAlmostEqual(q["change_pct"], 4.5)
        self.assertEqual(q["open"], 103.5)      # latest daily bar
        self.assertEqual(q["high"], 105.0)
        self.assertEqual(q["low"], 102.5)
        self.assertEqual(q["close"], 104.0)
        self.assertEqual(q["volume"], 1400)
        self.assertEqual(q["provider"], "Yahoo Finance")
        self.assertTrue(q["data_asof"])
        self.assertFalse(q["delayed"])

    def test_format_quote_shows_everything_required(self):
        with mock.patch.object(tdata, "_http_get",
                               return_value=_payload(price_ts=time.time())):
            text = tdata.format_quote(tdata.get_quote("TEST"))
        for needle in ("TEST", "Test Corp", "Price:", "Change:", "OHLC",
                       "Volume:", "Timeframe:", "Data provider:",
                       "Data timestamp:"):
            self.assertIn(needle, text)

    def test_stale_data_gets_the_delay_warning(self):
        with mock.patch.object(tdata, "_http_get",
                               return_value=_payload(price_ts=time.time() - 3700)):
            q = tdata.get_quote("TEST")
            text = tdata.format_quote(q)
        self.assertTrue(q["delayed"])
        self.assertIn("Market data may be delayed.", text)
        self.assertEqual(language.violations(text), [])

    def test_fresh_data_never_claimed_realtime_but_no_delay_line(self):
        with mock.patch.object(tdata, "_http_get",
                               return_value=_payload(price_ts=time.time())):
            q = tdata.get_quote("TEST")
            text = tdata.format_quote(q)
        self.assertFalse(q["delayed"])
        self.assertNotIn("Market data may be delayed.", text)
        self.assertNotIn("real-time", text.lower())

    def test_candles_format_includes_provider_and_timeframe(self):
        with mock.patch.object(tdata, "_http_get", return_value=_payload()):
            text = tdata.format_candles(tdata.get_candles("TEST", "1D"), last=3)
        self.assertIn("TEST", text)
        self.assertIn("1D", text)
        self.assertIn("Data provider:", text)
        self.assertIn("Data timestamp:", text)


class AggregationAndErrorsTest(TradingEnvMixin, unittest.TestCase):
    def test_4h_aggregates_hourly_bars_into_four_bar_buckets(self):
        # 8 hourly bars starting exactly on a 4h boundary → 2 buckets of 4.
        closes = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0]
        with mock.patch.object(tdata, "_http_get",
                               return_value=_payload(closes=closes, start_ts=1440000,
                                                     step=3600)):
            bars = tdata.get_candles("TEST", "4h")["bars"]
        self.assertEqual(len(bars), 2)
        first, second = bars
        self.assertEqual(first["t"], 1440000)
        self.assertEqual(first["o"], 9.5)          # first open
        self.assertEqual(first["h"], 14.0)         # max high of bars 0-3 (13+1)
        self.assertEqual(first["l"], 8.5)          # min low  (10-1.5)
        self.assertEqual(first["c"], 13.0)         # last close
        self.assertEqual(first["v"], 1000 + 1100 + 1200 + 1300)  # summed
        self.assertEqual(second["t"], 1440000 + 14400)
        self.assertEqual(second["o"], 13.5)

    def test_pure_aggregate_fn_is_standalone(self):
        bars = [{"t": 1440000 + i * 3600, "o": 1.0, "h": float(i + 2),
                 "l": 0.5, "c": float(i + 1), "v": 10} for i in range(4)]
        out = tdata.YahooProvider._aggregate_4h(bars)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["h"], 5.0)
        self.assertEqual(out[0]["c"], 4.0)
        self.assertEqual(out[0]["v"], 40)

    def test_provider_error_payload_surfaces_provider_words(self):
        bad = {"chart": {"result": None,
                         "error": {"code": "Not Found",
                                   "description": "No data found, symbol may be malformed"}}}
        with mock.patch.object(tdata, "_http_get", return_value=bad):
            with self.assertRaises(tdata.DataError) as cm:
                tdata.get_quote("NOSUCH")
        self.assertIn("No data found", str(cm.exception))

    def test_http_error_and_network_error_become_dataerror(self):
        class FakeResp:
            status_code = 503
            def json(self):
                return {}
        with mock.patch.object(tdata.requests, "get", return_value=FakeResp()):
            with self.assertRaises(tdata.DataError) as cm:
                tdata._http_get("https://example.invalid/x")
        self.assertIn("HTTP 503", str(cm.exception))

        import requests as _rq
        with mock.patch.object(tdata.requests, "get",
                               side_effect=_rq.ConnectionError("refused")):
            with self.assertRaises(tdata.DataError) as cm:
                tdata._http_get("https://example.invalid/x")
        self.assertIn("Network error", str(cm.exception))

    def test_unknown_provider_fails_loudly_listing_available(self):
        tsettings_path = self.tmp / "trading_settings.json"
        tdata_settings_mod = __import__("core.trading.settings", fromlist=["set_value"])
        tdata_settings_mod.set_value("provider", "nonexistent")
        with self.assertRaises(tdata.DataError) as cm:
            tdata.get_quote("TEST")
        msg = str(cm.exception)
        self.assertIn("nonexistent", msg)
        self.assertIn("yahoo", msg)
        self.assertTrue(tsettings_path.exists())

    def test_unsupported_timeframe_lists_supported(self):
        with self.assertRaises(tdata.DataError) as cm:
            tdata.get_candles("TEST", "3y")
        self.assertIn("Unsupported timeframe", str(cm.exception))
        self.assertIn("1D", str(cm.exception))

    def test_empty_symbol_rejected(self):
        with self.assertRaises(tdata.DataError):
            tdata.get_quote("   ")


class CacheTest(TradingEnvMixin, unittest.TestCase):
    def test_second_quote_within_ttl_does_not_refetch(self):
        with mock.patch.object(tdata, "_http_get",
                               return_value=_payload(price_ts=time.time())) as m:
            tdata.get_quote("TEST")
            tdata.get_quote("TEST")
            self.assertEqual(m.call_count, 1)
        tdata.clear_cache()
        with mock.patch.object(tdata, "_http_get",
                               return_value=_payload(price_ts=time.time())) as m:
            tdata.get_quote("TEST")
            tdata.get_quote("TEST")
            self.assertEqual(m.call_count, 1)
            tdata.clear_cache()
            tdata.get_quote("TEST")
            self.assertEqual(m.call_count, 2)

    def test_cache_is_keyed_by_symbol(self):
        with mock.patch.object(tdata, "_http_get",
                               return_value=_payload(price_ts=time.time())) as m:
            tdata.get_quote("AAA")
            tdata.get_quote("BBB")
            self.assertEqual(m.call_count, 2)


class NewsEarningsCalendarTest(TradingEnvMixin, unittest.TestCase):
    def test_news_normalized_with_source_and_date(self):
        raw = [{"title": "Test Corp beats estimates", "source": "WireCo",
                "url": "https://example.invalid/a", "date": "2026-09-22",
                "body": "details..."},
               {"title": "", "source": "skip"},     # dropped: no title
               {"title": "Second"}]                 # source/date default to ""
        with mock.patch("actions.web_search._ddg_news", return_value=raw):
            items = tdata.fetch_news("TEST", max_results=5)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["source"], "WireCo")
        self.assertEqual(items[0]["date"], "2026-09-22")

    def test_news_failure_is_dataerror_not_invention(self):
        with mock.patch("actions.web_search._ddg_news",
                        side_effect=RuntimeError("offline")):
            with self.assertRaises(tdata.DataError) as cm:
                tdata.fetch_news("TEST")
        self.assertIn("unavailable", str(cm.exception))

    def test_calendar_reports_honest_unavailability(self):
        r = tdata.get_calendar()
        self.assertFalse(r["available"])
        self.assertIn("no economic-calendar", r["reason"])
        self.assertIn("trading_settings", r["reason"])

    def test_earnings_failure_says_nothing_was_invented(self):
        with mock.patch.object(tdata, "_http_get",
                               side_effect=tdata.DataError("HTTP 401 from endpoint")):
            r = tdata.get_earnings("TEST")
        self.assertFalse(r["available"])
        self.assertIn("unavailable", r["reason"])
        self.assertIn("No earnings information was invented", r["reason"])

    def test_earnings_rows_parsed_when_provider_returns_them(self):
        ok = {"quoteSummary": {"result": [{"earnings": {"earningsChart": {
            "quarterly": [{"date": "Q3 2026", "estimate": "1.10", "actual": "1.25"}]}}}]}}
        with mock.patch.object(tdata, "_http_get", return_value=ok):
            r = tdata.get_earnings("TEST")
        self.assertTrue(r["available"])
        self.assertEqual(r["rows"][0]["actual"], "1.25")


if __name__ == "__main__":
    unittest.main()
