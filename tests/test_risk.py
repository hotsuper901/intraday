"""Unit tests for the entry risk engine."""
import pytest

from app import risk


def base_input(**overrides):
    kw = dict(
        price=100.0,
        entry=100.0,
        stop=99.0,
        account=25_000.0,
        risk_pct=1.0,
        atr_pct=1.2,
        rel_vol=1.5,
        from_open_pct=0.8,
        minutes_in_session=60,
        session_state="open",
    )
    kw.update(overrides)
    return risk.RiskInput(**kw)


def test_clean_setup_is_go():
    r = risk.assess(base_input(stop=95.0))
    assert r.verdict == "GO"
    # risk_amount = 250; stop distance = 5.0 → 50 shares, $250 risk
    assert r.shares == 50
    assert r.dollar_risk == 250.0
    assert r.stop_dist_pct == 5.0


def test_first_five_minutes_is_avoid():
    r = risk.assess(base_input(minutes_in_session=2))
    assert r.verdict == "AVOID"
    assert any("first 5 minutes" in x for x in r.reasons)


def test_last_ten_minutes_is_avoid():
    # session = 390 minutes; 385 min in → last 5 minutes
    r = risk.assess(base_input(minutes_in_session=385))
    assert r.verdict == "AVOID"
    assert any("last 10 minutes" in x for x in r.reasons)


def test_premarket_is_avoid():
    r = risk.assess(base_input(session_state="pre", minutes_in_session=None))
    assert r.verdict == "AVOID"


def test_market_closed_is_avoid():
    # fx weekend / equity weekend — 'closed' must never fall through silently
    r = risk.assess(base_input(session_state="closed", minutes_in_session=None))
    assert r.verdict == "AVOID"
    assert any("closed" in x for x in r.reasons)


def test_crypto_open_24_7_timing_rules_do_not_apply():
    # crypto: session open, no minutes → no opening-auction / close rules
    r = risk.assess(base_input(session_state="open", minutes_in_session=None, stop=95.0))
    assert r.verdict == "GO"


def test_high_atr_is_avoid():
    r = risk.assess(base_input(atr_pct=6.2))
    assert r.verdict == "AVOID"
    assert any("volatility too high" in x for x in r.reasons)


def test_moderate_atr_is_caution():
    r = risk.assess(base_input(atr_pct=3.5))
    assert r.verdict == "CAUTION"


def test_extended_move_is_avoid():
    r = risk.assess(base_input(from_open_pct=9.5))
    assert r.verdict == "AVOID"
    assert any("extended" in x for x in r.reasons)
    r2 = risk.assess(base_input(from_open_pct=-12.0))
    assert r2.verdict == "AVOID"


def test_thin_volume_is_caution():
    r = risk.assess(base_input(rel_vol=0.4))
    assert r.verdict == "CAUTION"
    assert any("thin tape" in x for x in r.reasons)


def test_no_participation_is_avoid():
    r = risk.assess(base_input(rel_vol=0.1))
    assert r.verdict == "AVOID"


def test_stop_above_entry_is_invalid():
    r = risk.assess(base_input(stop=101.0))
    assert r.verdict == "INVALID"


def test_bad_risk_pct_is_invalid():
    r = risk.assess(base_input(risk_pct=0))
    assert r.verdict == "INVALID"
    r = risk.assess(base_input(risk_pct=25))
    assert r.verdict == "INVALID"


def test_position_capped_at_max_account_pct():
    # stop 10% away → raw shares = 250 / 10 = 25 shares
    # cap = 25000 * 25% / 100 = 62 shares → not capped
    r = risk.assess(base_input(stop=90.0))
    assert not r.capped
    assert r.shares == 25
    # stop 40% away → raw shares = 250 / 40 = 6 shares, still under cap
    r = risk.assess(base_input(stop=60.0))
    assert r.shares == 6
    # tiny account: cap = 1000*25%/100 = 2 shares, raw = 10/1 = 10 → capped at 2
    r = risk.assess(base_input(account=1000.0, stop=99.0))
    assert r.capped
    assert r.shares == 2


def test_crypto_uses_fractional_shares():
    # BTC: 1% risk on a 1% stop is a 100% position → capped at 25% of account,
    # expressed as a fractional coin amount rather than rounding to zero.
    r = risk.assess(base_input(
        asset="crypto", price=97000.0, entry=97000.0, stop=96030.0,
        account=100_000.0, risk_pct=1.0,
    ))
    assert r.capped
    assert r.shares == 0.2577  # 25% of account / entry
    assert r.dollar_risk == round(0.2577 * 970.0, 2)
    assert r.verdict == "CAUTION"


def test_crypto_go_with_fractional_sizing():
    r = risk.assess(base_input(
        asset="crypto", price=97000.0, entry=97000.0, stop=94090.0,
        account=100_000.0, risk_pct=0.5,
    ))
    # risk_amount = 500; stop distance = 2910 → 0.1718 BTC, under the 0.2577 cap
    assert r.verdict == "GO"
    assert r.shares == 0.1718


def test_fx_fractional_shares():
    r = risk.assess(base_input(
        asset="fx", price=1.08, entry=1.08, stop=1.07,
        account=25_000.0, risk_pct=1.0,
    ))
    assert r.capped
    assert r.shares == round(25000.0 * 0.25 / 1.08, 4)


def test_equity_still_integer_shares():
    r = risk.assess(base_input(stop=95.0))
    assert isinstance(r.shares, int)
    assert r.shares == 50


def test_zero_shares_is_avoid():
    # risk_amount = 25 * 0.1% = 2.5; stop distance 90 → 0 shares
    r = risk.assess(base_input(account=2500.0, risk_pct=0.1, stop=10.0))
    assert r.verdict == "AVOID"
    assert any("zero shares" in x for x in r.reasons)


def test_missing_stop_is_invalid():
    r = risk.assess(base_input(stop=None))
    assert r.verdict == "INVALID"
