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
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from app import config, db, indicators, risk  # noqa: E402
from app import screener as screenlib  # noqa: E402
from app import signals  # noqa: E402
from app.fetcher import (  # noqa: E402
    ET,
    LAST_FETCH_ERRORS,
    DEMO_NAMES,
    asset_class,
    fetch_cnbc_quote,
    fetch_demo,
    fetch_ticker,
    fetch_yahoo_edge_batch,
    market_state,
)
from app.fetcher import _fetch_finnhub_quote as fetch_finnhub_quote  # noqa: E402

app = FastAPI(title="Intraday Radar")


# --------------------------------------------------------------------------
# On-demand fetch layer with a per-warm-instance TTL cache
# --------------------------------------------------------------------------
_INSTANCE_CACHE: dict[tuple[str, int], tuple[float, list, dict | None, str]] = {}
_TTL_SECONDS = 45.0
_FX_BATCH_CACHE: dict = {"ts": 0.0, "results": {}}
_FX_BATCH_1M_CACHE: dict = {"ts": 0.0, "results": {}}


async def _fx_batch(client: httpx.AsyncClient, interval: int = 5) -> dict:
    """One batched edge round-trip for the whole FX watchlist (cached ~20s).
    Serves both 5m and 1m Yahoo series through the edge proxy."""
    cache = _FX_BATCH_CACHE if interval >= 5 else _FX_BATCH_1M_CACHE
    now = time.time()
    if cache["results"] and now - cache["ts"] < 20:
        return cache["results"]
    tickers = [t for t in config.WATCHLIST if asset_class(t) == "fx"]
    results = await fetch_yahoo_edge_batch(client, tickers, interval)
    cache.update({"ts": now, "results": results})
    return results


async def bars_for(ticker: str, client: httpx.AsyncClient, interval: int = 5) -> tuple[list, dict | None, str]:
    """Bars, meta, and source ('live' | 'demo' | 'none') for one symbol."""
    key = (ticker, interval)
    now = time.time()
    hit = _INSTANCE_CACHE.get(key)
    if hit and now - hit[0] < _TTL_SECONDS:
        return hit[1], hit[2], hit[3]

    source = "live"
    if config.DATA_MODE == "demo":
        source = "demo"
        db.init_db()
        data = await asyncio.to_thread(fetch_demo, ticker)
    else:
        data = None
        if config.SERVERLESS and asset_class(ticker) == "fx":
            # One batched edge round-trip serves the whole FX watchlist at
            # the requested interval (5m screener bars or 1m signal bars).
            batch = await _fx_batch(client, interval)
            data = batch.get(ticker)
        if not data:
            data = await fetch_ticker(client, ticker, interval)
        if not data and config.LIVE_FALLBACK_TO_DEMO:
            source = "demo"
            db.init_db()
            data = await asyncio.to_thread(fetch_demo, ticker)
        if not data and config.SERVERLESS and asset_class(ticker) == "fx" and interval == 5:
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
                _INSTANCE_CACHE[key] = (now, [], meta, "quote")
                return [], meta, "quote"

    if not data:
        _INSTANCE_CACHE[key] = (now, [], None, "none")
        return [], None, "none"
    bars = data["bars"]
    meta = {
        "name": data["name"],
        "prev_close": data["prev_close"],
        "session_open": data["session_open"],
    }
    _INSTANCE_CACHE[key] = (now, bars, meta, source)
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
    async with httpx.AsyncClient(follow_redirects=True) as client:
        tasks = [asyncio.create_task(bars_for(t, client)) for t in config.WATCHLIST]
        if config.SERVERLESS:
            # Vercel functions have a hard time budget: collect whatever
            # finishes in time, mark the rest as unreachable.
            try:
                results = await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True), timeout=7.5
                )
            except asyncio.TimeoutError:
                results = []
                for task in tasks:
                    if task.done():
                        try:
                            results.append(_norm_result(task.result()))
                        except BaseException:
                            results.append(([], None, "none"))
                    else:
                        task.cancel()
                        results.append(([], None, "none"))
        else:
            results = await asyncio.gather(*tasks, return_exceptions=True)
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
    try:
        rows, fetch_errors = await screener_rows()
    except Exception as e:  # noqa: BLE001 — surface unexpected failures for diagnosis
        import traceback
        return JSONResponse(
            {"error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-1500:],
             "rows": [], "fetch_errors": len(config.WATCHLIST),
             "market_state": None, "data_mode": config.DATA_MODE, "refreshed_at": time.time()},
            headers={"Cache-Control": "no-store"},
        )
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
async def api_ticker(
    symbol: str = Query(..., min_length=1),
    bars_limit: int = Query(default=80, le=300),
    interval: int = Query(default=5, ge=1, le=15),
):
    t = symbol.upper().strip()
    iv = interval if interval in (1, 5) else 5
    async with httpx.AsyncClient(follow_redirects=True) as client:
        bars, meta, source = await bars_for(t, client, interval=iv)
    if config.DATA_MODE == "demo":
        iv = 5  # demo generator produces 5m bars only
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
            "interval": iv,
        },
        headers=_CACHE_30S,
    )


@app.get("/api/symbols")
def api_symbols():
    """Watchlist (ticker, name, asset) for the symbol search and pager."""
    symbols = [
        {
            "ticker": t,
            "name": DEMO_NAMES.get(t, t),
            "asset": asset_class(t),
        }
        for t in config.WATCHLIST
    ]
    return JSONResponse(
        {"symbols": symbols, "data_mode": config.DATA_MODE},
        headers=_CACHE_30S,
    )


class SignalRequest(BaseModel):
    ticker: str


@app.post("/api/signal")
async def api_signal(req: SignalRequest):
    """Multi-timeframe buy/sell signal: 1m + 5m indicator confluence with a
    price prediction (entry, target, stop, R:R)."""
    t = req.ticker.upper().strip()
    degraded_note: str | None = None
    async with httpx.AsyncClient(follow_redirects=True) as client:
        if config.DATA_MODE == "demo":
            # Demo generator produces 5m bars only — analyze those on both slots.
            bars_5m, meta, source = await bars_for(t, client, interval=5)
            bars_1m = bars_5m
            degraded = True
        elif config.SERVERLESS and asset_class(t) == "fx":
            # Try the real 1m series first (edge-batched); Jina's free tier
            # truncates huge 1m payloads, so fall back to 5m on both slots.
            bars_1m, meta, source = await bars_for(t, client, interval=1)
            if len(bars_1m) >= 26:
                bars_5m = signals.resample(bars_1m, 5)
                degraded = len(bars_5m) < 26
                if degraded:
                    bars_5m, meta, source = await bars_for(t, client, interval=5)
                    bars_1m = bars_5m
            else:
                bars_5m, meta, source = await bars_for(t, client, interval=5)
                bars_1m = bars_5m
                degraded = True
        else:
            bars_1m, meta, source = await bars_for(t, client, interval=1)
            degraded = False
            if len(bars_1m) >= 26:
                bars_5m = signals.resample(bars_1m, 5)
                if len(bars_5m) == len(bars_1m) and signals.median_gap(bars_1m) >= 240:
                    # The "1m" feed was actually coarse (e.g. a fallback source
                    # serving 5m bars) — both timeframes are the same series.
                    degraded = True
                elif len(bars_5m) < 26:
                    # Too few 1m bars to build a usable 5m series: fetch the
                    # real 5m series so the 5m timeframe is fully analyzed.
                    real5, meta5, src5 = await bars_for(t, client, interval=5)
                    if real5 and len(real5) >= 26:
                        bars_5m = real5
                        meta, source = meta5, src5
                        degraded = True
                    else:
                        bars_5m = signals.resample(bars_1m, 5)
            else:
                # Fall back: analyze the 5m series on both timeframes, flagged.
                bars_5m, meta, source = await bars_for(t, client, interval=5)
                bars_1m = bars_5m
                degraded = True
    if len(bars_1m) < 26:
        # Last resorts: one direct retry (bypasses the instance cache, fresh
        # client — the outer one is already closed here), then labeled demo
        # bars so the signal always renders something.
        if config.DATA_MODE != "demo":
            async with httpx.AsyncClient(follow_redirects=True) as retry_client:
                direct = await fetch_ticker(retry_client, t, interval=5)
            if direct and len(direct["bars"]) >= 26:
                bars_1m = bars_5m = direct["bars"]
                meta = {
                    "name": direct["name"],
                    "prev_close": direct["prev_close"],
                    "session_open": direct["session_open"],
                }
                source = "live"
                degraded = True
                degraded_note = "live 1m/5m unavailable — direct 5m fetch used"
        if len(bars_1m) < 26:
            db.init_db()
            demo_data = await asyncio.to_thread(fetch_demo, t)
            if demo_data and len(demo_data["bars"]) >= 26:
                bars_1m = bars_5m = demo_data["bars"]
                degraded = True
                source = "demo"
                degraded_note = "live data unavailable — labeled demo bars used"
        if len(bars_1m) < 26:
            raise HTTPException(status_code=404, detail=f"not enough data for {t}")
    result = signals.assess(bars_1m, bars_5m)
    result["ticker"] = t
    result["name"] = (meta or {}).get("name") or t
    result["source"] = source
    result["asset"] = asset_class(t)
    result["bars_1m"] = len(bars_1m)
    result["bars_5m"] = len(bars_5m)
    result["degraded"] = degraded
    if degraded:
        note = degraded_note or "1-minute data unavailable — 5m used for both timeframes"
        result["reasons"].append(note)
    return result


class RiskRequest(BaseModel):
    ticker: str
    entry: float | None = None
    stop: float | None = None
    account: float = Field(default=25_000.0, gt=0)
    risk_pct: float = Field(default=1.0, gt=0, le=10)


@app.post("/api/risk")
async def api_risk(req: RiskRequest):
    t = req.ticker.upper().strip()
    async with httpx.AsyncClient(follow_redirects=True) as client:
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
            "version": "v17",
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
# Static frontend (public/). Clean ticker route first; API routers above take
# precedence over the catch-all mount.
# --------------------------------------------------------------------------
@app.get("/ticker/{ticker}", include_in_schema=False)
def ticker_page(ticker: str):
    return FileResponse(str(ROOT / "public" / "ticker.html"))


@app.get("/guide", include_in_schema=False)
def guide_page():
    """Trading-signal manual: how to apply the signal on Binomo, Pocket
    Option, IQ Option and other brokers."""
    return FileResponse(str(ROOT / "public" / "guide.html"))


app.mount("/", StaticFiles(directory=str(ROOT / "public"), html=True, check_dir=False), name="frontend")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
