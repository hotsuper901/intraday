import httpx
from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel, Field

from api._shared import bars_for, market_state, metrics_for
from app import risk
from app.fetcher import asset_class

router = APIRouter()


class RiskRequest(BaseModel):
    ticker: str
    entry: float | None = None
    stop: float | None = None
    account: float = Field(default=25_000.0, gt=0)
    risk_pct: float = Field(default=1.0, gt=0, le=10)


@router.post("/api/risk")
async def check_risk(req: RiskRequest):
    t = req.ticker.upper().strip()
    async with httpx.AsyncClient() as client:
        bars, meta, source = await bars_for(t, client)
    if not bars:
        raise HTTPException(status_code=404, detail=f"no data for {t} — add it to WATCHLIST")
    m = metrics_for(t, meta, bars, source)
    state, mins = market_state(asset=asset_class(t))
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
        asset=asset_class(t),
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


app = FastAPI()
app.include_router(router)
