"""Source inclusion/exclusion policies shared by collectors and brief builders."""

import re
from urllib.parse import urlparse


VIETNAM_COUNTRY_NAMES = {"vietnam", "viet nam", "vn", "việt nam"}
VIETNAMESE_HOST_MARKERS = (".vn", "vnexpress", "tuoitre", "thanhnien", "vietnamnet")


def vietnam_source_reason(source_or_item):
    """Return a stable exclusion reason, or an empty string when not Vietnamese."""
    value = source_or_item or {}
    country = str(value.get("Country") or value.get("country") or "").strip().lower()
    if country in VIETNAM_COUNTRY_NAMES:
        return "country=Vietnam"

    for key in ("Website", "website", "url", "original_url", "resolved_url"):
        raw = str(value.get(key) or "").strip()
        if not raw:
            continue
        host = (urlparse(raw).hostname or "").lower()
        if host.endswith(".vn") or any(marker in host for marker in VIETNAMESE_HOST_MARKERS):
            return "domain=Vietnam"

    language = str(value.get("Language") or value.get("language") or "").strip().lower()
    if language in {"vi", "vi-vn", "vietnamese"} and country not in {"global", "international"}:
        return "language=Vietnamese"
    return ""


def is_vietnamese_source(source_or_item):
    return bool(vietnam_source_reason(source_or_item))


def filter_sources(sources, exclude_vietnam=False):
    """Filter configured sources and return (kept, excluded[{source, reason}])."""
    kept = []
    excluded = []
    for source in sources or []:
        reason = vietnam_source_reason(source) if exclude_vietnam else ""
        if reason:
            excluded.append({"source": source, "reason": reason})
        else:
            kept.append(source)
    return kept, excluded


def normalize_filter_flag(value):
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}
