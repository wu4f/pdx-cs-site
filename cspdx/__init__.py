"""pdx-cs-site package. Loads a repo-root .env (if present) on import so the
CLI and server pick up GOOGLE_API_KEY / ADMIN_TOKEN / etc. without manual export.
Real environment variables always take precedence (override=False)."""
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
except ImportError:
    # python-dotenv not installed: fall back to real env vars only.
    pass
