import time

from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse

from api._shared import market_state, screener_rows
from app import config, screener as screenlib

router = APIRouter()


@router.get("/api/screener")
async def screener(
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
        headers={"Cache-Control": "public, s-maxage=30, stale-while-revalidate=120"},
    )


app = FastAPI()
app.include_router(router)
