# Intraday Radar

A fast, self-contained dashboard for spotting intraday trade ideas and checking
entry risk before you click buy. Screener → ticker detail → risk check, end to
end — across equities, crypto, and forex.

- **Live data:** 5-minute OHLCV bars pulled from Yahoo's public chart endpoint
  (no API key needed), polled in the background and stored in SQLite. Same
  endpoint serves stocks (`AAPL`), crypto (`BTC-USD`), and forex (`EURUSD=X`).
- **Asset-aware trading calendars:** equities use the US session (09:30–16:00
  ET), forex trades Sun 17:00 ET → Fri 17:00 ET, crypto is 24/7. The risk
  engine's timing rules adapt per symbol instead of assuming one session.
- **Demo mode:** deterministic synthetic market data for all three asset
  classes, so the app works fully offline — useful for development or UI work.
- **Views:** a filterable/sortable screener with asset badges, a per-ticker
  detail page with a candlestick chart (canvas, zero frontend dependencies),
  and an entry risk checker with position sizing (whole shares for equities,
  fractional units for crypto/fx).
- **Deploy:** single `docker compose up`, plain `uvicorn`, or Vercel (see
  below). No external services, no build step for the frontend.

## Quick start (local)

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
# open http://localhost:8000
```

Or with Docker:

```bash
docker compose up --build
```

Try it without a network first:

```bash
DATA_MODE=demo uvicorn app.main:app --port 8000
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
| `GET /`                  | Screener page                                       |
| `GET /ticker/{T}`        | Detail page for a symbol                            |
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
includes a full Vercel layout that adapts the same app accordingly — static
frontend in `public/`, serverless functions in `api/`, data fetched on demand
with CDN caching.

How it works on Vercel:

- `GET /api/screener` and `GET /api/ticker?symbol=X` respond with
  `Cache-Control: s-maxage=30` so Vercel's edge CDN absorbs repeat traffic and
  Yahoo only sees a request on cache misses and cold starts.
- A per-warm-instance memory cache (25s TTL) shares fetched bars across the
  functions in the same instance.
- No SQLite — bars are computed straight from the live fetch. Demo mode uses an
  ephemeral `/tmp` database.
- `vercel.json` gives each function a 10s max duration (fits the retry budget).

Deploy:

```bash
npm i -g vercel
vercel login
vercel            # from the repo root; confirm the detected settings
vercel --prod     # production deployment
```

Set these environment variables in the Vercel dashboard
(Project → Settings → Environment Variables):

| Variable                 | Value        | Meaning                                  |
| ------------------------ | ------------ | ---------------------------------------- |
| `DATA_MODE`              | `live`       | `live` (Yahoo) or `demo` (synthetic)     |
| `WATCHLIST`              | `AAPL,TSLA,NVDA,AMD,SPY` | Symbols to screen (keep it ≤ 10–15 to stay under Yahoo's rate limit) |
| `LIVE_FALLBACK_TO_DEMO`  | `1` or `0`   | Show clearly-labeled demo bars for a symbol if the live fetch fails |

Try the exact Vercel layout locally before deploying (same-origin static + API):

```bash
DATA_MODE=demo uvicorn dev:app --port 8000
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
