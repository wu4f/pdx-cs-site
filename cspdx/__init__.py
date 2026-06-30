"""pdx-cs-site package. Loads a repo-root .env (if present) on import so the
CLI and server pick up GOOGLE_API_KEY / ADMIN_TOKEN / etc. without manual export.
Real environment variables always take precedence (override=False)."""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
except ImportError:
    # python-dotenv not installed: fall back to real env vars only.
    pass

# Default Gemini model. Override via GEMINI_MODEL env var (or .env) — env var
# takes priority over content.yaml chat.model for all pipeline stages.
DEFAULT_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
