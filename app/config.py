import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_REALTIME_MODEL = os.getenv(
    "OPENAI_REALTIME_MODEL", "gpt-4o-realtime-preview-2024-12-17"
)

PUBLIC_HOST = os.getenv("PUBLIC_HOST", "")
PORT = int(os.getenv("PORT", "8000"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

DATA_DIR = ROOT / "data"
RESTAURANTS_DIR = DATA_DIR / "restaurants"
DB_PATH = DATA_DIR / "voiceagent.sqlite"

EMBED_MODEL = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")

SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me-in-production")

# Outbound calling
OUTBOUND_GLOBAL_CONCURRENCY = int(os.getenv("OUTBOUND_GLOBAL_CONCURRENCY", "5"))
OUTBOUND_DISPATCH_INTERVAL_SEC = int(os.getenv("OUTBOUND_DISPATCH_INTERVAL_SEC", "5"))
OUTBOUND_SCHEDULER_INTERVAL_SEC = int(os.getenv("OUTBOUND_SCHEDULER_INTERVAL_SEC", "60"))
OUTBOUND_RETRY_DELAY_SEC = int(os.getenv("OUTBOUND_RETRY_DELAY_SEC", "1800"))
OUTBOUND_MAX_ATTEMPTS = int(os.getenv("OUTBOUND_MAX_ATTEMPTS", "2"))
