"""
core/trading/indicators.py — the technical-analysis math (spec §2).

Pure numpy, backward-looking only (every value at bar i uses bars ≤ i — no
centered windows, no future data). None/NaN-padded prefixes so partial series
are honest rather than quietly shifted.

None of these is a prediction: each returns measurements, and analysis.py is
responsible for phrasing them as evidence FOR/AGAINST plus an invalidation —
never as a signal that is "supposed to" happen.
"""
from __future__ import annotations

import numpy as np


def _arr(x) -> np.ndarray:
    return np.asarray(x, dtype=float)


def last_finite(a) -> float | None:
    """Most recent non-NaN value, or None when the series has none."""
    a = _arr(a) if a is not None else np.array([])
    for v in a[::-1]:
        if np.isfinite(v):
            return float(v)
    return None


def sma(c, n: int) -> np.ndarray:
    c = _arr(c)
    out = np.full(c.shape, np.nan)
    if n <= 0 or c.size < n:
        return out
    cs = np.cumsum(np.insert(c, 0, 0.0))
    out[n - 1:] = (cs[n:] - cs[:-n]) / n
    return out


def ema(c, n: int) -> np.ndarray:
    c = _arr(c)
    out = np.full(c.shape, np.nan)
    if n <= 0 or c.size < n:
        return out
    alpha = 2.0 / (n + 1)
    out[n - 1] = c[:n].mean()
    for i in range(n, c.size):
        out[i] = alpha * c[i] + (1.0 - alpha) * out[i - 1]
    return out


def vwap(h, l, c, v) -> np.ndarray:
    """Series-anchored VWAP (typical price × volume, cumulative). Anchoring is
    noted in reports — session-anchored VWAP needs a session clock we only
    have on intraday feeds."""
    h, l, c, v = _arr(h), _arr(l), _arr(c), _arr(v)
    out = np.full(c.shape, np.nan)
    if c.size == 0:
        return out
    tp = (h + l + c) / 3.0
    pv = np.nancumsum(tp * np.where(np.isfinite(c), v, 0.0))
    cv = np.nancumsum(np.where(np.isfinite(c), v, 0.0))
    with np.errstate(invalid="ignore", divide="ignore"):
        out = np.where(cv > 0, pv / cv, np.nan)
    return out


def rsi(c, n: int = 14) -> np.ndarray:
    """Wilder's RSI."""
    c = _arr(c)
    out = np.full(c.shape, np.nan)
    if c.size < n + 1:
        return out
    delta = np.diff(c)
    gains = np.where(delta > 0, delta, 0.0)
    losses = np.where(delta < 0, -delta, 0.0)
    avg_g = gains[:n].mean()
    avg_l = losses[:n].mean()
    out[n] = 100.0 if avg_l == 0 else 100.0 - 100.0 / (1.0 + avg_g / avg_l)
    for i in range(n, c.size - 1):
        avg_g = (avg_g * (n - 1) + gains[i]) / n
        avg_l = (avg_l * (n - 1) + losses[i]) / n
        out[i + 1] = 100.0 if avg_l == 0 else 100.0 - 100.0 / (1.0 + avg_g / avg_l)
    return out


def macd(c, fast: int = 12, slow: int = 26, signal_n: int = 9):
    """Returns (macd_line, signal_line, histogram)."""
    c = _arr(c)
    line = ema(c, fast) - ema(c, slow)
    signal = np.full(c.shape, np.nan)
    finite = np.flatnonzero(np.isfinite(line))
    if finite.size:
        start = finite[0]
        seg = ema(line[start:], signal_n)
        signal[start:] = seg
    hist = line - signal
    return line, signal, hist


def bollinger(c, n: int = 20, k: float = 2.0):
    """Returns (middle, upper, lower, percent_b, bandwidth)."""
    c = _arr(c)
    middle = sma(c, n)
    upper = np.full(c.shape, np.nan)
    lower = np.full(c.shape, np.nan)
    pct_b = np.full(c.shape, np.nan)
    bw = np.full(c.shape, np.nan)
    for i in range(n - 1, c.size):
        window = c[i - n + 1:i + 1]
        sd = float(np.std(window, ddof=0))
        m = middle[i]
        upper[i] = m + k * sd
        lower[i] = m - k * sd
        if upper[i] != lower[i]:
            pct_b[i] = (c[i] - lower[i]) / (upper[i] - lower[i])
            bw[i] = (upper[i] - lower[i]) / m if m else np.nan
    return middle, upper, lower, pct_b, bw


def true_range(h, l, c) -> np.ndarray:
    h, l, c = _arr(h), _arr(l), _arr(c)
    tr = h - l
    if c.size > 1:
        pc = np.roll(c, 1)
        tr[1:] = np.maximum.reduce([h[1:] - l[1:],
                                    np.abs(h[1:] - pc[1:]),
                                    np.abs(l[1:] - pc[1:])])
    return tr


def atr(h, l, c, n: int = 14) -> np.ndarray:
    """Wilder ATR."""
    tr = true_range(h, l, c)
    out = np.full(tr.shape, np.nan)
    if tr.size < n:
        return out
    out[n - 1] = tr[:n].mean()
    for i in range(n, tr.size):
        out[i] = (out[i - 1] * (n - 1) + tr[i]) / n
    return out


def adx(h, l, c, n: int = 14):
    """Returns (adx, plus_di, minus_di) — Wilder smoothing throughout."""
    h, l, c = _arr(h), _arr(l), _arr(c)
    size = c.size
    out_adx = np.full(size, np.nan)
    out_pdi = np.full(size, np.nan)
    out_mdi = np.full(size, np.nan)
    if size < 2 * n + 1:
        return out_adx, out_pdi, out_mdi
    up = np.zeros(size)
    dn = np.zeros(size)
    for i in range(1, size):
        up_move = h[i] - h[i - 1]
        dn_move = l[i - 1] - l[i]
        up[i] = up_move if (up_move > dn_move and up_move > 0) else 0.0
        dn[i] = dn_move if (dn_move > up_move and dn_move > 0) else 0.0
    tr_ = true_range(h, l, c)
    atr_s = np.zeros(size)
    pdm_s = np.zeros(size)
    mdm_s = np.zeros(size)
    atr_s[n] = tr_[1:n + 1].sum()
    pdm_s[n] = up[1:n + 1].sum()
    mdm_s[n] = dn[1:n + 1].sum()
    dx = np.full(size, np.nan)
    for i in range(n + 1, size):
        atr_s[i] = atr_s[i - 1] - atr_s[i - 1] / n + tr_[i]
        pdm_s[i] = pdm_s[i - 1] - pdm_s[i - 1] / n + up[i]
        mdm_s[i] = mdm_s[i - 1] - mdm_s[i - 1] / n + dn[i]
        pdi = 100.0 * pdm_s[i] / atr_s[i] if atr_s[i] else 0.0
        mdi = 100.0 * mdm_s[i] / atr_s[i] if atr_s[i] else 0.0
        out_pdi[i] = pdi
        out_mdi[i] = mdi
        dx[i] = 100.0 * abs(pdi - mdi) / (pdi + mdi) if (pdi + mdi) else 0.0
    valid = np.flatnonzero(np.isfinite(dx))
    if valid.size >= n:
        start = valid[n - 1]
        out_adx[start] = np.nanmean(dx[valid[:n]])
        for i in range(start + 1, size):
            if np.isfinite(dx[i]):
                out_adx[i] = (out_adx[i - 1] * (n - 1) + dx[i]) / n
    return out_adx, out_pdi, out_mdi


def stochastic(h, l, c, n: int = 14, smooth: int = 3):
    """Returns (%K, %D)."""
    h, l, c = _arr(h), _arr(l), _arr(c)
    k = np.full(c.shape, np.nan)
    for i in range(n - 1, c.size):
        hh = h[i - n + 1:i + 1].max()
        ll = l[i - n + 1:i + 1].min()
        k[i] = 50.0 if hh == ll else 100.0 * (c[i] - ll) / (hh - ll)
    return k, sma(k, smooth)


def volume_stats(v, n: int = 20):
    """Returns (average volume over n, latest/average relative volume)."""
    v = _arr(v)
    if v.size < 2:
        return None, None
    window = v[-min(n, v.size):]
    avg = float(np.nanmean(window[:-1])) if v.size > 1 else None
    latest = float(v[-1])
    if avg and avg > 0:
        return avg, latest / avg
    return avg, None


# ── Structure: pivots, S/R, trend, crosses, breakouts ───────────────────────

def pivots(h, l, left: int = 2, right: int = 2):
    """Swing points: index i is a swing high when its high exceeds the `left`
    bars before and the `right` bars after. Backward-looking by construction."""
    h, l = _arr(h), _arr(l)
    highs, lows = [], []
    for i in range(left, h.size - right):
        window_h = h[i - left:i + right + 1]
        window_l = l[i - left:i + right + 1]
        if np.isfinite(h[i]) and h[i] == window_h.max() and (window_h < h[i]).sum() >= left:
            highs.append((i, float(h[i])))
        if np.isfinite(l[i]) and l[i] == window_l.min() and (window_l > l[i]).sum() >= left:
            lows.append((i, float(l[i])))
    return highs, lows


def support_resistance(bars: dict, price: float, max_levels: int = 3):
    """Clustered swing levels split into supports (< price) and resistances
    (> price), most-touched first. Falls back to series extremes when there
    are not enough pivots (short history — said as much in the report)."""
    c = _arr(bars["c"])
    highs, lows = pivots(bars["h"], bars["l"])
    pts = [p for _, p in highs] + [p for _, p in lows]
    fallback = not pts
    if fallback:
        pts = [float(np.nanmax(bars["h"])), float(np.nanmin(bars["l"]))]
    pts.sort()
    tolerance = max(abs(price) * 0.005, 1e-9)
    clusters: list[list[float]] = [[pts[0]]]
    for p in pts[1:]:
        if p - clusters[-1][-1] <= tolerance:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    levels = [{"price": float(np.mean(cl)), "touches": len(cl)} for cl in clusters]
    supports = sorted((lv for lv in levels if lv["price"] < price),
                      key=lambda x: (-x["touches"], price - x["price"]))[:max_levels]
    resistances = sorted((lv for lv in levels if lv["price"] > price),
                         key=lambda x: (-x["touches"], x["price"] - price))[:max_levels]
    return {"supports": supports, "resistances": resistances, "fallback": fallback}


def previous_levels(bars: dict):
    """Prior bar's high/low/close — on daily series these are the previous
    day's levels (§2 'previous highs/lows'). Uses the second-to-last bar
    because the last bar is the still-developing session."""
    b = bars["bars"]
    if len(b) < 2:
        return None
    prev = b[-2]
    return {"high": prev["h"], "low": prev["l"], "close": prev["c"], "t": prev["t"]}


def trend_structure(h, l, c) -> dict:
    """Uptrend / downtrend / range from swing structure, with a moving-average
    fallback for short histories. Returns {label, evidence[]}."""
    highs, lows = pivots(h, l)
    evidence: list[str] = []
    if len(highs) >= 2 and len(lows) >= 2:
        hh = highs[-1][1] > highs[-2][1]
        hl = lows[-1][1] > lows[-2][1]
        lh = highs[-1][1] < highs[-2][1]
        ll = lows[-1][1] < lows[-2][1]
        evidence.append(f"last swing highs {highs[-2][1]:.4f} → {highs[-1][1]:.4f}, "
                        f"lows {lows[-2][1]:.4f} → {lows[-1][1]:.4f}")
        if hh and hl:
            return {"label": "uptrend", "evidence": evidence + ["higher highs and higher lows"]}
        if lh and ll:
            return {"label": "downtrend", "evidence": evidence + ["lower highs and lower lows"]}
        return {"label": "range", "evidence": evidence + ["swing highs/lows are mixed (no clear direction)"]}
    e50, e200 = ema(c, 50), ema(c, 200)
    v50, v200 = last_finite(e50), last_finite(e200)
    if v50 is not None and v200 is not None:
        if v50 > v200:
            return {"label": "uptrend (MA-based)", "evidence": [f"EMA50 {v50:.4f} above EMA200 {v200:.4f}"]}
        if v50 < v200:
            return {"label": "downtrend (MA-based)", "evidence": [f"EMA50 {v50:.4f} below EMA200 {v200:.4f}"]}
    return {"label": "range (limited history)", "evidence": ["not enough swings or MAs to classify"]}


def ma_cross_state(c, fast: int = 20, slow: int = 50) -> dict:
    """Current fast/slow relationship + whether a cross happened recently."""
    f, s = sma(c, fast), sma(c, slow)
    diff = f - s
    state = "flat/unknown"
    last = last_finite(diff)
    if last is not None:
        state = "bullish (fast above slow)" if last > 0 else "bearish (fast below slow)"
    cross_bars_ago = None
    for i in range(diff.size - 1, 0, -1):
        if np.isfinite(diff[i]) and np.isfinite(diff[i - 1]):
            if diff[i - 1] <= 0 < diff[i]:
                cross_bars_ago = diff.size - 1 - i
                state = "bullish — golden cross happened"
                break
            if diff[i - 1] >= 0 > diff[i]:
                cross_bars_ago = diff.size - 1 - i
                state = "bearish — death cross happened"
                break
    return {"state": state, "cross_bars_ago": cross_bars_ago,
            "fast": fast, "slow": slow}


def breakout_state(c, sr: dict, lookback: int = 3) -> dict:
    """Is price currently breaking/closing through the nearest level?
    Returns observed booleans only — 'breakout' here means measured, not
    predicted."""
    c = _arr(c)
    price = last_finite(c)
    if price is None or not sr:
        return {"testing": None, "closed_above": None, "closed_below": None}
    above = [lv["price"] for lv in sr.get("resistances", []) if lv["price"] > price]
    below = [lv["price"] for lv in sr.get("supports", []) if lv["price"] < price]
    near_above = min(above) if above else None
    near_below = max(below) if below else None
    recent = c[-min(lookback, c.size):]
    closed_above = bool(near_above is not None and np.nanmax(recent) >= near_above)
    closed_below = bool(near_below is not None and np.nanmin(recent) <= near_below)
    return {"near_resistance": near_above, "near_support": near_below,
            "closed_above": closed_above, "closed_below": closed_below,
            "price": price}


def volatility(c, n: int = 20) -> dict:
    """Percentage volatility: stdev of returns + ATR as % of price."""
    c = _arr(c)
    out = {"returns_pct": None, "atr_pct": None}
    if c.size > n:
        rets = np.diff(c[-n - 1:]) / c[-n - 1:-1]
        rets = rets[np.isfinite(rets)]
        if rets.size:
            out["returns_pct"] = float(np.std(rets, ddof=0) * 100.0)
    return out


def atr_pct(h, l, c, n: int = 14) -> float | None:
    a = last_finite(atr(h, l, c, n))
    price = last_finite(c)
    if a is None or not price:
        return None
    return a / price * 100.0
