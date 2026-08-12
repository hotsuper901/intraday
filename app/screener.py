"""Screener filtering/sorting over already-computed metric rows. Pure and testable."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ScreenParams:
    min_change: float | None = None
    max_change: float | None = None
    min_relvol: float | None = None
    min_price: float | None = None
    max_price: float | None = None
    sort: str = "change_desc"  # change_desc | change_asc | relvol | atr | rsi
    direction: str = "any"  # any | up | down
    limit: int = 50


def _passes_change(chg: float | None, p: ScreenParams) -> bool:
    if p.min_change is None:
        return True
    if chg is None:
        return False
    if p.direction == "up":
        return chg >= p.min_change
    if p.direction == "down":
        return chg <= -p.min_change
    return abs(chg) >= p.min_change


def apply_filters(rows: list[dict], p: ScreenParams) -> list[dict]:
    out: list[dict] = []
    for r in rows:
        price = r.get("price")
        if price is None:
            continue
        if p.min_price is not None and price < p.min_price:
            continue
        if p.max_price is not None and price > p.max_price:
            continue
        chg = r.get("change_pct")
        if not _passes_change(chg, p):
            continue
        if p.max_change is not None and (chg is None or chg > p.max_change):
            continue
        rv = r.get("rel_vol")
        if p.min_relvol is not None and (rv is None or rv < p.min_relvol):
            continue
        out.append(r)
    key = {
        "change_desc": lambda r: -(r.get("change_pct") or -9999),
        "change_asc": lambda r: r.get("change_pct") or 9999,
        "relvol": lambda r: -(r.get("rel_vol") or 0),
        "atr": lambda r: -(r.get("atr_pct") or 0),
        "rsi": lambda r: -(r.get("rsi") or 0),
    }.get(p.sort, lambda r: -(r.get("change_pct") or -9999))
    return sorted(out, key=key)[: p.limit]


def parse_screen_params(
    min_change: str | None,
    min_relvol: str | None,
    min_price: str | None,
    max_price: str | None,
    sort: str | None,
    direction: str | None = None,
) -> ScreenParams:
    def f(v, d=None):
        try:
            return float(v) if v not in (None, "") else d
        except ValueError:
            return d

    return ScreenParams(
        min_change=f(min_change),
        min_relvol=f(min_relvol),
        min_price=f(min_price),
        max_price=f(max_price),
        sort=sort if sort in {"change_desc", "change_asc", "relvol", "atr", "rsi"} else "change_desc",
        direction=direction if direction in {"any", "up", "down"} else "any",
    )
