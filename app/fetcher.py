"""Data acquisition: live 5m bars from Yahoo's public chart endpoint, plus a
deterministic demo generator so the app is fully usable with no network."""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
import urllib.parse
import warnings
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

from . import config, db

log = logging.getLogger("fetcher")

try:
    ET = ZoneInfo("America/New_York")
except Exception:
    warnings.warn("tzdata not installed — falling back to fixed UTC-4 (install tzdata for DST-correct clocks)")
    ET = timezone(timedelta(hours=-4))

YAHOO_HOSTS = [
    "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
    "https://query2.finance.yahoo.com/v8/finance/chart/{ticker}",
]
COOKIE_URL = "https://fc.yahoo.com"
CNBC_QUOTE_URL = "https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol"
# US-friendly crypto sources (no key) — Yahoo, Binance, and Bybit all refuse
# datacenter IPs like Vercel's; Kraken and Coinbase Exchange do not.
KRAKEN_OHLC_URL = "https://api.kraken.com/0/public/OHLC"
COINBASE_CANDLES_URL = "https://api.exchange.coinbase.com/products/{pair}/candles"
KUCOIN_CANDLES_URL = "https://api.kucoin.com/api/v1/market/candles"
NASDAQ_CHART_URL = "https://api.nasdaq.com/api/quote/{sym}/chart"
JINA_BASE = "https://r.jina.ai/"
FINNHUB_CANDLE_URL = "https://finnhub.io/api/v1/{endpoint}/candle"
FINNHUB_QUOTE_URL = "https://finnhub.io/api/v1/quote"
_FINNHUB_CANDLES_DISABLED = False
FINNHUB_FX_PAIRS = {
    "EURUSD=X": "OANDA:EUR_USD", "GBPUSD=X": "OANDA:GBP_USD",
    "USDJPY=X": "OANDA:USD_JPY", "AUDUSD=X": "OANDA:AUD_USD",
    "USDCAD=X": "OANDA:USD_CAD", "USDCHF=X": "OANDA:USD_CHF",
    "NZDUSD=X": "OANDA:NZD_USD", "EURGBP=X": "OANDA:EUR_GBP",
    "EURJPY=X": "OANDA:EUR_JPY", "GBPJPY=X": "OANDA:GBP_JPY",
    "USDMXN=X": "OANDA:USD_MXN", "USDCNY=X": "OANDA:USD_CNY",
}
CRYPTO_WINDOW = 288  # 24h of 5m bars

KRAKEN_PAIRS = {
    "BTC-USD": "XBTUSD", "ETH-USD": "ETHUSD", "SOL-USD": "SOLUSD",
    "XRP-USD": "XRPUSD", "DOGE-USD": "XDGUSD", "ADA-USD": "ADAUSD",
    "LINK-USD": "LINKUSD", "LTC-USD": "LTCUSD", "AVAX-USD": "AVAXUSD",
    "DOT-USD": "DOTUSD", "XLM-USD": "XLMUSD",
}

# Yahoo rate-limits hard when we fire dozens of parallel requests from a
# datacenter IP. Cap concurrency across the whole process.
_FETCH_SEM = asyncio.Semaphore(4)

# Last failure per ticker, surfaced via /api/status for diagnostics.
LAST_FETCH_ERRORS: dict[str, str] = {}


async def _bootstrap_cookies(client: httpx.AsyncClient) -> None:
    """Yahoo occasionally demands a cookie before serving chart data. One
    request to fc.yahoo.com usually satisfies it."""
    try:
        await client.get(COOKIE_URL, headers={"User-Agent": config.USER_AGENT}, timeout=config.HTTP_TIMEOUT)
    except httpx.HTTPError:
        pass


def _trim_24h(bars: list[dict]) -> list[dict] | None:
    bars = bars[-CRYPTO_WINDOW:]
    return bars or None


async def _fetch_kraken(client: httpx.AsyncClient, ticker: str, interval: int = 5) -> dict | None:
    """Crypto OHLCV from Kraken's public API (no key, US-friendly)."""
    pair = KRAKEN_PAIRS.get(ticker.upper())
    if not pair:
        return None
    try:
        resp = await client.get(
            KRAKEN_OHLC_URL, params={"pair": pair, "interval": interval}, timeout=config.HTTP_TIMEOUT
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("error"):
            LAST_FETCH_ERRORS[ticker] = f"kraken:{data['error']}"
            return None
        # Kraken returns its canonical pair name (e.g. XXBTZUSD) regardless of
        # the alias we request — take the first real OHLC entry.
        rows = None
        for k, v in data.get("result", {}).items():
            if k != "last":
                rows = v
                break
        if not rows:
            LAST_FETCH_ERRORS[ticker] = f"kraken:empty result ({data.get('error') or 'rate-limited?'})"
            return None
        bars = [
            {
                "ts": int(float(r[0])),
                "open": float(r[1]),
                "high": float(r[2]),
                "low": float(r[3]),
                "close": float(r[4]),
                "volume": float(r[6]),
            }
            for r in rows
        ]
        bars = bars[-CRYPTO_WINDOW if interval >= 5 else 1440:]
        if not bars:
            return None
        return {
            "bars": bars,
            "name": DEMO_NAMES.get(ticker, pair),
            "prev_close": bars[0]["open"],
            "session_open": bars[0]["ts"],
        }
    except (httpx.HTTPError, ValueError, IndexError, TypeError, KeyError) as e:
        LAST_FETCH_ERRORS[ticker] = f"kraken:{type(e).__name__}: {e}"
        return None


async def _fetch_coinbase(client: httpx.AsyncClient, ticker: str, interval: int = 5) -> dict | None:
    """Crypto OHLCV from Coinbase Exchange's public API (no key, US-based)."""
    pair = ticker.upper()
    try:
        resp = await client.get(
            COINBASE_CANDLES_URL.format(pair=pair),
            params={"granularity": interval * 60},
            headers={"User-Agent": config.USER_AGENT},
            timeout=config.HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        rows = resp.json()
        # Coinbase rows: [time, low, high, open, close, volume], newest first.
        bars = [
            {
                "ts": int(r[0]),
                "open": float(r[3]),
                "high": float(r[2]),
                "low": float(r[1]),
                "close": float(r[4]),
                "volume": float(r[5]),
            }
            for r in reversed(rows)
        ]
        bars = bars[-CRYPTO_WINDOW if interval >= 5 else 1440:]
        if not bars:
            return None
        return {
            "bars": bars,
            "name": DEMO_NAMES.get(ticker, pair),
            "prev_close": bars[0]["open"],
            "session_open": bars[0]["ts"],
        }
    except (httpx.HTTPError, ValueError, IndexError, TypeError, KeyError) as e:
        LAST_FETCH_ERRORS[ticker] = f"coinbase:{type(e).__name__}: {e}"
        return None


async def _fetch_kucoin(client: httpx.AsyncClient, ticker: str, interval: int = 5) -> dict | None:
    """Crypto OHLCV from KuCoin's public API (no key). Third fallback — covers
    symbols missing from Kraken/Coinbase like BNB."""
    sym = ticker.upper().split("-")[0] + "-USDT"
    try:
        now_s = int(time.time())
        resp = await client.get(
            KUCOIN_CANDLES_URL,
            params={
                "type": f"{interval}min",
                "symbol": sym,
                "startAt": now_s - 86400,
                "endAt": now_s,
            },
            headers={"User-Agent": config.USER_AGENT},
            timeout=config.HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        rows = resp.json().get("data") or []
        # KuCoin rows: [time, open, close, high, low, volume, turnover], newest first.
        bars = [
            {
                "ts": int(r[0]),
                "open": float(r[1]),
                "close": float(r[2]),
                "high": float(r[3]),
                "low": float(r[4]),
                "volume": float(r[5]),
            }
            for r in reversed(rows)
        ]
        bars = _trim_24h(bars)
        if not bars:
            return None
        return {
            "bars": bars,
            "name": DEMO_NAMES.get(ticker, sym),
            "prev_close": bars[0]["open"],
            "session_open": bars[0]["ts"],
        }
    except (httpx.HTTPError, ValueError, IndexError, TypeError, KeyError) as e:
        LAST_FETCH_ERRORS[ticker] = f"kucoin:{type(e).__name__}: {e}"
        return None


def _finnhub_symbol(ticker: str) -> tuple[str, str | None]:
    """(endpoint, symbol) for Finnhub's candle API."""
    cls = asset_class(ticker)
    if cls == "fx":
        return "forex", FINNHUB_FX_PAIRS.get(ticker.upper())
    if cls == "crypto":
        return "crypto", "BINANCE:" + ticker.upper().split("-")[0] + "USDT"
    return "stock", ticker.upper()


async def _fetch_finnhub(client: httpx.AsyncClient, ticker: str) -> dict | None:
    """Real 5m OHLCV via a Finnhub API key. Note: Finnhub's free plan does
    NOT include candle endpoints — this activates automatically if the key
    ever has access. A circuit breaker stops retrying after the first 403."""
    global _FINNHUB_CANDLES_DISABLED
    if not config.FINNHUB_KEY or _FINNHUB_CANDLES_DISABLED:
        return None
    endpoint, sym = _finnhub_symbol(ticker)
    if not sym:
        return None
    try:
        resp = await client.get(
            FINNHUB_CANDLE_URL.format(endpoint=endpoint),
            params={"symbol": sym, "resolution": 5, "count": 288, "token": config.FINNHUB_KEY},
            timeout=config.HTTP_TIMEOUT,
        )
        if resp.status_code == 403:
            _FINNHUB_CANDLES_DISABLED = True  # plan lacks candles; stop trying
            return None
        if resp.status_code != 200:
            LAST_FETCH_ERRORS[ticker] = f"finnhub:http {resp.status_code}"
            return None
        data = resp.json()
        if data.get("s") != "ok" or not data.get("t"):
            LAST_FETCH_ERRORS[ticker] = f"finnhub:no_data ({data.get('s')})"
            return None
        bars = []
        for i, ts in enumerate(data["t"]):
            if data["c"][i] is None:
                continue
            bars.append(
                {
                    "ts": int(ts),
                    "open": float(data["o"][i]),
                    "high": float(data["h"][i]),
                    "low": float(data["l"][i]),
                    "close": float(data["c"][i]),
                    "volume": float(data["v"][i] or 0),
                }
            )
        if not bars:
            return None
        bars = bars[-288:]
        return {
            "bars": bars,
            "name": DEMO_NAMES.get(ticker, sym),
            "prev_close": bars[0]["open"],
            "session_open": bars[0]["ts"],
        }
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as e:
        LAST_FETCH_ERRORS[ticker] = f"finnhub:{type(e).__name__}: {e}"
        return None


async def _fetch_finnhub_quote(client: httpx.AsyncClient, ticker: str) -> dict | None:
    """Live equity quote via Finnhub (free tier includes quotes): price, day
    high/low/open, change vs previous close."""
    if not config.FINNHUB_KEY or asset_class(ticker) != "equity":
        return None
    sym = ticker.upper()
    try:
        resp = await client.get(
            FINNHUB_QUOTE_URL,
            params={"symbol": sym, "token": config.FINNHUB_KEY},
            timeout=config.HTTP_TIMEOUT,
        )
        if resp.status_code != 200:
            return None
        q = resp.json()
        if q.get("c") is None:
            return None
        price = float(q["c"])
        prev = q.get("pc")
        chg = round((price - prev) / prev * 100, 2) if prev else None
        return {
            "price": price,
            "open": float(q.get("o") or price),
            "high": float(q.get("h") or price),
            "low": float(q.get("l") or price),
            "change_pct": chg,
            "volume": None,
            "name": sym,
            "ts": q.get("t"),
        }
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as e:
        LAST_FETCH_ERRORS[ticker] = f"finnhubq:{type(e).__name__}: {e}"
        return None


def _cnbc_symbol(ticker: str) -> str | None:
    """Map to CNBC's quote symbol. USD pairs use the quote currency (EURUSD=X
    → EUR=, USDJPY=X → JPY=); crosses use BASEQUOTE=; equities pass through."""
    t = ticker.upper()
    if not t.endswith("=X"):
        return t
    base, quote = t[:3], t[3:6]
    if base == "USD":
        return quote + "="
    if quote == "USD":
        return base + "="
    return t.replace("=X", "=")


async def fetch_cnbc_quote(client: httpx.AsyncClient, ticker: str) -> dict | None:
    """Live quote for equities and FX from CNBC's public API (no key). No
    intraday bars — used when Yahoo refuses so the screener stays populated."""
    sym = _cnbc_symbol(ticker)
    if not sym:
        return None
    try:
        resp = await client.get(
            CNBC_QUOTE_URL,
            params={"symbols": sym, "requestMethod": "itv", "noform": "1"},
            headers={"User-Agent": config.USER_AGENT},
            timeout=config.HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        row = resp.json()["FormattedQuoteResult"]["FormattedQuote"][0]
        if row.get("code") not in (None, 0, "0"):
            return None

        def _f(v, default=None):
            if v is None or str(v).upper() == "UNCH":
                return default
            try:
                return float(v)
            except ValueError:
                return default

        last_val = row.get("last")
        if last_val is None or str(last_val).upper() == "UNCH":
            # Illiquid moments: fall back to the previous close.
            last_val = row.get("previous_day_closing") or row.get("open")
        price = _f(last_val)
        if price is None:
            return None
        open_p = _f(row.get("open"), price)
        high = _f(row.get("high"), price)
        low = _f(row.get("low"), price)
        chg_pct = None
        raw_chg = row.get("change_pct")
        if raw_chg and str(raw_chg).upper() != "UNCH":
            try:
                chg_pct = round(float(str(raw_chg).replace("%", "")), 2)
            except ValueError:
                chg_pct = None
        volume = None
        raw_vol = row.get("volume")
        if raw_vol:
            try:
                volume = int(str(raw_vol).replace(",", ""))
            except ValueError:
                volume = None
        ts = None
        raw_ts = row.get("last_time")
        if raw_ts:
            try:
                ts = int(datetime.fromisoformat(raw_ts).timestamp())
            except ValueError:
                ts = None
        return {
            "price": price,
            "open": open_p,
            "high": high,
            "low": low,
            "change_pct": chg_pct,
            "volume": volume,
            "name": row.get("name") or row.get("shortName") or ticker,
            "ts": ts,
        }
    except (httpx.HTTPError, ValueError, IndexError, TypeError, KeyError) as e:
        LAST_FETCH_ERRORS[ticker] = f"cnbc:{type(e).__name__}: {e}"
        return None


async def _fetch_yahoo_via_jina(client: httpx.AsyncClient, ticker: str, interval: int = 5) -> dict | None:
    """Yahoo chart data through Jina AI's reader proxy. Jina fetches from
    Google Cloud IPs that Yahoo does not rate-limit, so this unblocks real
    candles for equities and FX on serverless platforms. Free tier: ~20
    requests/minute — fine with CDN caching on top."""
    yahoo_url = YAHOO_HOSTS[0].format(ticker=ticker) + f"?range=1d&interval={interval}m"
    try:
        resp = await client.get(
            JINA_BASE + urllib.parse.quote(yahoo_url, safe=""),
            # A realistic browser UA trips Jina's Cloudflare challenge; the
            # simple UA is routed through their bot-allowed path.
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=min(config.HTTP_TIMEOUT + 4.0, 9.0),
        )
        if resp.status_code != 200:
            return None
        marker = "Markdown Content:"
        idx = resp.text.find(marker)
        if idx == -1:
            return None
        payload = json.loads(resp.text[idx + len(marker):].strip())
        return _parse_chart(ticker, payload)
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as e:
        LAST_FETCH_ERRORS[ticker] = f"jina:{type(e).__name__}: {e}"
        return None


async def _fetch_yahoo_with_crumb(client: httpx.AsyncClient, ticker: str) -> dict | None:
    """Yahoo's cookie+crumb handshake. Sometimes unblocks 429s where plain
    requests are rate-limited."""
    try:
        await client.get(
            "https://fc.yahoo.com",
            headers={"User-Agent": config.USER_AGENT},
            timeout=config.HTTP_TIMEOUT,
        )
        crumb_resp = await client.get(
            "https://query1.finance.yahoo.com/v1/test/getcrumb",
            headers={"User-Agent": config.USER_AGENT},
            timeout=config.HTTP_TIMEOUT,
        )
        crumb = crumb_resp.text.strip()
        if not crumb or "Too Many" in crumb or len(crumb) > 40:
            return None
        url = YAHOO_HOSTS[0].format(ticker=ticker)
        resp = await client.get(
            url,
            params={"range": "1d", "interval": "5m", "crumb": crumb},
            headers={"User-Agent": config.USER_AGENT},
            timeout=config.HTTP_TIMEOUT,
        )
        if resp.status_code == 200:
            return _parse_chart(ticker, resp.json())
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
        pass
    return None


async def _fetch_nasdaq_series(client: httpx.AsyncClient, ticker: str) -> dict | None:
    """Nasdaq's public intraday chart: 5-minute closes (no OHLC). We build
    honest wickless candles from consecutive closes — the bodies are real
    price moves, high/low are simply the body extremes."""
    if asset_class(ticker) != "equity":
        return None
    try:
        for assetclass in ("stocks", "etf"):
            resp = await client.get(
                NASDAQ_CHART_URL.format(sym=ticker),
                params={"assetclass": assetclass},
                headers={"User-Agent": config.USER_AGENT, "Accept": "application/json"},
                timeout=config.HTTP_TIMEOUT,
            )
            if resp.status_code != 200:
                continue
            payload = resp.json()
            chart = (payload.get("data") or {}).get("chart") or []
            if chart:
                break
        else:
            return None
        pts = []
        for item in chart:
            ts = item.get("x")
            close = item.get("y")
            if ts and close:
                pts.append((int(ts) // 1000, float(close)))
        if len(pts) < 5:
            return None
        bars = []
        prev_close = pts[0][1]
        for ts, close in pts:
            open_p = prev_close
            bars.append(
                {
                    "ts": ts,
                    "open": round(open_p, 4),
                    "high": round(max(open_p, close), 4),
                    "low": round(min(open_p, close), 4),
                    "close": round(close, 4),
                    "volume": 0,
                }
            )
            prev_close = close
        bars = bars[-288:]
        return {
            "bars": bars,
            "name": ticker,
            "prev_close": bars[0]["open"],
            "session_open": bars[0]["ts"],
            "synthetic": True,
        }
    except Exception as e:  # noqa: BLE001 — best-effort fallback, never crash
        LAST_FETCH_ERRORS[ticker] = f"nasdaq:{type(e).__name__}: {e}"
        return None


async def fetch_ticker(client: httpx.AsyncClient, ticker: str, interval: int = 5) -> dict | None:
    """Returns {'bars': [...], 'name': str, 'prev_close': float, 'session_open': int}
    or None on any failure. Interval in minutes (1 or 5). Retries with backoff,
    rotates hosts, then falls through a datacenter-friendly source cascade."""
    async with _FETCH_SEM:
        if config.FINNHUB_KEY:
            finnhub_data = await _fetch_finnhub(client, ticker)
            if finnhub_data:
                return finnhub_data
        last_err: Exception | None = None
        for attempt in range(max(1, config.FETCH_RETRIES)):
            url = YAHOO_HOSTS[attempt % len(YAHOO_HOSTS)].format(ticker=ticker)
            try:
                resp = await client.get(
                    url,
                    params={"range": "1d", "interval": f"{interval}m"},
                    headers={"User-Agent": config.USER_AGENT},
                    timeout=config.HTTP_TIMEOUT,
                )
                if resp.status_code == 429:
                    last_err = httpx.HTTPStatusError(
                        "429 Too Many Requests", request=resp.request, response=resp
                    )
                    await _bootstrap_cookies(client)
                elif resp.status_code >= 400:
                    last_err = httpx.HTTPStatusError(
                        f"{resp.status_code}", request=resp.request, response=resp
                    )
                else:
                    resp.raise_for_status()
                    return _parse_chart(ticker, resp.json())
            except (httpx.HTTPError, KeyError, ValueError, IndexError, TypeError) as e:
                last_err = e
                LAST_FETCH_ERRORS[ticker] = f"yahoo:{type(e).__name__}: {e}"
            except Exception as e:  # noqa: BLE001 — surface anything unexpected
                last_err = e
                LAST_FETCH_ERRORS[ticker] = f"yahoo:{type(e).__name__}: {e}"
            if attempt < max(0, config.FETCH_RETRIES - 1):
                await asyncio.sleep(1.5 * (attempt + 1) + random.random())
        if asset_class(ticker) == "crypto":
            for src in (_fetch_kraken, _fetch_coinbase, _fetch_kucoin):
                data = await src(client, ticker, interval)
                if data:
                    return data
        else:
            # FX: Jina-proxied Yahoo (fresh IPs, real candles). Equities use
            # the Nasdaq series. Jina is reserved for FX only (12 pairs) to
            # stay under its free-tier rate limit.
            try:
                if asset_class(ticker) == "fx":
                    via_jina = await _fetch_yahoo_via_jina(client, ticker, interval)
                    if via_jina:
                        return via_jina
                series = await _fetch_nasdaq_series(client, ticker)
                if series:
                    return series
            except Exception as e:  # noqa: BLE001 — fallback layer never crashes
                LAST_FETCH_ERRORS[ticker] = f"fallback:{type(e).__name__}: {e}"
        log.warning("fetch %s failed after retries: %s", ticker, last_err)
        return None


def _parse_chart(ticker: str, payload: dict) -> dict | None:
    result = payload["chart"]["result"][0]
    meta = result["meta"]
    q = result["indicators"]["quote"][0]
    timestamps = result.get("timestamp", [])
    bars = []
    for i, ts in enumerate(timestamps):
        close = q["close"][i]
        if close is None:
            continue
        bars.append(
            {
                "ts": int(ts),
                "open": q["open"][i],
                "high": q["high"][i],
                "low": q["low"][i],
                "close": close,
                "volume": q["volume"][i] or 0,
            }
        )
    if not bars:
        return None
    # Session open: the regular trading period start reported by the feed.
    session_open = None
    try:
        periods = meta.get("currentTradingPeriod", {}).get("regular", {})
        if periods.get("start"):
            session_open = int(periods["start"])
    except Exception:
        session_open = bars[0]["ts"]
    return {
        "bars": bars,
        "name": meta.get("longName") or meta.get("shortName") or ticker,
        "prev_close": meta.get("previousClose"),
        "session_open": session_open,
    }

DEMO_BASE_PRICES = {
    # equities
    "AAPL": 232.0, "TSLA": 262.0, "NVDA": 141.0, "AMD": 164.0, "SPY": 601.0,
    "QQQ": 521.0, "MSFT": 431.0, "META": 586.0, "AMZN": 204.0, "GOOGL": 176.0,
    "NFLX": 918.0, "PLTR": 92.0,
    # crypto (trades 24/7)
    "BTC-USD": 96500.0, "ETH-USD": 3420.0, "SOL-USD": 198.0, "XRP-USD": 2.42,
    "DOGE-USD": 0.31, "ADA-USD": 0.95, "BNB-USD": 675.0, "LINK-USD": 24.5,
    "LTC-USD": 118.0, "AVAX-USD": 42.0, "DOT-USD": 8.9, "XLM-USD": 0.42,
    # forex (trades ~24/5, Sun 17:00 ET → Fri 17:00 ET)
    "EURUSD=X": 1.0845, "GBPUSD=X": 1.2695, "USDJPY=X": 149.2,
    "AUDUSD=X": 0.6542, "USDCAD=X": 1.3568, "USDCHF=X": 0.8662,
    "NZDUSD=X": 0.5921, "EURGBP=X": 0.8532, "EURJPY=X": 161.8,
    "GBPJPY=X": 189.4, "USDMXN=X": 17.82, "USDCNY=X": 7.21,
}
DEMO_DAY_DRIFT = {
    # equities
    "TSLA": 0.032, "NVDA": 0.021, "AMD": -0.024, "PLTR": 0.028, "MSFT": -0.012,
    # crypto movers
    "BTC-USD": 0.028, "ETH-USD": 0.021, "SOL-USD": -0.030, "DOGE-USD": 0.038,
    "XRP-USD": -0.018,
    # forex: tiny daily ranges
    "EURUSD=X": 0.004, "USDJPY=X": -0.005, "GBPUSD=X": 0.003,
}
DEMO_NAMES = {
    "BTC-USD": "Bitcoin USD", "ETH-USD": "Ethereum USD", "SOL-USD": "Solana USD",
    "XRP-USD": "XRP USD", "DOGE-USD": "Dogecoin USD", "ADA-USD": "Cardano USD",
    "BNB-USD": "BNB USD", "LINK-USD": "Chainlink USD", "LTC-USD": "Litecoin USD",
    "AVAX-USD": "Avalanche USD", "DOT-USD": "Polkadot USD", "XLM-USD": "Stellar USD",
    "EURUSD=X": "Euro / US Dollar", "GBPUSD=X": "British Pound / US Dollar",
    "USDJPY=X": "US Dollar / Japanese Yen", "AUDUSD=X": "Australian Dollar / US Dollar",
    "USDCAD=X": "US Dollar / Canadian Dollar", "USDCHF=X": "US Dollar / Swiss Franc",
    "NZDUSD=X": "New Zealand Dollar / US Dollar", "EURGBP=X": "Euro / British Pound",
    "EURJPY=X": "Euro / Japanese Yen", "GBPJPY=X": "British Pound / Japanese Yen",
    "USDMXN=X": "US Dollar / Mexican Peso", "USDCNY=X": "US Dollar / Chinese Yuan",
}


def asset_class(ticker: str) -> str:
    """'fx' | 'crypto' | 'equity' from Yahoo symbol conventions."""
    t = ticker.upper().strip()
    if t.endswith("=X"):
        return "fx"
    if t.endswith("-USD") or t.endswith("-USDT") or t.endswith("-EUR"):
        return "crypto"
    return "equity"


def _bars_timeline(ticker: str) -> list[int]:
    """5m bar timestamps for the asset's trading window, up to now (ET).
    Equities: 09:30–16:00. FX: 24h on weekdays. Crypto: 24/7 (midnight ET)."""
    now = datetime.now(ET)
    cls = asset_class(ticker)
    if cls != "crypto" and now.weekday() >= 5:
        return []
    if cls == "equity":
        start = now.replace(hour=9, minute=30, second=0, microsecond=0)
    else:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    ts = int(start.timestamp())
    last = int(now.timestamp()) // 300 * 300
    return list(range(ts, last + 1, 300))


def fetch_demo(ticker: str) -> dict | None:
    """Deterministic intraday walk, seeded per (ticker, bar ts). Continues from
    the last stored bar if one exists so refreshes look continuous."""
    base = DEMO_BASE_PRICES.get(ticker)
    if base is None:
        return None
    drift = DEMO_DAY_DRIFT.get(ticker, 0.0)
    timestamps = _bars_timeline(ticker)
    if not timestamps:
        return None

    cls = asset_class(ticker)
    dp = 5 if cls == "fx" else (4 if cls == "crypto" else 2)  # small-base assets need decimals
    prev_close = round(base * (1 - drift * 0.35), dp)
    existing = db.bars_for(ticker, limit=600)
    existing_map = {b["ts"]: b for b in existing}
    session_open = min(timestamps)

    bars = []
    last_close = existing_map[timestamps[0]]["close"] if timestamps[0] in existing_map else prev_close
    n = len(timestamps)
    for i, ts in enumerate(timestamps):
        if ts in existing_map:
            b = existing_map[ts]
            last_close = b["close"]
            bars.append(b)
            continue
        rng = random.Random(f"{ticker}:{ts}")
        frac = i / max(n - 1, 1)
        # Spread the full-day drift across every bar; noise is per-bar.
        trend = drift / n
        # Per-bar noise ≈ daily vol / sqrt(bars per day): crypto ~4%/day,
        # fx ~1.3%/day, equities ~3.5%/day.
        noise_scale = {"crypto": 0.003, "fx": 0.0012, "equity": 0.0035}[cls]
        if abs(drift) > 0.015:
            noise_scale *= 1.4  # movers swing harder
        if ticker == "TSLA":
            noise_scale = 0.007  # the wild one
        noise = (rng.random() - 0.5) * noise_scale
        chg = trend + noise
        open_p = last_close * (1 + (rng.random() - 0.5) * 0.0008)
        close = last_close * (1 + chg)
        hi = max(open_p, close) * (1 + rng.random() * 0.0012)
        lo = min(open_p, close) * (1 - rng.random() * 0.0012)
        vol = int(base * 800 * (1 + 0.4 * abs(chg) * 400) * (0.7 + rng.random() * 0.6))
        if abs(drift) > 0.015:  # movers trade heavier
            vol = int(vol * 1.8)
            if i >= n - 6:  # late-day surge → elevated relvol in the screener
                vol = int(vol * 3.0)
        if i < 6:  # opening push
            vol = int(vol * 1.6)
        bars.append(
            {"ts": ts, "open": round(open_p, dp), "high": round(hi, dp),
             "low": round(lo, dp), "close": round(close, dp), "volume": vol}
        )
        last_close = close

    return {
        "bars": bars,
        "name": DEMO_NAMES.get(ticker, ticker),
        "prev_close": prev_close,
        "session_open": session_open,
    }


async def refresh_all(force: bool = False) -> dict:
    """Fetch watchlist, upsert into DB. Returns a per-ticker summary."""
    summary: dict[str, str] = {}
    if config.DATA_MODE == "demo":
        for ticker in config.WATCHLIST:
            try:
                data = await asyncio.to_thread(fetch_demo, ticker)
                if data:
                    db.upsert_meta(ticker, data["name"], data["prev_close"], data["session_open"])
                    db.upsert_bars(ticker, data["bars"])
                    summary[ticker] = "demo"
            except Exception as e:
                log.warning("demo %s failed: %s", ticker, e)
                summary[ticker] = f"error: {e}"
        return summary

    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            *(fetch_ticker(client, t) for t in config.WATCHLIST),
            return_exceptions=True,
        )
    for ticker, res in zip(config.WATCHLIST, results):
        if isinstance(res, Exception):
            log.warning("live fetch %s failed: %s", ticker, res)
            summary[ticker] = f"error: {res}"
            continue
        if res is None:
            summary[ticker] = "no data"
            continue
        try:
            db.upsert_meta(ticker, res["name"], res["prev_close"], res["session_open"])
            db.upsert_bars(ticker, res["bars"])
            summary[ticker] = f"ok ({len(res['bars'])} bars)"
        except Exception as e:
            summary[ticker] = f"error: {e}"
    return summary


def market_state(now: datetime | None = None, asset: str = "equity") -> tuple[str, int | None]:
    """('pre'|'open'|'after'|'closed', minutes_into_session).

    - crypto: always open (24/7).
    - fx: open Sun 17:00 ET → Fri 17:00 ET.
    - equity: US regular session, 09:30–16:00 ET weekdays.
    """
    now = now or datetime.now(ET)
    mins = now.hour * 60 + now.minute
    wd = now.weekday()

    if asset == "crypto":
        return "open", None

    if asset == "fx":
        if wd == 6:  # Sunday: opens 17:00
            return ("open", None) if mins >= 17 * 60 else ("closed", None)
        if wd == 4 and mins >= 17 * 60:  # Friday close
            return "closed", None
        if wd <= 4:  # Mon–Thu all day, Fri before 17:00
            return "open", None
        return "closed", None  # Saturday

    # equity
    if wd >= 5:
        return "closed", None
    if mins < config.SESSION_OPEN_MINUTES:
        return "pre", None
    if mins > config.SESSION_CLOSE_MINUTES:
        return "after", None
    return "open", mins - config.SESSION_OPEN_MINUTES
