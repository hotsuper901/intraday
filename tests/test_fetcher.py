"""Tests for asset classification and trading calendars."""
from datetime import datetime
from zoneinfo import ZoneInfo

from app.fetcher import asset_class, market_state

ET = ZoneInfo("America/New_York")


def test_asset_class_rules():
    assert asset_class("AAPL") == "equity"
    assert asset_class("BRK-B") == "equity"  # dashed but not a crypto pair
    assert asset_class("BTC-USD") == "crypto"
    assert asset_class("ETH-EUR") == "crypto"
    assert asset_class("DOGE-USDT") == "crypto"
    assert asset_class("EURUSD=X") == "fx"
    assert asset_class("usdjpy=x") == "fx"


def test_crypto_always_open_even_saturday_3am():
    sat = datetime(2026, 8, 15, 3, 0, tzinfo=ET)
    state, mins = market_state(sat, asset="crypto")
    assert state == "open"
    assert mins is None


def test_fx_weekend_closed_sat():
    sat = datetime(2026, 8, 15, 12, 0, tzinfo=ET)
    assert market_state(sat, asset="fx")[0] == "closed"


def test_fx_sunday_opens_at_17et():
    before = datetime(2026, 8, 16, 16, 59, tzinfo=ET)
    after = datetime(2026, 8,16, 17, 1, tzinfo=ET)
    assert market_state(before, asset="fx")[0] == "closed"
    assert market_state(after, asset="fx")[0] == "open"


def test_fx_friday_closes_at_17et():
    before = datetime(2026, 8, 14, 16, 30, tzinfo=ET)
    after = datetime(2026, 8, 14, 17, 5, tzinfo=ET)
    assert market_state(before, asset="fx")[0] == "open"
    assert market_state(after, asset="fx")[0] == "closed"


def test_fx_open_overnight_monday_3am():
    mon = datetime(2026, 8, 10, 3, 0, tzinfo=ET)
    assert market_state(mon, asset="fx")[0] == "open"


def test_equity_calendar_unchanged():
    pre = datetime(2026, 8, 10, 9, 15, tzinfo=ET)
    open_ = datetime(2026, 8, 10, 10, 0, tzinfo=ET)
    after = datetime(2026, 8, 10, 16, 5, tzinfo=ET)
    sat = datetime(2026, 8, 15, 10, 0, tzinfo=ET)
    assert market_state(pre)[0] == "pre"
    assert market_state(open_)[0] == "open"
    assert market_state(open_)[1] == 30
    assert market_state(after)[0] == "after"
    assert market_state(sat)[0] == "closed"


def test_crypto_market_state_ignores_equity_clock():
    # 2am ET on a Tuesday: equities pre-market, crypto open
    tue_2am = datetime(2026, 8, 11, 2, 0, tzinfo=ET)
    assert market_state(tue_2am)[0] == "pre"
    assert market_state(tue_2am, asset="crypto")[0] == "open"
