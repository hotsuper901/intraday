"""Pure indicator math over 5-minute bars. No I/O here, so it's trivially testable."""
from __future__ import annotations


def typical_price(bar: dict) -> float:
    return (bar["high"] + bar["low"] + bar["close"]) / 3.0


def rel_volume(bars: list[dict]) -> float:
    """Last bar volume vs mean of prior 20 bars (session-adjusted feel)."""
    if len(bars) < 2:
        return 1.0
    prior = [b["volume"] for b in bars[-21:-1]]
    prior = [v for v in prior if v and v > 0]
    if not prior or not prior[-1]:
        return 1.0
    last = bars[-1]["volume"] or 0
    return last / (sum(prior) / len(prior))


def atr_pct(bars: list[dict], period: int = 14) -> float:
    """Average true range as % of current close."""
    if len(bars) < period + 1:
        return 0.0
    trs: list[float] = []
    for i in range(len(bars) - period, len(bars)):
        b, p = bars[i], bars[i - 1]
        trs.append(max(b["high"] - b["low"], abs(b["high"] - p["close"]), abs(b["low"] - p["close"])))
    atr = sum(trs) / len(trs)
    close = bars[-1]["close"]
    return (atr / close * 100.0) if close else 0.0


def rsi14(bars: list[dict]) -> float:
    """Wilder RSI on closes. Returns 50.0 when history is too short."""
    if len(bars) < 15:
        return 50.0
    closes = [b["close"] for b in bars]
    gains, losses = [], []
    for i in range(1, 15):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_gain = sum(gains) / 14
    avg_loss = sum(losses) / 14
    for i in range(15, len(closes)):
        d = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * 13 + max(d, 0.0)) / 14
        avg_loss = (avg_loss * 13 + max(-d, 0.0)) / 14
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def vwap(bars: list[dict]) -> float:
    """Session volume-weighted average price."""
    pv = sum(typical_price(b) * (b["volume"] or 0) for b in bars)
    vol = sum(b["volume"] or 0 for b in bars)
    return (pv / vol) if vol else 0.0


def compute_metrics(
    ticker: str,
    bars: list[dict],
    prev_close: float | None,
) -> dict:
    """Everything the screener and detail page need, from one bar list.

    The session-open price is the open of the first stored bar of the day.
    """
    out: dict = {
        "ticker": ticker,
        "bars": len(bars),
        "last_ts": None,
        "price": None,
        "change_pct": None,
        "from_open_pct": None,
        "vwap_dist_pct": None,
        "rel_vol": None,
        "atr_pct": None,
        "rsi": None,
        "vwap": None,
        "day_high": None,
        "day_low": None,
        "day_volume": None,
        "session_open": None,
    }
    if not bars:
        return out
    last = bars[-1]
    session_open = bars[0]["open"]
    out["last_ts"] = last["ts"]
    out["price"] = last["close"]
    out["day_high"] = max(b["high"] for b in bars)
    out["day_low"] = min(b["low"] for b in bars)
    out["day_volume"] = sum(b["volume"] or 0 for b in bars)
    out["rel_vol"] = round(rel_volume(bars), 2)
    out["atr_pct"] = round(atr_pct(bars), 2)
    out["rsi"] = round(rsi14(bars), 1)
    out["vwap"] = round(vwap(bars), 4)
    if prev_close and prev_close > 0:
        out["change_pct"] = round((last["close"] - prev_close) / prev_close * 100, 2)
    if session_open and session_open > 0:
        out["from_open_pct"] = round((last["close"] - session_open) / session_open * 100, 2)
        out["session_open"] = session_open
    if out["vwap"]:
        out["vwap_dist_pct"] = round((last["close"] - out["vwap"]) / out["vwap"] * 100, 2)
    return out
