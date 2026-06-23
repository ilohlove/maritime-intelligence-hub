import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

from app.config import ROOT_DIR


SETTINGS_PATH = ROOT_DIR / "config" / "runtime_settings.json"
ENV_PATH = ROOT_DIR / ".env"
VIETNAM_TIMEZONE = timezone(timedelta(hours=7))

LEGACY_FACEBOOK_INTRO_TEXT = "{brief_label}\n{date}\n\nNguon duoc ghi tren tung anh.\nMaritime Intelligence Hub"
FACEBOOK_HASHTAGS = [
    "#MaritimeIntelligenceHub",
    "#MaritimeBrief",
    "#ShippingNews",
    "#MaritimeIndustry",
    "#ContainerShipping",
    "#PortOperations",
    "#MarineSafety",
    "#DeckOfficer",
    "#ChiefOfficer",
    "#MerchantNavy",
]
PREVIOUS_DEFAULT_FACEBOOK_INTRO_TEXT = "\n".join(
    [
        "{facebook_title}",
        "",
        "Tổng hợp nhanh những tin tức đáng chú ý nhất trong 24 giờ qua từ các nguồn hàng hải quốc tế và trong nước.",
        "",
        "Đọc trong 3 phút để nắm được những thay đổi quan trọng về hãng tàu, cảng biển, an toàn hàng hải, quy định mới và thị trường vận tải biển.",
        "",
        "📅 Cập nhật lúc 07:30 và 19:30 mỗi ngày.",
        "Để không bỏ lỡ bài Điểm tin nào, bạn có thể Theo dõi tớ và chọn Xem trước.",
        "",
        *FACEBOOK_HASHTAGS,
    ]
)
DEFAULT_FACEBOOK_INTRO_TEXT = "\n".join(
    [
        "Tóm tắt nhanh cho anh em những chuyển động đáng chú ý nhất của ngành trong nửa ngày qua.",
        "",
        "Khung giờ phát sóng: 7:30 và 19:30 mỗi ngày.",
        "",
        "Nhớ ấn Follow và thêm trang vào Yêu thích để không bỏ sót bất kỳ bản tin quan trọng nào nha!",
    ]
)


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
        "facebook_intro_text": DEFAULT_FACEBOOK_INTRO_TEXT,
        "facebook_dry_run": True,
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
    _normalize_runtime_settings(settings)
    return settings


def save_runtime_settings(settings, path=SETTINGS_PATH):
    merged = _deep_copy(DEFAULT_SETTINGS)
    _deep_update(merged, settings or {})
    _normalize_runtime_settings(merged)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    return merged


def render_facebook_intro_text(template=None, brief_label=None, now=None):
    text = _facebook_template_or_default(template)
    now = now or datetime.now(timezone.utc).astimezone(VIETNAM_TIMEZONE)
    brief_text = facebook_brief_label_text(brief_label, now=now)
    return (
        text.replace("{date}", now.strftime("%d/%m/%Y"))
        .replace("{datetime}", now.strftime("%d/%m/%Y %H:%M"))
        .replace("{brief_label}", brief_text)
        .replace("{facebook_title}", facebook_brief_title(brief_label, now=now))
    )


def facebook_brief_title(brief_label=None, now=None):
    period = _facebook_period(brief_label, now=now)
    label = "BUỔI SÁNG" if period == "morning" else "BUỔI TỐI"
    return f"⚓ ĐIỂM TIN HÀNG HẢI {label}"


def facebook_brief_label_text(brief_label=None, now=None):
    period = _facebook_period(brief_label, now=now)
    return "Bản tin buổi sáng" if period == "morning" else "Bản tin buổi tối"


def load_ai_env():
    load_dotenv(ENV_PATH, encoding="utf-8-sig")
    return {
        "AI_PROVIDER": os.getenv("AI_PROVIDER", "mock"),
        "OPENAI_MODEL": os.getenv("OPENAI_MODEL", ""),
        "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY", ""),
        "GEMINI_MODEL": os.getenv("GEMINI_MODEL", ""),
        "GEMINI_API_KEY": os.getenv("GEMINI_API_KEY", ""),
        "TELEGRAM_BOT_TOKEN": os.getenv("TELEGRAM_BOT_TOKEN", ""),
        "FACEBOOK_PAGE_ID": os.getenv("FACEBOOK_PAGE_ID", ""),
        "FACEBOOK_PAGE_ACCESS_TOKEN": os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN", ""),
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


def _normalize_runtime_settings(settings):
    publish = settings.setdefault("publish", {})
    if _is_legacy_facebook_template(publish.get("facebook_intro_text")):
        publish["facebook_intro_text"] = DEFAULT_FACEBOOK_INTRO_TEXT


def _facebook_template_or_default(template):
    if not template or _is_legacy_facebook_template(template):
        return DEFAULT_FACEBOOK_INTRO_TEXT
    return str(template).strip()


def _is_legacy_facebook_template(template):
    return str(template or "").strip() in {
        LEGACY_FACEBOOK_INTRO_TEXT,
        PREVIOUS_DEFAULT_FACEBOOK_INTRO_TEXT,
    }


def _facebook_period(brief_label=None, now=None):
    label = str(brief_label or "").strip().lower()
    if label == "morning":
        return "morning"
    if label == "evening":
        return "evening"
    now = now or datetime.now(timezone.utc).astimezone(VIETNAM_TIMEZONE)
    return "morning" if now.hour < 12 else "evening"
