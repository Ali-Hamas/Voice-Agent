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
