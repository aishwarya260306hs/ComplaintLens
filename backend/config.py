"""Central configuration. Everything can be overridden with environment variables or a .env file."""
import os
import secrets
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # python-dotenv is optional
    pass

BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = BASE_DIR / "instance"


def _backend_path(value):
    path = Path(value).expanduser()
    return str(path if path.is_absolute() else BASE_DIR / path)


def _flag(name, default=False):
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


def _secret_key():
    """SECRET_KEY from env, otherwise a key generated once and kept in instance/secret.key."""
    key = os.environ.get("SECRET_KEY")
    if key:
        return key
    INSTANCE_DIR.mkdir(exist_ok=True)
    path = INSTANCE_DIR / "secret.key"
    if not path.exists():
        path.write_text(secrets.token_hex(32))
        try:
            path.chmod(0o600)
        except OSError:
            pass
    return path.read_text().strip()


class Config:
    SECRET_KEY = _secret_key()

    # ---- Database (SQLite + SQLAlchemy) ----
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL") or \
        "sqlite:///" + (INSTANCE_DIR / "hacknetix.db").as_posix()
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # ---- Sessions / uploads ----
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _flag("COOKIE_SECURE")        # set COOKIE_SECURE=1 behind HTTPS
    MAX_CONTENT_LENGTH = 6 * 1024 * 1024                   # request cap (image limit is 5 MB)
    UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER") or str(BASE_DIR / "uploads")

    # ---- Fine-tuned HACKNETIX Sentence Transformer (local only) ----
    _DEFAULT_MODEL_ROOT = BASE_DIR.parent / "ai_model" / "models" / "hacknetix_sentence_transformer"
    MODEL_ROOT = _backend_path(os.environ.get("MODEL_ROOT") or _DEFAULT_MODEL_ROOT)
    MODEL_ZIP = _backend_path(os.environ["MODEL_ZIP"]) if os.environ.get("MODEL_ZIP") else ""
    # Development-only escape hatch. When 0 (default) the app refuses to start without the real model.
    ALLOW_FALLBACK_EMBEDDER = _flag("ALLOW_FALLBACK_EMBEDDER", False)
    # Existing cosine-similarity cutoff; it has not been calibrated for the fine-tuned model.
    SIMILARITY_THRESHOLD = float(os.environ["SIMILARITY_THRESHOLD"]) if os.environ.get("SIMILARITY_THRESHOLD") else None
    MAX_SUGGESTIONS = int(os.environ.get("MAX_SUGGESTIONS", "5"))

    CATEGORIES = [
        "Infrastructure", "Academics", "Hostel & Housing", "Canteen & Food", "Transport",
        "IT & Network", "Safety & Security", "Administration", "Other",
    ]
