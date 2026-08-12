"""Runtime configuration, all overridable via environment variables."""
import os


def _env_list(name: str, default: str) -> list[str]:
    raw = os.getenv(name, default)
    return [t.strip().upper() for t in raw.split(",") if t.strip()]


WATCHLIST: list[str] = _env_list(
    "WATCHLIST",
    # US equities
    "AAPL,TSLA,NVDA,AMD,SPY,QQQ,MSFT,META,AMZN,GOOGL,"
    # crypto majors (24/7)
    "BTC-USD,ETH-USD,SOL-USD,XRP-USD,DOGE-USD,ADA-USD,"
    "BNB-USD,LINK-USD,LTC-USD,AVAX-USD,DOT-USD,XLM-USD,"
    # forex majors
    "EURUSD=X,GBPUSD=X,USDJPY=X,AUDUSD=X,USDCAD=X,USDCHF=X,"
    "NZDUSD=X,EURGBP=X,EURJPY=X,GBPJPY=X,USDMXN=X,USDCNY=X",
)
REFRESH_SECONDS: int = int(os.getenv("REFRESH_SECONDS", "120"))
DB_PATH: str = os.getenv("DB_PATH", "market.db")
DATA_MODE: str = os.getenv("DATA_MODE", "live").lower()  # "live" | "demo"
# When live fetching fails, optionally substitute demo bars for that ticker.
LIVE_FALLBACK_TO_DEMO: bool = os.getenv("LIVE_FALLBACK_TO_DEMO", "0") == "1"
# Serverless (Vercel sets VERCEL=1): functions have a ~10s budget, so keep the
# fetch plan short — one retry, 6s timeouts. The dedicated poller uses more.
SERVERLESS: bool = os.getenv("VERCEL", "0") == "1"
MAX_POSITION_PCT: float = float(os.getenv("MAX_POSITION_PCT", "25"))
MAX_BARS_PER_TICKER: int = 1200
HTTP_TIMEOUT: float = float(os.getenv("HTTP_TIMEOUT", "6" if SERVERLESS else "10"))
FETCH_RETRIES: int = int(os.getenv("FETCH_RETRIES", "1" if SERVERLESS else "3"))
USER_AGENT: str = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

# US regular session (America/New_York)
SESSION_OPEN_MINUTES: int = 9 * 60 + 30  # 570
SESSION_CLOSE_MINUTES: int = 16 * 60  # 960
# Quiet-zone rules for entries (minutes)
OPEN_QUIET_MINUTES: int = 5
CLOSE_QUIET_MINUTES: int = 10
