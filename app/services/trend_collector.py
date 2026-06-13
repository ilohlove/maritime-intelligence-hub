import csv
import io
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

import requests

from app.services.storage import upsert_trend_keyword


GOOGLE_TRENDS_RSS_URL = "https://trends.google.com/trending/rss?geo=VN"
REQUEST_TIMEOUT = 20

MARITIME_CORE_KEYWORDS = [
    "shipping",
    "port",
    "container",
    "logistics",
    "freight",
    "freight rates",
    "IMO",
    "Red Sea",
    "Suez",
    "Panama Canal",
    "sanction",
    "tariff",
    "collision",
    "fire",
    "crew",
    "emissions",
    "green shipping",
    "LNG",
]

VIETNAM_IMPACT_KEYWORDS = [
    "Cai Mep",
    "Hai Phong",
    "Vietnam port",
    "cang bien",
    "xuat nhap khau",
    "logistics Viet Nam",
    "hai quan",
    "van tai bien",
]

GENERAL_HOT_KEYWORDS = [
    "World Cup",
    "bong da",
    "doi tuyen",
    "lich thi dau",
    "ket qua",
]


@dataclass
class TrendKeyword:
    keyword: str
    category: str
    timeframe: str
    search_volume: int | None = None
    started_at: str | None = None
    status: str | None = None
    source: str = "seed"


def seed_default_trends(db_path=None):
    trends = []
    for keyword in MARITIME_CORE_KEYWORDS:
        trends.append(TrendKeyword(keyword, "maritime_core", "seed", source="curated"))
    for keyword in VIETNAM_IMPACT_KEYWORDS:
        trends.append(TrendKeyword(keyword, "vietnam_impact", "seed", source="curated"))
    for keyword in GENERAL_HOT_KEYWORDS:
        trends.append(TrendKeyword(keyword, "general_hot", "seed", source="curated"))
    return save_trends(trends, db_path=db_path)


def import_trends_csv(path, timeframe="24h", source="google_trends_csv", db_path=None):
    text = Path(path).read_text(encoding="utf-8-sig")
    trends = parse_trends_csv(text, timeframe=timeframe, source=source)
    return save_trends(trends, db_path=db_path)


def fetch_google_trends_rss(timeframe="24h", url=GOOGLE_TRENDS_RSS_URL, db_path=None, session=None):
    session = session or requests.Session()
    response = session.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": "MaritimeIntelligenceHub/1.0"})
    response.raise_for_status()
    trends = parse_trends_rss(response.text, timeframe=timeframe, source="google_trends_rss")
    return save_trends(trends, db_path=db_path)


def parse_trends_csv(text, timeframe="24h", source="google_trends_csv"):
    reader = csv.DictReader(io.StringIO(text))
    trends = []
    for row in reader:
        keyword = _first_value(row, ["Xu hướng", "Trend", "Title", "Search term", "keyword", "Keyword"])
        if not keyword:
            continue
        trends.append(
            TrendKeyword(
                keyword=keyword,
                category=_classify_keyword(keyword),
                timeframe=timeframe,
                search_volume=_parse_int(_first_value(row, ["Lượng tìm kiếm", "Search volume", "Volume"])),
                started_at=_first_value(row, ["Đã bắt đầu", "Started", "Start time"]),
                status=_first_value(row, ["Trạng thái", "Status"]),
                source=source,
            )
        )
    return trends


def parse_trends_rss(xml_text, timeframe="24h", source="google_trends_rss"):
    root = ElementTree.fromstring(xml_text.encode("utf-8"))
    trends = []
    for item in root.findall(".//item"):
        title = _child_text(item, "title")
        if not title:
            continue
        trends.append(
            TrendKeyword(
                keyword=title,
                category=_classify_keyword(title),
                timeframe=timeframe,
                search_volume=_parse_int(_child_text(item, "ht:approx_traffic") or _child_text(item, "approx_traffic")),
                started_at=_child_text(item, "pubDate"),
                status="active",
                source=source,
            )
        )
    return trends


def save_trends(trends, db_path=None):
    count = 0
    for trend in trends:
        upsert_trend_keyword(
            trend.keyword,
            trend.category,
            trend.timeframe,
            trend.source,
            search_volume=trend.search_volume,
            started_at=trend.started_at,
            status=trend.status,
            db_path=db_path,
        )
        count += 1
    return count


def _classify_keyword(keyword):
    lowered = _normalize(keyword)
    folded = _fold_accents(lowered)
    if any(_fold_accents(_normalize(item)) in folded for item in MARITIME_CORE_KEYWORDS):
        return "maritime_core"
    if any(_fold_accents(_normalize(item)) in folded for item in VIETNAM_IMPACT_KEYWORDS):
        return "vietnam_impact"
    if any(_fold_accents(_normalize(item)) in folded for item in GENERAL_HOT_KEYWORDS):
        return "general_hot"
    return "google_trend"


def _first_value(row, names):
    lowered = {key.lower(): value for key, value in row.items() if key}
    for name in names:
        value = lowered.get(name.lower())
        if value:
            return value.strip()
    return ""


def _child_text(item, wanted):
    wanted_local = wanted.rsplit(":", 1)[-1].lower()
    for child in list(item):
        local = child.tag.rsplit("}", 1)[-1].rsplit(":", 1)[-1].lower()
        if local == wanted_local and child.text:
            return child.text.strip()
    return ""


def _parse_int(value):
    if not value:
        return None
    digits = "".join(ch for ch in value if ch.isdigit())
    return int(digits) if digits else None


def _normalize(value):
    return " ".join(str(value or "").lower().split())


def _fold_accents(value):
    return "".join(
        char for char in unicodedata.normalize("NFD", value)
        if unicodedata.category(char) != "Mn"
    )
