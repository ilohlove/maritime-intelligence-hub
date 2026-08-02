import logging
import os
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


if getattr(sys, "frozen", False):
    ROOT_DIR = Path(sys.executable).resolve().parent
else:
    ROOT_DIR = Path(__file__).resolve().parent.parent

LOG_DIR = ROOT_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)


def _redact(value):
    text = str(value or "")
    text = re.sub(r"(?i)([?&](?:key|api[_-]?key|token|access_token)=)[^&\s]+", r"\1***", text)
    text = re.sub(r"(?i)((?:x-goog-api-key|authorization|api[_-]?key)\s*[:=]\s*)[^\s,;]+", r"\1***", text)
    for env_key in ("OPENAI_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY"):
        secret = os.getenv(env_key, "").strip()
        if secret:
            text = text.replace(secret, "***")
    return text


class SecretRedactionFilter(logging.Filter):
    def filter(self, record):
        record.msg = _redact(record.getMessage())
        record.args = ()
        return True

def _scrub_existing_log(path):
    path = Path(path)
    if not path.exists():
        return
    try:
        original = path.read_text(encoding="utf-8", errors="replace")
        scrubbed = _redact(original)
        if scrubbed == original:
            return
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_text(scrubbed, encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        return


_scrub_existing_log(LOG_DIR / "application.log")

_file_handler = RotatingFileHandler(
    LOG_DIR / "application.log",
    maxBytes=10 * 1024 * 1024,
    backupCount=7,
    encoding="utf-8",
)
_file_handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s"))
_file_handler.addFilter(SecretRedactionFilter())

logging.basicConfig(level=logging.INFO, handlers=[_file_handler])

logger = logging.getLogger("app")
