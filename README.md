# Intraday Radar

A fast, self-contained dashboard for spotting intraday trade ideas and checking
entry risk before you click buy. Screener → ticker detail → risk check, end to
end — across equities, crypto, and forex.

- **Live data:** 5-minute OHLCV bars pulled from free public endpoints (no
  API key needed), polled in the background and stored in SQLite. Equities and
  forex use Yahoo's chart endpoint; crypto falls back to Kraken and Coinbase
  Exchange, which serve datacenter IPs (Vercel functions) that Yahoo, Binance,
  and Bybit block or rate-limit.
- **Asset-aware trading calendars:** equities use the US session (09:30–16:00
  ET), forex trades Sun 17:00 ET → Fri 17:00 ET, crypto is 24/7. The risk
  engine's timing rules adapt per symbol instead of assuming one session.
- **Demo mode:** deterministic synthetic market data for all three asset
  classes, so the app works fully offline — useful for development or UI work.
- **Views:** a cinematic landing page, a filterable/sortable screener with
  asset badges, a per-ticker detail page with a candlestick chart (canvas,
  zero frontend dependencies), and an entry risk checker with position sizing
  (whole shares for equities, fractional units for crypto/fx).
- **Design system:** a Linear/Modern dark theme — near-black `#050506` canvas,
  indigo `#5E6AD2` accent, layered ambient lighting (noise + 64px grid +
  static light pools), multi-layer shadows and expo-out micro-interactions.
  Pure hand-rolled CSS in `public/static/style.css`; no CSS framework, no
  build step.
- **Deploy:** single `docker compose up`, plain `uvicorn`, or Vercel (see
  below). No external services, no build step for the frontend.

## Quick start (local)

```bash
pip install -r requirements.txt
uvicorn app.standalone:app --host 0.0.0.0 --port 8000
# open http://localhost:8000
```

Or with Docker:

```bash
docker compose up --build
```

Try it without a network first:

```bash
DATA_MODE=demo uvicorn app.standalone:app --port 8000
```

## Configuration (environment variables)

| Variable            | Default                                 | Meaning                                   |
| ------------------- | --------------------------------------- | ----------------------------------------- |
| `WATCHLIST`         | 34 symbols: 10 US equities, 12 crypto majors (`BTC-USD`, `ETH-USD`, …), 12 forex majors (`EURUSD=X`, …) | Symbols polled and screened (comma separated) |
| `REFRESH_SECONDS`   | `120`                                   | Background poll interval (34 symbols ≈ 1k requests/hr — keep ≥ 60s to stay under Yahoo's rate limit) |
| `DATA_MODE`         | `live`                                  | `live` (Yahoo) or `demo` (synthetic)      |
| `DB_PATH`           | `market.db`                             | SQLite file location                      |
| `MAX_POSITION_PCT`  | `25`                                    | Cap on position size, % of account        |

## Endpoints

| Route                    | What it does                                        |
| ------------------------ | --------------------------------------------------- |
| `GET /`                  | Landing page                                        |
| `GET /screener`          | Screener page                                       |
| `GET /ticker/{T}`        | Detail page for a symbol                            |
| `GET /guide`             | Trading-signal manual (broker playbook)             |
| `GET /api/screener`      | JSON rows; query params `min_change`, `min_relvol`, `min_price`, `max_price`, `sort` |
| `GET /api/ticker/{T}`    | JSON metrics + recent bars                          |
| `POST /api/risk`         | `{ticker, entry?, stop?, account?, risk_pct?}` → verdict, reasons, share size |
| `POST /api/refresh`      | Wake the background fetcher immediately             |
| `GET /api/status`        | Mode, watchlist, last refresh summary               |

## Risk check rules

The risk engine (pure, unit-tested) gives each entry a verdict:

- **GO** — no violations.
- **CAUTION** — ATR% > 3, relative volume < 0.6×, price > 5% from the open,
  or the requested position gets capped by `MAX_POSITION_PCT`.
- **AVOID** — the asset's market is closed (equity weekend/after-hours, fx
  weekend), entries in the first 5 / last 10 minutes of the *equity* session
  (not applied to crypto/fx), ATR% > 5 (stop can't breathe), relative volume
  < 0.3× (nobody's trading), price > 8% from open (chasing), or the risk math
  yields zero shares.
- **INVALID** — stop not below entry, bad risk %, etc.

Position sizing: `units = account × risk% / (entry − stop)`, capped so
`units × entry ≤ MAX_POSITION_PCT% × account`. Equities size in whole shares;
crypto and forex size in fractional units (e.g. 0.1718 BTC) so high-priced
assets don't round to zero.

## Deploying to Vercel

Vercel is serverless: no long-running poller and no persistent disk. This repo
is structured for Vercel's Python runtime, which auto-detects FastAPI and
loads a single entrypoint app that routes every request.

How it works on Vercel:

- Root `main.py` is the entrypoint (pinned via `tool.vercel.entrypoint` in
  `pyproject.toml`). It serves the static frontend from `public/` and the API
  (`/api/screener`, `/api/ticker`, `/api/risk`, `/api/status`) in one app.
- `GET /api/screener` and `GET /api/ticker` respond with
  `Cache-Control: s-maxage=30` so Vercel's edge CDN absorbs repeat traffic and
  Yahoo only sees a request on cache misses and cold starts.
- A per-warm-instance memory cache (25s TTL) shares fetched bars across
  requests in the same instance. No SQLite in live mode; demo mode uses an
  ephemeral `/tmp` database.
- The original background-poller app (`app/standalone.py`, `docker compose up`) is
  untouched and remains the choice for a persistent server.

Deploy:

```bash
npm i -g vercel
vercel login
vercel            # from the repo root; accept the defaults (FastAPI preset)
vercel --prod
```

Or import the GitHub repo in the Vercel dashboard (Add New → Project →
Import Git Repository) — every push to `main` then redeploys automatically.

Set these environment variables in the Vercel dashboard
(Project → Settings → Environment Variables):

| Variable                 | Value        | Meaning                                  |
| ------------------------ | ------------ | ---------------------------------------- |
| `DATA_MODE`              | `live`       | `live` (Yahoo) or `demo` (synthetic)     |
| `WATCHLIST`              | `AAPL,TSLA,NVDA,BTC-USD,ETH-USD,SOL-USD,EURUSD=X,USDJPY=X,SPY,QQQ` | Symbols to screen (keep it ≤ 10–15 to stay under Yahoo's rate limit) |
| `LIVE_FALLBACK_TO_DEMO`  | `1` or `0`   | Show clearly-labeled demo bars for a symbol if the live fetch fails |

Try the exact Vercel layout locally before deploying:

```bash
DATA_MODE=demo uvicorn main:app --port 8000
# or: python dev.py
```

Notes:

- Vercel's free Hobby plan is plenty for personal use. Cold starts add ~1–2s to
  the first request after idle; cached responses stay fast.
- Yahoo rate-limits aggressively from datacenter IPs. The fetcher retries with
  backoff and rotates between `query1`/`query2` hosts; the CDN cache plus a
  short watchlist keeps things calm. If the feed is down entirely, the screener
  shows an explicit error instead of pretending the market is empty.
- If you'd rather keep the original background-poller architecture (60s refresh
  loop + SQLite), deploy the Docker image to Railway, Render, or Fly.io instead —
  the `Dockerfile` in this repo works on all three with zero changes. Vercel is
  the pick when you want the static frontend on the edge for free.

## Tests

```bash
pytest
```

Covers the full risk rule set (asset-aware timing, volatility, participation,
extension, integer vs fractional sizing, invalid inputs), screener
filtering/sorting, asset classification, and the three trading calendars
(equity / fx / crypto).

## Notes

- Bar timestamps are epoch seconds in `America/New_York`; the app renders all
  clock labels in ET.
- Symbol conventions are Yahoo's: stocks `AAPL`, crypto `BTC-USD` (24/7),
  forex `EURUSD=X`. Asset class is derived from the symbol itself.
- The "⟳ Refresh" button wakes the poller immediately; the UI itself refreshes
  the screener every 20s and the detail page every 30s.
- With the full 34-symbol watchlist, live polling averages ~1,000 requests/hr
  against Yahoo. Keep `REFRESH_SECONDS ≥ 60`; on Vercel trim the watchlist to
  ~10–15 symbols since cold starts bypass the CDN cache.
