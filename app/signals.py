"""Multi-indicator signal engine — TradingView-style technical analysis on
1-minute and 5-minute bars, combined into a weighted BUY/SELL prediction.

Pure functions of bar lists. No I/O. Unit-tested.
"""
from __future__ import annotations

from typing import Optional


# ---------------------------------------------------------------------------
# Indicator math
# ---------------------------------------------------------------------------
def ema(values: list[float], period: int) -> Optional[float]:
    if not values:
        return None
    k = 2.0 / (period + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return e


def rsi(closes: list[float], period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_gain == 0 and avg_loss == 0:
        return 50.0  # flat market — neither overbought nor oversold
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def macd(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9):
    """Returns (macd_line, signal_line, histogram)."""
    if len(closes) < slow + signal:
        return None, None, None

    def ema_series(data, period):
        k = 2.0 / (period + 1)
        out = [data[0]]
        for v in data[1:]:
            out.append(v * k + out[-1] * (1 - k))
        return out

    ef = ema_series(closes, fast)
    es = ema_series(closes, slow)
    macd_line = [a - b for a, b in zip(ef, es)]
    sig_line = ema_series(macd_line, signal)
    hist = macd_line[-1] - sig_line[-1]
    return macd_line[-1], sig_line[-1], hist


def stochastic(bars: list[dict], k_period: int = 14, d_period: int = 3):
    """Returns (k, d) — fast and slow stochastic."""
    if len(bars) < k_period + d_period:
        return None, None
    ks = []
    for i in range(k_period - 1, len(bars)):
        window = bars[i - k_period + 1: i + 1]
        hi = max(b["high"] for b in window)
        lo = min(b["low"] for b in window)
        if hi == lo:
            ks.append(50.0)
        else:
            ks.append((bars[i]["close"] - lo) / (hi - lo) * 100.0)
    d = sum(ks[-d_period:]) / d_period
    return ks[-1], d


def bollinger(closes: list[float], period: int = 20, k: float = 2.0):
    if len(closes) < period:
        return None, None, None
    window = closes[-period:]
    mid = sum(window) / period
    var = sum((c - mid) ** 2 for c in window) / period
    sd = var ** 0.5
    return mid, mid + k * sd, mid - k * sd


def vwap(bars: list[dict]) -> Optional[float]:
    pv = sum((b["high"] + b["low"] + b["close"]) / 3 * (b["volume"] or 0) for b in bars)
    vol = sum(b["volume"] or 0 for b in bars)
    return (pv / vol) if vol else None


def atr(bars: list[dict], period: int = 14) -> Optional[float]:
    if len(bars) < period + 1:
        return None
    trs = []
    for i in range(len(bars) - period, len(bars)):
        b, p = bars[i], bars[i - 1]
        trs.append(max(b["high"] - b["low"], abs(b["high"] - p["close"]), abs(b["low"] - p["close"])))
    return sum(trs) / len(trs)


def obv_slope(bars: list[dict], lookback: int = 10) -> Optional[float]:
    """Slope of on-balance volume over the last N bars (per-bar rate)."""
    if len(bars) < lookback + 1:
        return None
    obv = 0.0
    series = []
    for i in range(max(1, len(bars) - lookback - 1), len(bars)):
        b, p = bars[i], bars[i - 1]
        if b["close"] > p["close"]:
            obv += b["volume"] or 0
        elif b["close"] < p["close"]:
            obv -= b["volume"] or 0
        series.append(obv)
    if len(series) < 2 or series[-1] == series[0]:
        return 0.0
    return (series[-1] - series[0]) / (len(series) - 1)


def pivots(bars: list[dict], window: int = 2) -> tuple[list[float], list[float]]:
    """Fractal pivot highs (resistances) and lows (supports)."""
    res, sup = [], []
    n = len(bars)
    for i in range(window, n - window):
        seg_h = [bars[j]["high"] for j in range(i - window, i + window + 1)]
        seg_l = [bars[j]["low"] for j in range(i - window, i + window + 1)]
        if bars[i]["high"] == max(seg_h):
            res.append(bars[i]["high"])
        if bars[i]["low"] == min(seg_l):
            sup.append(bars[i]["low"])
    return res, sup


def detect_pattern(bars: list[dict]) -> Optional[str]:
    if len(bars) < 3:
        return None
    p, c = bars[-2], bars[-1]
    body_c = abs(c["close"] - c["open"])
    rng_c = c["high"] - c["low"] or 1.0
    if c["close"] > c["open"] and p["close"] < p["open"] and c["close"] > p["open"] and c["open"] < p["close"]:
        return "bullish engulfing"
    if c["close"] < c["open"] and p["close"] > p["open"] and c["open"] > p["close"] and c["close"] < p["open"]:
        return "bearish engulfing"
    if body_c < 0.1 * rng_c:
        return "doji"
    lower_wick = min(c["open"], c["close"]) - c["low"]
    upper_wick = c["high"] - max(c["open"], c["close"])
    if lower_wick > 2 * (body_c or 0.00001) and c["close"] >= c["open"]:
        return "hammer"
    if upper_wick > 2 * (body_c or 0.00001) and c["close"] <= c["open"]:
        return "shooting star"
    return None


def median_gap(bars: list[dict]) -> float:
    """Median seconds between consecutive bars (robust to market pauses)."""
    if len(bars) < 2:
        return 0.0
    gaps = sorted(bars[i + 1]["ts"] - bars[i]["ts"] for i in range(len(bars) - 1))
    return float(gaps[len(gaps) // 2])


def resample(bars: list[dict], factor: int) -> list[dict]:
    """Aggregate 1m bars into 5m (factor=5) bars.

    Adaptive: if the source bars are already ~factor minutes apart (e.g. a
    fallback feed handed us 5m bars where 1m was requested), return them
    unchanged instead of producing garbage 25m aggregates."""
    gap = median_gap(bars)
    if gap and gap >= factor * 60 * 0.75:
        return list(bars)
    out = []
    for i in range(0, len(bars) - len(bars) % factor, factor):
        group = bars[i: i + factor]
        out.append(
            {
                "ts": group[0]["ts"],
                "open": group[0]["open"],
                "high": max(b["high"] for b in group),
                "low": min(b["low"] for b in group),
                "close": group[-1]["close"],
                "volume": sum(b["volume"] or 0 for b in group),
            }
        )
    return out


# ---------------------------------------------------------------------------
# Timeframe analysis
# ---------------------------------------------------------------------------
def _timeframe_analysis(bars: list[dict], label: str) -> dict:
    if len(bars) < 26:
        return {
            "label": label, "verdict": "NO DATA", "score": 0.0,
            "indicators": {}, "reasons": ["not enough bars"],
        }
    closes = [b["close"] for b in bars]
    price = closes[-1]
    e9, e21, e50 = ema(closes, 9), ema(closes, 21), ema(closes, 50)
    r = rsi(closes)
    m_line, m_sig, m_hist = macd(closes)
    k_st, d_st = stochastic(bars)
    bb_mid, bb_up, bb_low = bollinger(closes)
    vp = vwap(bars)
    a = atr(bars)
    obv = obv_slope(bars)
    pattern = detect_pattern(bars)
    res, sup = pivots(bars[-90:])

    score = 0.0
    reasons: list[str] = []

    # Trend: EMA stack
    if e9 and e21:
        if e9 > e21:
            score += 1.0
            reasons.append(f"EMA 9 above EMA 21 — {label} uptrend")
        else:
            score -= 1.0
            reasons.append(f"EMA 9 below EMA 21 — {label} downtrend")
    if e21 and e50:
        if e21 > e50:
            score += 0.5
        else:
            score -= 0.5
    # Trend strength: spread of the fast EMAs
    if e9 and e21 and price:
        spread = (e9 - e21) / price * 100
        if spread > 0.15:
            score += 0.3
        elif spread < -0.15:
            score -= 0.3

    # Momentum: MACD
    if m_hist is not None:
        if m_hist > 0:
            score += 0.8
            reasons.append("MACD histogram positive")
        elif m_hist < 0:
            score -= 0.8
            reasons.append("MACD histogram negative")
    if m_line is not None and m_sig is not None:
        if m_line > m_sig:
            score += 0.4
        elif m_line < m_sig:
            score -= 0.4

    # RSI zones
    if r is not None:
        if r >= 75:
            score -= 1.5
            reasons.append(f"RSI {r:.0f} — overbought, reversal risk")
        elif r >= 60:
            score += 0.5
        elif r >= 50:
            score += 0.2
        elif r >= 40:
            score -= 0.2
        elif r >= 25:
            score -= 0.5
        else:
            score += 1.5
            reasons.append(f"RSI {r:.0f} — oversold, bounce setup")

    # Stochastic
    if k_st is not None and d_st is not None:
        if k_st > d_st:
            score += 0.5
        elif k_st < d_st:
            score -= 0.5
        if k_st >= 85 and d_st >= 80:
            score -= 0.5
            reasons.append(f"Stochastic {k_st:.0f} — overbought")
        elif k_st <= 15 and d_st <= 20:
            score += 0.5
            reasons.append(f"Stochastic {k_st:.0f} — oversold")

    # Bollinger position
    if bb_up and bb_low and bb_up > bb_low:
        pos = (price - bb_low) / (bb_up - bb_low)
        if pos >= 1.0:
            score -= 0.8
            reasons.append("Price above upper Bollinger — extended")
        elif pos <= 0.0:
            score += 0.8
            reasons.append("Price below lower Bollinger — stretched low")
        elif pos >= 0.85:
            score -= 0.3
        elif pos <= 0.15:
            score += 0.3

    # VWAP
    if vp:
        if price > vp * 1.0005:
            score += 0.7
            reasons.append("Price above VWAP — buyers in control")
        elif price < vp * 0.9995:
            score -= 0.7
            reasons.append("Price below VWAP — sellers in control")

    # Volume
    if obv is not None and abs(obv) > 0:
        if obv > 0:
            score += 0.4
        else:
            score -= 0.4

    # Candlestick pattern
    if pattern == "bullish engulfing" or pattern == "hammer":
        score += 1.0
        reasons.append(f"{pattern.title()} pattern")
    elif pattern == "bearish engulfing" or pattern == "shooting star":
        score -= 1.0
        reasons.append(f"{pattern.title()} pattern")
    elif pattern == "doji":
        reasons.append("Doji — indecision")

    verdict = (
        "STRONG BUY" if score >= 3.0 else
        "BUY" if score >= 1.2 else
        "NEUTRAL" if score > -1.2 else
        "SELL" if score > -3.0 else
        "STRONG SELL"
    )
    return {
        "label": label,
        "verdict": verdict,
        "score": round(score, 2),
        "indicators": {
            "ema9": round(e9, 4) if e9 else None,
            "ema21": round(e21, 4) if e21 else None,
            "rsi": round(r, 1) if r is not None else None,
            "macd_hist": round(m_hist, 4) if m_hist is not None else None,
            "stoch_k": round(k_st, 1) if k_st is not None else None,
            "stoch_d": round(d_st, 1) if d_st is not None else None,
            "bb_upper": round(bb_up, 4) if bb_up else None,
            "bb_lower": round(bb_low, 4) if bb_low else None,
            "vwap": round(vp, 4) if vp else None,
            "atr": round(a, 4) if a else None,
            "pattern": pattern,
        },
        "reasons": reasons[:6],
        "resistance": sorted([x for x in res if x > price], reverse=True)[:2],
        "support": sorted([x for x in sup if x < price])[:2],
    }


def _rough_atr(bars: list[dict]) -> Optional[float]:
    """Mean true range over the last few bars — a stop-distance estimate for
    series too short for a full ATR."""
    if len(bars) < 2:
        return None
    trs = []
    for i in range(max(1, len(bars) - 14), len(bars)):
        b, p = bars[i], bars[i - 1]
        trs.append(max(b["high"] - b["low"], abs(b["high"] - p["close"]), abs(b["low"] - p["close"])))
    return sum(trs) / len(trs) if trs else None


def assess(bars_1m: list[dict], bars_5m: list[dict]) -> dict:
    """Combined multi-timeframe signal with prediction."""
    a1 = _timeframe_analysis(bars_1m, "1m")
    a5 = _timeframe_analysis(bars_5m, "5m")
    s1, s5 = a1["score"], a5["score"]
    combined = 0.62 * s5 + 0.38 * s1
    same_direction = (s1 > 0 and s5 > 0) or (s1 < 0 and s5 < 0)
    confluence = same_direction and abs(s1) >= 1.2 and abs(s5) >= 1.2
    if confluence:
        combined *= 1.1
    confidence = min(94, round(45 + abs(combined) * 9 + (8 if confluence else 0)))

    verdict = (
        "STRONG BUY" if combined >= 3.0 else
        "BUY" if combined >= 1.2 else
        "NEUTRAL" if combined > -1.2 else
        "SELL" if combined > -3.0 else
        "STRONG SELL"
    )
    direction = "BUY" if combined > 0.3 else ("SELL" if combined < -0.3 else "NEUTRAL")

    # Prediction: target = nearest S/R, stop = ATR-based. Falls back gracefully
    # so a prediction always renders when any bars exist at all.
    price = (bars_5m[-1]["close"] if bars_5m else (bars_1m[-1]["close"] if bars_1m else None))
    atr5 = a5["indicators"].get("atr")
    if price:
        if atr5 is None or atr5 <= 0:
            atr5 = _rough_atr(bars_5m or bars_1m) or price * 0.002
        target = stop = rr = None
        if direction == "BUY":
            target = a5["resistance"][0] if a5["resistance"] else price + 3 * atr5
            support = a5["support"][0] if a5["support"] else price - 2 * atr5
            stop = min(support, price - 1.5 * atr5) if a5["support"] else price - 2 * atr5
            if target <= price:
                target = price + 3 * atr5
            # A pivot that sits 2 pips away makes a degenerate R:R — extend the
            # target so the trade always pays at least 1.5x its risk.
            if target - price < 1.5 * (price - stop):
                target = price + 1.5 * (price - stop)
        elif direction == "SELL":
            target = a5["support"][0] if a5["support"] else price - 3 * atr5
            resistance = a5["resistance"][0] if a5["resistance"] else price + 2 * atr5
            stop = max(resistance, price + 1.5 * atr5) if a5["resistance"] else price + 2 * atr5
            if target >= price:
                target = price - 3 * atr5
            if price - target < 1.5 * (stop - price):
                target = price - 1.5 * (stop - price)
        else:
            target = a5["resistance"][0] if a5["resistance"] else price + 2 * atr5
            support = a5["support"][0] if a5["support"] else price - 2 * atr5
            stop = support if support and support < price else price - 1.5 * atr5
            if target - price < 1.5 * (price - stop):
                target = price + 1.5 * (price - stop)
        if target and stop and stop != price:
            rr = round(abs(target - price) / abs(stop - price), 2)

        return {
            "verdict": verdict,
            "direction": direction,
            "score": round(combined, 2),
            "confidence": confidence,
            "confluence": confluence,
            "timeframes": {"1m": a1, "5m": a5},
            "prediction": {
                "entry": price,
                "target": round(target, 4) if target else None,
                "stop": round(stop, 4) if stop else None,
                "rr": rr,
            },
            "reasons": a5["reasons"][:4] + a1["reasons"][:2],
        }

    return {
        "verdict": verdict,
        "direction": direction,
        "score": round(combined, 2),
        "confidence": confidence,
        "confluence": confluence,
        "timeframes": {"1m": a1, "5m": a5},
        "prediction": {
            "entry": None,
            "target": None,
            "stop": None,
            "rr": None,
        },
        "reasons": a5["reasons"][:4] + a1["reasons"][:2],
    }
