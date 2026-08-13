"""Tests for the signal engine."""
from app import signals


def make_bars(closes, spread=0.1):
    bars = []
    for i, c in enumerate(closes):
        bars.append(
            {
                "ts": 1000 + i * 60,
                "open": closes[i - 1] if i else c - spread / 2,
                "high": c + spread,
                "low": c - spread,
                "close": c,
                "volume": 1000 + i * 10,
            }
        )
    return bars


def uptrend(n=120):
    return make_bars([100.0 + i * 0.5 for i in range(n)])


def downtrend(n=120):
    return make_bars([200.0 - i * 0.5 for i in range(n)])


def flat(n=120):
    # truly flat: constant closes, tiny wicks
    return make_bars([150.0 for _ in range(n)])


def test_uptrend_signals_buy():
    a = signals._timeframe_analysis(uptrend(), "5m")
    assert a["verdict"] in ("BUY", "STRONG BUY")
    assert a["score"] > 0.5


def test_downtrend_signals_sell():
    a = signals._timeframe_analysis(downtrend(), "5m")
    assert a["verdict"] in ("SELL", "STRONG SELL")
    assert a["score"] < -0.5


def test_flat_market_is_neutralish():
    a = signals._timeframe_analysis(flat(), "5m")
    assert a["verdict"] == "NEUTRAL"


def test_oversold_rsi_gives_bullish_reason():
    # long decline: the last 14 bars all falling → RSI pinned low
    closes = [300.0 - i * 2 for i in range(110)]
    a = signals._timeframe_analysis(make_bars(closes), "5m")
    assert a["indicators"]["rsi"] is not None and a["indicators"]["rsi"] < 30
    assert any("oversold" in r for r in a["reasons"])


def test_resample_aggregates_5m():
    bars = []
    for i in range(15):
        bars.append(
            {"ts": 1000 + i * 60, "open": i, "high": i + 1, "low": i - 1, "close": i + 0.5, "volume": 10}
        )
    out = signals.resample(bars, 5)
    assert len(out) == 3
    assert out[0]["ts"] == 1000
    assert out[0]["open"] == 0
    assert out[0]["high"] == 5
    assert out[0]["close"] == 4.5
    assert out[0]["volume"] == 50


def test_confluence_boosts_confidence():
    u = uptrend(120)
    u2 = make_bars([100.0 + i * 0.3 for i in range(120)])  # independent uptrend
    combined = signals.assess(u2, u)
    assert combined["confluence"] is True
    assert combined["verdict"] in ("BUY", "STRONG BUY")
    assert combined["confidence"] >= 60


def test_same_series_never_counts_as_confluence():
    # Degraded mode analyzes the same bars on both slots — that must not
    # trigger the confluence boost or claim 1m+5m agreement.
    u = uptrend(120)
    r = signals.assess(u, u)
    assert r["confluence"] is False
    r2 = signals.assess(list(u), list(u))  # copies with identical data
    assert r2["confluence"] is False


def test_opposing_timeframes_are_neutral():
    up, down = uptrend(), downtrend()
    r = signals.assess(down, up)  # 1m down, 5m up — conflicting
    assert r["confluence"] is False
    assert r["verdict"] in ("NEUTRAL", "BUY", "SELL")  # no extreme


def test_buy_prediction_has_target_above_entry():
    r = signals.assess(uptrend(), uptrend())
    p = r["prediction"]
    assert p["entry"] and p["target"] and p["stop"]
    assert p["target"] > p["entry"] > p["stop"]
    assert p["rr"] >= 0.5


def test_sell_prediction_has_target_below_entry():
    r = signals.assess(downtrend(), downtrend())
    p = r["prediction"]
    assert p["entry"] and p["target"] and p["stop"]
    assert p["target"] < p["entry"] < p["stop"]
    assert p["rr"] >= 0.5


def test_not_enough_bars():
    a = signals._timeframe_analysis(make_bars([10, 11, 12]), "1m")
    assert a["verdict"] == "NO DATA"


def test_patterns_detected():
    # two bars forming a bullish engulfing at the end of a flat series
    bars = flat(100)
    bars[-2] = dict(bars[-2], open=151.0, close=149.0, high=151.2, low=148.8)
    bars[-1] = dict(bars[-1], open=148.6, close=151.6, high=151.8, low=148.4)
    a = signals._timeframe_analysis(bars, "5m")
    assert a["indicators"]["pattern"] == "bullish engulfing"
    assert any("Engulfing" in r for r in a["reasons"])
