import json
import os
from pathlib import Path

from dotenv import load_dotenv

from app.config import ROOT_DIR


SETTINGS_PATH = ROOT_DIR / "config" / "runtime_settings.json"
ENV_PATH = ROOT_DIR / ".env"


DEFAULT_SETTINGS = {
    "scan": {
        "auto_run_enabled": False,
        "priority": "P1",
        "limit_per_source": 10,
        "brief_limit": 12,
        "keywords": "port congestion, freight rate, vessel accident, Vietnam logistics",
        "runs_per_day": 2,
        "times": ["07:15", "19:15"],
        "timezone_offset": "+7",
        "retry_attempts": 2,
        "preferred_sources": [],
    },
    "ai": {
        "provider": "mock",
        "model": "rule-based-mock",
        "min_score": 6,
        "force_summary": False,
        "request_delay_seconds": 1.5,
        "retry_attempts": 2,
    },
    "visual": {
        "source_mode": "combined",
        "sheet_url": "",
        "sheet_limit_max": True,
        "sheet_limit": 20,
        "app_limit_max": True,
        "app_limit": 20,
        "card_limit_max": True,
        "card_limit": 12,
        "background_mode": "article",
        "font_family": "Arial",
        "title_size": 54,
        "summary_size": 31,
        "impact_size": 27,
        "text_color": "#102033",
        "accent_color": "#0f766e",
        "watermark": "Maritime Intelligence Hub",
        "show_title": True,
        "show_summary": True,
        "show_impact": True,
        "show_source": True,
        "show_url": True,
        "show_hot_keywords": True,
    },
    "publish": {
        "create_markdown": True,
        "create_json": True,
        "create_image_cards": False,
        "send_telegram": False,
        "post_facebook": False,
        "telegram_chat_id": "",
        "telegram_chat_ids": [],
        "telegram_intro_text": "{date}",
    },
}


def load_runtime_settings(path=SETTINGS_PATH):
    settings = _deep_copy(DEFAULT_SETTINGS)
    path = Path(path)
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            _deep_update(settings, loaded)
        except (OSError, json.JSONDecodeError):
            pass
    return settings


def save_runtime_settings(settings, path=SETTINGS_PATH):
    merged = _deep_copy(DEFAULT_SETTINGS)
    _deep_update(merged, settings or {})
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    return merged


def load_ai_env():
    load_dotenv(ENV_PATH, encoding="utf-8-sig")
    return {
        "AI_PROVIDER": os.getenv("AI_PROVIDER", "mock"),
        "OPENAI_MODEL": os.getenv("OPENAI_MODEL", ""),
        "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY", ""),
        "GEMINI_MODEL": os.getenv("GEMINI_MODEL", ""),
        "GEMINI_API_KEY": os.getenv("GEMINI_API_KEY", ""),
        "TELEGRAM_BOT_TOKEN": os.getenv("TELEGRAM_BOT_TOKEN", ""),
    }


def save_ai_env(values):
    current = _read_env_file(ENV_PATH)
    for key, value in values.items():
        current[key] = str(value or "")
        os.environ[key] = str(value or "")
    lines = [f"{key}={value}" for key, value in current.items()]
    ENV_PATH.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def _read_env_file(path):
    path = Path(path)
    if not path.exists():
        return {}
    result = {}
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def _deep_copy(value):
    return json.loads(json.dumps(value))


def _deep_update(target, source):
    for key, value in (source or {}).items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value
