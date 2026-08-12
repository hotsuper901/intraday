"""Intraday Radar — FastAPI app. Screener + ticker detail + risk check."""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from . import config, db, fetcher, indicators, risk, screener
from .fetcher import ET

log = logging.getLogger("main")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

# Absolute paths so the module can be imported from any working directory.
_APP_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(_APP_DIR / "templates"))


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
    _last_refresh["summary"] = await fetcher.refresh_all()
    _last_refresh["ts"] = time.time()
    task = asyncio.create_task(_worker())
    yield
    task.cancel()


app = FastAPI(title="Intraday Radar", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(_APP_DIR / "static")), name="static")


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
# Pages
# --------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    state, mins = fetcher.market_state()
    return templates.TemplateResponse(
        request, "screener.html",
        {
            "watchlist": config.WATCHLIST,
            "data_mode": config.DATA_MODE,
            "market_state": state,
            "refresh_seconds": config.REFRESH_SECONDS,
            "bar_count": db.bar_count(),
        },
    )


@app.get("/ticker/{ticker}", response_class=HTMLResponse)
def ticker_page(request: Request, ticker: str):
    t = ticker.upper().strip()
    state, mins = fetcher.market_state()
    return templates.TemplateResponse(
        request, "ticker.html",
        {"ticker": t, "data_mode": config.DATA_MODE, "market_state": state},
    )


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
def api_ticker(ticker: str, bars_limit: int = Query(default=80, le=300)):
    t = ticker.upper().strip()
    meta = db.all_meta().get(t)
    bars = db.bars_for(t, limit=bars_limit)
    if not bars:
        raise HTTPException(status_code=404, detail=f"no data for {t} — add it to WATCHLIST")
    m = _metrics_for(t, meta, bars)
    return {"metrics": m, "bars": bars, "name": meta.get("name") if meta else t}


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
