"""
Shared harness for the trading-module tests (not collected by discovery —
no test_ prefix).

Isolation contract for every suite:
  * settings/journal/paper/alerts JSON paths are redirected into a temp dir —
    tests never touch the user's real config/api_keys.json or personal data;
  * the alert background worker is disabled (no threads, no network polls);
  * the market-data TTL cache and any prepared orders are cleared both ways.
"""
import tempfile
import unittest
from pathlib import Path

from core.trading import alerts as talerts
from core.trading import data as tdata
from core.trading import journal as tjournal
from core.trading import paper as tpaper
from core.trading import settings as tsettings
from core.trading import orderflow as torderflow

_PATH_NAMES = {
    id(tsettings): "trading_settings.json",
    id(tjournal): "trading_journal.json",
    id(tpaper): "paper_account.json",
    id(talerts): "trading_alerts.json",
}


class TradingEnvMixin:
    def setUp(self):
        super().setUp()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self._saved_paths = []
        for mod in (tsettings, tjournal, tpaper, talerts):
            self._saved_paths.append((mod, mod._path))
            filename = _PATH_NAMES[id(mod)]
            mod._path = self._mkpath(self.tmp, filename)
        talerts.configure(False)          # no worker threads in tests
        talerts.drain()
        talerts._queue.clear()
        tdata.clear_cache()
        torderflow._pending.clear()

    @staticmethod
    def _mkpath(tmp, filename):
        return lambda: tmp / filename

    def tearDown(self):
        talerts.configure(True)
        talerts.drain()
        talerts._queue.clear()
        tdata.clear_cache()
        torderflow._pending.clear()
        for mod, fn in self._saved_paths:
            mod._path = fn
        self._tmpdir.cleanup()
        super().tearDown()
