import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
from fastapi import APIRouter, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from api._shared import bars_for, market_state, metrics_for
from app import config

router = APIRouter()


@router.get("/api/ticker")
async def ticker(symbol: str = Query(..., min_length=1), bars_limit: int = Query(default=80, le=300)):
    t = symbol.upper().strip()
    async with httpx.AsyncClient() as client:
        bars, meta, source = await bars_for(t, client)
    if not bars:
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
        },
        headers={"Cache-Control": "public, s-maxage=30, stale-while-revalidate=120"},
    )


app = FastAPI()
app.include_router(router)
