"""Intraday Radar — FastAPI app. Screener + ticker detail + risk check."""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.responses import Response

from . import config, db, fetcher, indicators, risk, screener, signals
from .fetcher import ET

log = logging.getLogger("main")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

# Absolute paths so the module can be imported from any working directory.
# Pages and assets live in the repo-level public/ dir — the same files the
# serverless entrypoint (main.py) serves, so the UI has one source of truth.
_APP_DIR = Path(__file__).resolve().parent
_PUBLIC_DIR = _APP_DIR.parent / "public"


# --------------------------------------------------------------------------
# App lifecycle: init DB, seed data, run background refresher
# --------------------------------------------------------------------------
_refresh_event = asyncio.Event()
_last_refresh: dict = {"ts": None, "summary": {}}


async def _worker():
    while True:
        try:
            _last_refresh["summary"] = await fetcher.refresh_all()
            _last_refresh["ts"] = time.time()
        except Exception as e:
            log.error("refresh failed: %s", e)
        try:
            await asyncio.wait_for(_refresh_event.wait(), timeout=config.REFRESH_SECONDS)
        except asyncio.TimeoutError:
            pass
        _refresh_event.clear()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    log.info("mode=%s watchlist=%s", config.DATA_MODE, config.WATCHLIST)
    if config.SERVERLESS:
        # Running on a serverless platform (Vercel sets VERCEL=1): no
        # background poller, no startup fetch. The root main.py entrypoint is
        # the serverless app; this guard only prevents accidental resource use.
        yield
        return
    _last_refresh["summary"] = await fetcher.refresh_all()
    _last_refresh["ts"] = time.time()
    task = asyncio.create_task(_worker())
    yield
    task.cancel()


class CachedStaticFiles(StaticFiles):
    """Static assets with long-lived cache headers (versioned ?v=N URLs)."""

    async def get_response(self, path: str, scope) -> Response:
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return resp


app = FastAPI(title="Intraday Radar", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=500)
app.mount("/static", CachedStaticFiles(directory=str(_PUBLIC_DIR / "static"), check_dir=False), name="static")


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _metrics_for(ticker: str, meta: dict | None, bars: list[dict]) -> dict:
    prev_close = meta.get("prev_close") if meta else None
    m = indicators.compute_metrics(ticker, bars, prev_close)
    m["name"] = (meta or {}).get("name") or ticker
    m["updated_at"] = (meta or {}).get("updated_at")
    m["asset"] = fetcher.asset_class(ticker)
    return m


# --------------------------------------------------------------------------
# Pages — same public/ files as the serverless entrypoint
# --------------------------------------------------------------------------
@app.get("/")
def index():
    return FileResponse(str(_PUBLIC_DIR / "index.html"))


@app.get("/screener")
def screener_page():
    return FileResponse(str(_PUBLIC_DIR / "screener.html"))


@app.get("/ticker/{ticker}")
def ticker_page(ticker: str):
    return FileResponse(str(_PUBLIC_DIR / "ticker.html"))


@app.get("/guide")
def guide_page():
    """Trading-signal manual: Binomo, Pocket Option, IQ Option and more."""
    return FileResponse(str(_PUBLIC_DIR / "guide.html"))


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------
@app.get("/api/screener")
def api_screener(
    min_change: str | None = None,
    min_relvol: str | None = None,
    min_price: str | None = None,
    max_price: str | None = None,
    sort: str | None = None,
    direction: str | None = None,
):
    params = screener.parse_screen_params(min_change, min_relvol, min_price, max_price, sort, direction)
    meta_map = db.all_meta()
    rows = []
    for ticker in config.WATCHLIST:
        if ticker not in meta_map:
            continue
        bars = db.bars_for(ticker, limit=120)
        rows.append(_metrics_for(ticker, meta_map[ticker], bars))
    filtered = screener.apply_filters(rows, params)
    state, mins = fetcher.market_state()
    return {
        "rows": filtered,
        "market_state": state,
        "minutes_in_session": mins,
        "refreshed_at": _last_refresh["ts"],
        "data_mode": config.DATA_MODE,
    }


@app.get("/api/ticker/{ticker}")
def api_ticker(ticker: str, bars_limit: int = Query(default=80, le=300), interval: int = Query(default=5, ge=1, le=15)):
    t = ticker.upper().strip()
    meta = db.all_meta().get(t)
    bars = db.bars_for(t, limit=bars_limit)
    if not bars:
        raise HTTPException(status_code=404, detail=f"no data for {t} — add it to WATCHLIST")
    m = _metrics_for(t, meta, bars)
    return {"metrics": m, "bars": bars, "name": meta.get("name") if meta else t, "interval": 5}


@app.get("/api/symbols")
def api_symbols():
    """Watchlist (ticker, name, asset) for the symbol search and pager."""
    symbols = [
        {"ticker": t, "name": fetcher.DEMO_NAMES.get(t, t), "asset": fetcher.asset_class(t)}
        for t in config.WATCHLIST
    ]
    return {"symbols": symbols, "data_mode": config.DATA_MODE}


class SignalRequest(BaseModel):
    ticker: str


@app.post("/api/signal")
async def api_signal(req: SignalRequest):
    """Multi-timeframe buy/sell signal: 1m + 5m confluence with prediction."""
    t = req.ticker.upper().strip()
    degraded_note: str | None = None
    async with httpx.AsyncClient() as client:
        data = None
        degraded = True
        if config.DATA_MODE == "demo":
            db.init_db()
            data = await asyncio.to_thread(fetcher.fetch_demo, t)
            bars_1m = data["bars"] if data else []
            bars_5m = bars_1m
        else:
            data = await fetcher.fetch_ticker(client, t, interval=1)
            if data:
                bars_1m = data["bars"]
                bars_5m = signals.resample(bars_1m, 5)
                degraded = len(bars_5m) == len(bars_1m) and signals.median_gap(bars_1m) >= 240
                if len(bars_5m) < 26:
                    # Too few 1m bars to build a 5m series — use the real one.
                    real5 = await fetcher.fetch_ticker(client, t, interval=5)
                    if real5 and len(real5["bars"]) >= 26:
                        bars_5m = real5["bars"]
                        data = real5
                        degraded = True
            if not data or len(data.get("bars") or []) < 26:
                data = await fetcher.fetch_ticker(client, t, interval=5)
                if data:
                    bars_1m = bars_5m = data["bars"]
                else:
                    bars_1m = bars_5m = db.bars_for(t, limit=300)
    if len(bars_1m) < 26:
        # Demo bars as a labeled last resort so the signal always renders.
        db.init_db()
        demo_data = await asyncio.to_thread(fetcher.fetch_demo, t)
        if demo_data and len(demo_data["bars"]) >= 26:
            bars_1m = bars_5m = demo_data["bars"]
            data = demo_data
            degraded = True
            degraded_note = "live data unavailable — labeled demo bars used"
    if len(bars_1m) < 26:
        raise HTTPException(status_code=404, detail=f"not enough data for {t}")
    result = signals.assess(bars_1m, bars_5m)
    result["ticker"] = t
    result["name"] = (data or {}).get("name") or t
    result["source"] = "demo" if degraded_note else config.DATA_MODE
    result["asset"] = fetcher.asset_class(t)
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
def api_risk(req: RiskRequest):
    t = req.ticker.upper().strip()
    meta = db.all_meta().get(t)
    bars = db.bars_for(t, limit=120)
    if not bars:
        raise HTTPException(status_code=404, detail=f"no data for {t}")
    m = _metrics_for(t, meta, bars)
    state, mins = fetcher.market_state(asset=m["asset"])
    ri = risk.RiskInput(
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
    result = risk.assess(ri)
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


@app.post("/api/refresh")
async def api_refresh():
    _refresh_event.set()  # wakes the worker immediately
    return {"status": "queued", "mode": config.DATA_MODE, "watchlist": config.WATCHLIST}


@app.get("/api/status")
def api_status():
    state, mins = fetcher.market_state()
    return {
        "mode": config.DATA_MODE,
        "watchlist": config.WATCHLIST,
        "bar_count": db.bar_count(),
        "last_refresh": _last_refresh["ts"],
        "last_summary": _last_refresh["summary"],
        "market_state": state,
        "minutes_in_session": mins,
        "et_now": datetime.now(ET).isoformat(timespec="seconds"),
    }
