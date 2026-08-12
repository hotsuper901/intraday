"""Unit tests for screener filtering and sorting."""
from app.screener import ScreenParams, apply_filters, parse_screen_params


def make_row(ticker, price, chg, relvol=1.0, atr=1.0, rsi=50.0):
    return {
        "ticker": ticker, "name": f"{ticker} Inc", "price": price,
        "change_pct": chg, "rel_vol": relvol, "atr_pct": atr, "rsi": rsi,
    }


ROWS = [
    make_row("AAA", 10.0, 4.5, 2.5, 1.2, 62),
    make_row("BBB", 200.0, -1.2, 0.9, 0.8, 45),
    make_row("CCC", 50.0, 0.4, 0.4, 3.4, 30),
    make_row("DDD", 30.0, 2.1, 1.8, 2.2, 71),
]


def test_no_filters_returns_all_sorted_by_change_desc():
    out = apply_filters(ROWS, ScreenParams())
    assert [r["ticker"] for r in out] == ["AAA", "DDD", "CCC", "BBB"]


def test_min_change_filter_direction_any():
    out = apply_filters(ROWS, ScreenParams(min_change=2.0))
    assert [r["ticker"] for r in out] == ["AAA", "DDD"]


def test_min_change_filter_direction_up():
    out = apply_filters(ROWS, ScreenParams(min_change=1.0, direction="up"))
    assert [r["ticker"] for r in out] == ["AAA", "DDD"]


def test_min_change_filter_direction_down():
    out = apply_filters(ROWS, ScreenParams(min_change=1.0, direction="down"))
    assert [r["ticker"] for r in out] == ["BBB"]


def test_direction_matches_movers_both_ways():
    # a down-mover with strong absolute change passes with direction=any
    rows = ROWS + [make_row("EEE", 40.0, -6.0, 2.2)]
    out = apply_filters(rows, ScreenParams(min_change=1.5, min_relvol=1.5, direction="any"))
    assert set(r["ticker"] for r in out) == {"AAA", "DDD", "EEE"}
    out_up = apply_filters(rows, ScreenParams(min_change=1.5, min_relvol=1.5, direction="up"))
    assert set(r["ticker"] for r in out_up) == {"AAA", "DDD"}


def test_price_range_filter():
    out = apply_filters(ROWS, ScreenParams(min_price=10.0, max_price=100.0))
    assert [r["ticker"] for r in out] == ["AAA", "DDD", "CCC"]


def test_min_relvol_filter():
    out = apply_filters(ROWS, ScreenParams(min_relvol=1.0))
    assert [r["ticker"] for r in out] == ["AAA", "DDD"]


def test_sort_by_relvol():
    out = apply_filters(ROWS, ScreenParams(sort="relvol"))
    assert out[0]["ticker"] == "AAA"
    assert out[-1]["ticker"] == "CCC"


def test_limit():
    out = apply_filters(ROWS, ScreenParams(limit=2))
    assert len(out) == 2


def test_parse_params_defaults_and_bad_input():
    p = parse_screen_params("", "", "", "", "bogus")
    assert p.sort == "change_desc"
    assert p.min_change is None
    p = parse_screen_params("1.5", "2", "10", "200", "atr")
    assert p.min_change == 1.5
    assert p.min_relvol == 2.0
    assert p.min_price == 10.0
    assert p.max_price == 200.0
    assert p.sort == "atr"
    assert parse_screen_params("abc", None, None, None, None).min_change is None
