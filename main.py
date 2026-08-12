"""Intraday Radar — Vercel entrypoint (also runnable via `uvicorn main:app`).

Vercel's Python runtime auto-detects FastAPI and loads an entrypoint app that
routes every request. We pin this module via `tool.vercel.entrypoint` in
pyproject.toml so Vercel uses THIS app — a single FastAPI application that
serves the static frontend from public/ plus the /api endpoints, fetching
market data on demand (no background poller on serverless).
"""
import asyncio
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Make the repo root importable no matter how the module is loaded.
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Set before app.config reads the environment.
os.environ.setdefault("DB_PATH", "/tmp/market.db")
os.environ.setdefault("DATA_MODE", "live")

import httpx  # noqa: E402
from fastapi import FastAPI, HTTPException, Query  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from app import config, db, indicators, risk  # noqa: E402
from app import screener as screenlib  # noqa: E402
from app.fetcher import (  # noqa: E402
    ET,
    LAST_FETCH_ERRORS,
    asset_class,
    fetch_cnbc_quote,
    fetch_demo,
    fetch_ticker,
    market_state,
)
from app.fetcher import _fetch_finnhub_quote as fetch_finnhub_quote  # noqa: E402

app = FastAPI(title="Intraday Radar")


# --------------------------------------------------------------------------
# On-demand fetch layer with a per-warm-instance TTL cache
# --------------------------------------------------------------------------
_INSTANCE_CACHE: dict[str, tuple[float, list, dict | None, str]] = {}
_TTL_SECONDS = 25.0


async def bars_for(ticker: str, client: httpx.AsyncClient) -> tuple[list, dict | None, str]:
    """Bars, meta, and source ('live' | 'demo' | 'none') for one symbol."""
    now = time.time()
    hit = _INSTANCE_CACHE.get(ticker)
    if hit and now - hit[0] < _TTL_SECONDS:
        return hit[1], hit[2], hit[3]

    source = "live"
    if config.DATA_MODE == "demo":
        source = "demo"
        db.init_db()
        data = await asyncio.to_thread(fetch_demo, ticker)
    else:
        data = await fetch_ticker(client, ticker)
        if not data and config.LIVE_FALLBACK_TO_DEMO:
            source = "demo"
            db.init_db()
            data = await asyncio.to_thread(fetch_demo, ticker)
        if not data and config.SERVERLESS and asset_class(ticker) == "fx":
            # No keyless FX candle source serves datacenter IPs (Yahoo/Binance/
            # Bybit/Stooq/Dukascopy all refuse). Fill FX with clearly-labeled
            # demo bars so every symbol has candles on Vercel.
            source = "demo"
            db.init_db()
            data = await asyncio.to_thread(fetch_demo, ticker)
        if not data:
            # Quote-level fallback: Finnhub (key) then CNBC — keeps the
            # screener alive with real prices when candles are unavailable.
            q = await fetch_finnhub_quote(client, ticker) or await fetch_cnbc_quote(client, ticker)
            if q:
                meta = {"name": q["name"], "quote": q}
                _INSTANCE_CACHE[ticker] = (now, [], meta, "quote")
                return [], meta, "quote"

    if not data:
        _INSTANCE_CACHE[ticker] = (now, [], None, "none")
        return [], None, "none"
    bars = data["bars"]
    meta = {
        "name": data["name"],
        "prev_close": data["prev_close"],
        "session_open": data["session_open"],
    }
    _INSTANCE_CACHE[ticker] = (now, bars, meta, source)
    return bars, meta, source


def metrics_for(ticker: str, meta: dict | None, bars: list, source: str = "live") -> dict:
    if source == "quote" and meta and meta.get("quote"):
        q = meta["quote"]
        price = q.get("price")
        open_p = q.get("open")
        m = {
            "ticker": ticker,
            "bars": 0,
            "last_ts": q.get("ts"),
            "price": price,
            "change_pct": q.get("change_pct"),
            "from_open_pct": round((price - open_p) / open_p * 100, 2) if price and open_p else None,
            "vwap_dist_pct": None,
            "rel_vol": None,
            "atr_pct": None,
            "rsi": None,
            "vwap": None,
            "day_high": q.get("high"),
            "day_low": q.get("low"),
            "day_volume": q.get("volume"),
            "session_open": open_p,
        }
    else:
        m = indicators.compute_metrics(ticker, bars, meta.get("prev_close") if meta else None)
    m["name"] = (meta or {}).get("name") or ticker
    m["source"] = source
    m["asset"] = asset_class(ticker)
    return m


def _norm_result(res):
    if isinstance(res, BaseException):
        return [], None, "none"
    return res


async def screener_rows() -> tuple[list[dict], int]:
    async with httpx.AsyncClient() as client:
        coros = [bars_for(t, client) for t in config.WATCHLIST]
        if config.SERVERLESS:
            # Vercel functions have a hard time budget: collect whatever
            # finishes in time, mark the rest as unreachable.
            try:
                results = await asyncio.wait_for(
                    asyncio.gather(*coros, return_exceptions=True), timeout=9.0
                )
            except asyncio.TimeoutError:
                results = []
                for c in coros:
                    if c.done():
                        try:
                            results.append(_norm_result(c.result()))
                        except BaseException:
                            results.append(([], None, "none"))
                    else:
                        c.cancel()
                        results.append(([], None, "none"))
        else:
            results = await asyncio.gather(*coros, return_exceptions=True)
        results = [_norm_result(r) for r in results]
    rows: list[dict] = []
    errors = 0
    for t, (bars, meta, source) in zip(config.WATCHLIST, results):
        if not bars and source != "quote":
            errors += 1
            continue
        rows.append(metrics_for(t, meta, bars, source))
    return rows, errors


_CACHE_30S = {"Cache-Control": "public, s-maxage=30, stale-while-revalidate=120"}


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------
@app.get("/api/screener")
async def api_screener(
    min_change: str | None = None,
    min_relvol: str | None = None,
    min_price: str | None = None,
    max_price: str | None = None,
    sort: str | None = None,
    direction: str | None = None,
):
    params = screenlib.parse_screen_params(min_change, min_relvol, min_price, max_price, sort, direction)
    rows, fetch_errors = await screener_rows()
    filtered = screenlib.apply_filters(rows, params)
    state, mins = market_state()
    return JSONResponse(
        {
            "rows": filtered,
            "fetch_errors": fetch_errors,
            "market_state": state,
            "minutes_in_session": mins,
            "data_mode": config.DATA_MODE,
            "refreshed_at": time.time(),
        },
        headers=_CACHE_30S,
    )


@app.get("/api/ticker")
async def api_ticker(symbol: str = Query(..., min_length=1), bars_limit: int = Query(default=80, le=300)):
    t = symbol.upper().strip()
    async with httpx.AsyncClient() as client:
        bars, meta, source = await bars_for(t, client)
    if not bars and source != "quote":
        raise HTTPException(status_code=404, detail=f"no data for {t} — add it to WATCHLIST")
    state, mins = market_state()
    return JSONResponse(
        {
            "metrics": metrics_for(t, meta, bars, source),
            "bars": bars[-bars_limit:],
            "name": meta.get("name") if meta else t,
            "data_mode": config.DATA_MODE,
            "market_state": state,
            "minutes_in_session": mins,
            "quote_only": source == "quote",
        },
        headers=_CACHE_30S,
    )


class RiskRequest(BaseModel):
    ticker: str
    entry: float | None = None
    stop: float | None = None
    account: float = Field(default=25_000.0, gt=0)
    risk_pct: float = Field(default=1.0, gt=0, le=10)


@app.post("/api/risk")
async def api_risk(req: RiskRequest):
    t = req.ticker.upper().strip()
    async with httpx.AsyncClient() as client:
        bars, meta, source = await bars_for(t, client)
    if not bars and source != "quote":
        raise HTTPException(status_code=404, detail=f"no data for {t} — add it to WATCHLIST")
    m = metrics_for(t, meta, bars, source)
    state, mins = market_state(asset=m["asset"])
    result = risk.assess(
        risk.RiskInput(
            price=m["price"],
            entry=req.entry,
            stop=req.stop,
            account=req.account,
            risk_pct=req.risk_pct,
            atr_pct=m["atr_pct"],
            rel_vol=m["rel_vol"],
            from_open_pct=m["from_open_pct"],
            minutes_in_session=mins,
            session_state=state,
            asset=m["asset"],
        )
    )
    return {
        "ticker": t,
        "price": m["price"],
        "verdict": result.verdict,
        "reasons": result.reasons,
        "shares": result.shares,
        "dollar_risk": result.dollar_risk,
        "stop_dist_pct": result.stop_dist_pct,
        "capped": result.capped,
        "entry": result.entry,
        "stop": result.stop,
        "context": {
            "atr_pct": m["atr_pct"],
            "rel_vol": m["rel_vol"],
            "from_open_pct": m["from_open_pct"],
            "vwap_dist_pct": m["vwap_dist_pct"],
            "rsi": m["rsi"],
            "market_state": state,
            "minutes_in_session": mins,
            "asset": m["asset"],
        },
    }


@app.get("/api/status")
def api_status():
    state, mins = market_state()
    return JSONResponse(
        {
            "version": "v14",
            "mode": config.DATA_MODE,
            "watchlist": config.WATCHLIST,
            "market_state": state,
            "minutes_in_session": mins,
            "et_now": datetime.now(ET).isoformat(timespec="seconds"),
            "fetched_at": time.time(),
            "fetch_diagnostics": dict(list(LAST_FETCH_ERRORS.items())[:8]),
        },
        headers={"Cache-Control": "public, s-maxage=5"},
    )


# --------------------------------------------------------------------------
# Static frontend (public/). API routers above take precedence over this
# catch-all mount. check_dir=False so import can never crash if public/ is
# not in the function bundle (Vercel serves it from the CDN separately).
# --------------------------------------------------------------------------
app.mount("/", StaticFiles(directory=str(ROOT / "public"), html=True, check_dir=False), name="frontend")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
