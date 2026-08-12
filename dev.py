"""Local dev server for the Vercel layout (same-origin static + /api).

Run from the repo root:  DATA_MODE=demo uvicorn dev:app --port 8000
This is exactly what Vercel serves: static files from public/ plus the
serverless functions in api/, all on one origin.
"""
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from api.risk import router as risk_router
from api.screener import router as screener_router
from api.status import router as status_router
from api.ticker import router as ticker_router

app = FastAPI(title="Intraday Radar (Vercel layout)")
for r in (screener_router, ticker_router, risk_router, status_router):
    app.include_router(r)
app.mount("/", StaticFiles(directory="public", html=True), name="static")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
