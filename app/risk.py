"""Entry risk check: a small rule engine that keeps you out of bad entries.

Pure function of its inputs — no market data calls, no DB. This is what the
tests pin down.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import config


@dataclass
class RiskInput:
    price: float
    entry: float | None = None
    stop: float | None = None
    account: float = 25_000.0
    risk_pct: float = 1.0
    atr_pct: float | None = None
    rel_vol: float | None = None
    from_open_pct: float | None = None
    minutes_in_session: int | None = None
    max_position_pct: float = config.MAX_POSITION_PCT
    # Session state: "pre", "open", "after", "closed"
    session_state: str = "open"
    # Sizing: equities use whole shares; crypto/fx allow fractional units
    asset: str = "equity"


def _fmt_shares(q: float) -> str:
    if q >= 1:
        return str(int(q))
    return f"{q:.4f}".rstrip("0").rstrip(".")


@dataclass
class RiskResult:
    verdict: str  # GO | CAUTION | AVOID | INVALID
    reasons: list[str] = field(default_factory=list)
    shares: int = 0
    dollar_risk: float = 0.0
    stop_dist_pct: float = 0.0
    capped: bool = False
    entry: float = 0.0
    stop: float = 0.0


def assess(ri: RiskInput) -> RiskResult:
    entry = ri.entry if ri.entry else ri.price
    reasons: list[str] = []
    avoid: list[str] = []
    caution: list[str] = []

    # --- Sanity checks ----------------------------------------------------
    if ri.price <= 0 or entry <= 0:
        return RiskResult("INVALID", ["price must be positive"])
    if ri.stop is None or ri.stop <= 0:
        return RiskResult("INVALID", ["a stop below entry is required"])
    if ri.stop >= entry:
        return RiskResult("INVALID", ["stop must be below entry"])
    if ri.risk_pct <= 0 or ri.risk_pct > 10:
        return RiskResult("INVALID", ["risk % must be between 0 and 10"])
    if ri.account <= 0:
        return RiskResult("INVALID", ["account size must be positive"])

    # --- Session timing rules ---------------------------------------------
    mins = ri.minutes_in_session
    if ri.session_state == "pre":
        avoid.append("market not open — no entries pre-market")
    elif ri.session_state == "after":
        avoid.append("market closed — no entries after hours")
    elif ri.session_state == "closed":
        avoid.append("market closed — no entries right now")
    elif mins is not None:
        if mins < config.OPEN_QUIET_MINUTES:
            avoid.append("first 5 minutes: opening auction noise, let a range form")
        if mins > config.SESSION_CLOSE_MINUTES - config.CLOSE_QUIET_MINUTES - config.SESSION_OPEN_MINUTES:
            avoid.append("last 10 minutes: no time for a setup to work, flat into the close")

    # --- Volatility rules --------------------------------------------------
    if ri.atr_pct is not None:
        if ri.atr_pct > 5.0:
            avoid.append(f"ATR {ri.atr_pct:.1f}% — volatility too high for a defined stop")
        elif ri.atr_pct > 3.0:
            caution.append(f"ATR {ri.atr_pct:.1f}% — wide stops, size down")

    # --- Participation rules -----------------------------------------------
    if ri.rel_vol is not None:
        if ri.rel_vol < 0.3:
            avoid.append(f"relative volume {ri.rel_vol:.2f}x — nobody is trading this right now")
        elif ri.rel_vol < 0.6:
            caution.append(f"relative volume {ri.rel_vol:.2f}x — thin tape, fills may slip")

    # --- Extension rules ----------------------------------------------------
    if ri.from_open_pct is not None:
        ext = abs(ri.from_open_pct)
        if ext > 8.0:
            avoid.append(f"{ri.from_open_pct:+.1f}% from open — chasing an extended move")
        elif ext > 5.0:
            caution.append(f"{ri.from_open_pct:+.1f}% from open — late in the move")

    # --- Position sizing ----------------------------------------------------
    stop_dist = entry - ri.stop
    stop_dist_pct = stop_dist / entry * 100.0
    risk_amount = ri.account * ri.risk_pct / 100.0
    raw_shares = risk_amount / stop_dist
    max_shares = ri.account * ri.max_position_pct / 100.0 / entry
    capped = raw_shares > max_shares
    if capped:
        raw_shares = max_shares
    if ri.asset in ("crypto", "fx"):
        shares = round(raw_shares, 4)  # fractional coins / lots
    else:
        shares = int(raw_shares)
    if capped:
        caution.append(
            f"position capped at {ri.max_position_pct:.0f}% of account "
            f"({_fmt_shares(shares)}) — stop is wider than the risk allows"
        )
    if shares <= 0:
        avoid.append("risk math produces zero shares — stop too wide for this account")

    # --- Verdict ------------------------------------------------------------
    if avoid:
        verdict = "AVOID"
        reasons = avoid + caution
    elif caution:
        verdict = "CAUTION"
        reasons = caution
    else:
        verdict = "GO"
        reasons = ["no rule violations — setup is tradeable"]

    return RiskResult(
        verdict=verdict,
        reasons=reasons,
        shares=shares,
        dollar_risk=round(shares * stop_dist, 2),
        stop_dist_pct=round(stop_dist_pct, 2),
        capped=capped,
        entry=entry,
        stop=ri.stop,
    )
