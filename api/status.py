import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse

from api._shared import market_state
from app import config
from app.fetcher import ET

router = APIRouter()


@router.get("/api/status")
def status():
    state, mins = market_state()
    return JSONResponse(
        {
            "mode": config.DATA_MODE,
            "watchlist": config.WATCHLIST,
            "market_state": state,
            "minutes_in_session": mins,
            "et_now": datetime.now(ET).isoformat(timespec="seconds"),
            "fetched_at": time.time(),
        },
        headers={"Cache-Control": "public, s-maxage=15"},
    )


app = FastAPI()
app.include_router(router)
