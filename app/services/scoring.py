import json
from datetime import datetime, timezone

from app.services.storage import get_articles_for_scoring, list_trend_keywords, update_article_score
from app.services.trend_collector import seed_default_trends


CATEGORY_BONUS = {
    "Safety": 2,
    "Regulation": 2,
    "Accident Investigation": 2,
    "Container": 1,
    "Port": 1,
    "Logistics": 1,
    "Trade": 1,
    "Shipping Market": 1,
}

PRIORITY_BONUS = {"P1": 2, "P2": 1, "P3": 0}
COPYRIGHT_PENALTY = {"Low": 0, "Medium": 1, "High": 3}


def score_pending_articles(db_path=None):
    articles = get_articles_for_scoring(db_path=db_path) if db_path else get_articles_for_scoring()
    seed_default_trends(db_path=db_path)
    trends = list_trend_keywords(db_path=db_path) if db_path else list_trend_keywords()
    scored = []
    for article in articles:
        score_result = calculate_hotness_score(article, trends=trends)
        update_article_score(
            article["id"],
            score_result["importance_score"],
            db_path=db_path,
            hotness_score=score_result["hotness_score"],
            hot_keywords=score_result["hot_keywords"],
            why_hot=score_result["why_hot"],
        )
        scored.append(
            {
                "article_id": article["id"],
                "title": article["title"],
                "score": score_result["importance_score"],
                "hotness_score": score_result["hotness_score"],
                "hot_keywords": score_result["hot_keywords"],
            }
        )
    return scored


def calculate_importance_score(article):
    return calculate_hotness_score(article)["importance_score"]


def calculate_hotness_score(article, trends=None):
    quality = int(article.get("content_quality_score") or 5)
    business = int(article.get("business_value_score") or 5)
    base = round((quality + business) / 2)

    score = base
    score += PRIORITY_BONUS.get(article.get("priority"), 0)
    score += CATEGORY_BONUS.get(article.get("category"), 0)
    score -= COPYRIGHT_PENALTY.get(article.get("copyright_risk"), 0)

    if article.get("status") == "duplicate_title":
        score -= 3

    title = (article.get("title") or "").lower()
    if any(keyword in title for keyword in ["accident", "collision", "fire", "ban", "strike", "port", "safety"]):
        score += 1

    hotness = score
    reasons = []

    recency_bonus, recency_reason = _recency_bonus(article.get("published_at"))
    hotness += recency_bonus
    if recency_reason:
        reasons.append(recency_reason)

    if not article.get("published_at"):
        hotness -= 4
        reasons.append("missing published_at; held back from daily hot ranking")

    if article.get("country") and article.get("country") != "Vietnam":
        hotness += 1
        reasons.append("international P1 source")

    if _looks_like_menu_or_category(article):
        hotness -= 5
        reasons.append("looks like category/menu page")

    matched_keywords = _matched_trend_keywords(article, trends or [])
    if matched_keywords:
        hotness += min(4, len(matched_keywords))
        reasons.append("matches hot keywords: " + ", ".join(matched_keywords[:5]))

    if _has_vietnam_impact(article):
        hotness += 1
        reasons.append("Vietnam/logistics impact")

    importance_score = max(1, min(10, int(score)))
    hotness_score = max(1, min(10, int(hotness)))
    return {
        "importance_score": importance_score,
        "hotness_score": hotness_score,
        "hot_keywords": matched_keywords,
        "why_hot": "; ".join(reasons) if reasons else "source quality and maritime relevance",
    }


def parse_hot_keywords(value):
    if not value:
        return []
    if isinstance(value, list):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return []


def _recency_bonus(published_at):
    parsed = _parse_datetime(published_at)
    if not parsed:
        return 0, ""
    age_hours = (datetime.now(timezone.utc) - parsed).total_seconds() / 3600
    if age_hours <= 24:
        return 3, "published in last 24h"
    if age_hours <= 48:
        return 2, "published in last 48h"
    if age_hours <= 168:
        return 1, "published in last 7d"
    return -3, "older than weekly window"


def _matched_trend_keywords(article, trends):
    text = " ".join(
        str(article.get(key) or "")
        for key in ["title", "description", "content_excerpt", "category", "source_name"]
    ).lower()
    matched = []
    for trend in trends:
        if trend.get("category") == "google_trend":
            continue
        keyword = (trend.get("keyword") or "").strip()
        if keyword and keyword.lower() in text:
            matched.append(keyword)
    return matched[:8]


def _has_vietnam_impact(article):
    text = " ".join(
        str(article.get(key) or "")
        for key in ["title", "description", "content_excerpt", "source_name"]
    ).lower()
    return any(
        keyword in text
        for keyword in [
            "vietnam",
            "viet nam",
            "cai mep",
            "hai phong",
            "saigon",
            "cang",
            "xuat nhap khau",
        ]
    )


def _looks_like_menu_or_category(article):
    title = (article.get("title") or "").strip().lower()
    url = (article.get("url") or "").strip().lower()
    weak_titles = {
        "hoạt động kinh doanh",
        "tin tức chuyên ngành",
        "tin kinh tế – xã hội",
        "tin kinh tế - xã hội",
        "tin tức cho nhà đầu tư",
        "công bố thông tin hoạt động",
        "phát triển bền vững",
    }
    if title in weak_titles:
        return True
    return any(marker in url for marker in ["/chuyen-muc/", "/category/", "/tag/"])


def _parse_datetime(value):
    if not value:
        return None
    text = str(value).strip()
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None
