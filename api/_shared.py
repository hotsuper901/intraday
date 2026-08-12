"""Shared plumbing for the Vercel serverless API functions.

Vercel is serverless: no background poller, no persistent disk. Instead we
fetch on demand with a short per-instance memory cache, and the API responses
carry CDN cache headers (s-maxage) so repeat traffic is absorbed by Vercel's
edge and Yahoo only sees a request on cache misses / cold starts.
"""
import asyncio
import os
import sys
import time

# On Vercel the function's own directory is sys.path[0], not the repo root.
# Make both layouts work by ensuring the repo root is importable.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Must be set before app.config reads them.
os.environ.setdefault("DB_PATH", "/tmp/market.db")
os.environ.setdefault("DATA_MODE", "live")

import httpx  # noqa: E402

from app import config, db, indicators  # noqa: E402
from app.fetcher import asset_class, fetch_demo, fetch_ticker, market_state  # noqa: E402

_INSTANCE_CACHE: dict[str, tuple[float, list, dict | None, str]] = {}
_TTL_SECONDS = 25.0


async def bars_for(ticker: str, client: httpx.AsyncClient) -> tuple[list, dict | None, str]:
    """Bars, meta, and source ('live' | 'demo' | 'none') for one symbol,
    with a per-warm-instance TTL cache."""
    now = time.time()
    hit = _INSTANCE_CACHE.get(ticker)
    if hit and now - hit[0] < _TTL_SECONDS:
        return hit[1], hit[2], hit[3]

    source = "live"
    if config.DATA_MODE == "demo":
        source = "demo"
        db.init_db()  # ephemeral /tmp store; demo bars are deterministic anyway
        data = await asyncio.to_thread(fetch_demo, ticker)
    else:
        data = await fetch_ticker(client, ticker)
        if not data and config.LIVE_FALLBACK_TO_DEMO:
            source = "demo"
            db.init_db()
            data = await asyncio.to_thread(fetch_demo, ticker)

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
    m = indicators.compute_metrics(ticker, bars, meta.get("prev_close") if meta else None)
    m["name"] = (meta or {}).get("name") or ticker
    m["source"] = source
    m["asset"] = asset_class(ticker)
    return m


async def screener_rows() -> tuple[list[dict], int]:
    """All watchlist rows plus a count of symbols whose fetch failed."""
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(*(bars_for(t, client) for t in config.WATCHLIST))
    rows: list[dict] = []
    errors = 0
    for t, (bars, meta, source) in zip(config.WATCHLIST, results):
        if not bars:
            errors += 1
            continue
        rows.append(metrics_for(t, meta, bars, source))
    return rows, errors
