import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


if getattr(sys, "frozen", False):
    ROOT_DIR = Path(sys.executable).resolve().parent
else:
    ROOT_DIR = Path(__file__).resolve().parent.parent

LOG_DIR = ROOT_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

_file_handler = RotatingFileHandler(
    LOG_DIR / "application.log",
    maxBytes=10 * 1024 * 1024,
    backupCount=7,
    encoding="utf-8",
)
_file_handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s"))

logging.basicConfig(level=logging.INFO, handlers=[_file_handler])

logger = logging.getLogger("app")
