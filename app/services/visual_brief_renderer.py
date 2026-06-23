import base64
import hashlib
import html
import json
import re
import webbrowser
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import requests

from app.config import ROOT_DIR
from app.services.combined_brief_source import normalize_source_url
from app.services.rss_collector import REQUEST_TIMEOUT, USER_AGENT


CARD_WIDTH = 1080
CARD_HEIGHT = 1350
MAX_IMAGE_BYTES = 10 * 1024 * 1024
DEFAULT_BRIEF_DIR = ROOT_DIR / "output" / "briefs"
DEFAULT_VISUAL_BRIEF_DIR = ROOT_DIR / "output" / "visual_briefs"
DEFAULT_IMAGE_CACHE_DIR = ROOT_DIR / "temp" / "visual_assets"
BRIEF_TYPES = {"morning", "evening", "weekly", "combined", "sheet", "app"}

META_PATTERN = re.compile(r"<meta\b(?P<attrs>[^>]*)>", re.IGNORECASE)
IMG_PATTERN = re.compile(r"<img\b(?P<attrs>[^>]*)>", re.IGNORECASE)
ATTR_PATTERN = re.compile(r"(?P<name>[\w:-]+)\s*=\s*(['\"])(?P<value>.*?)\2", re.IGNORECASE)
IMAGE_URL_PATTERN = re.compile(
    r"https?://[^\s\"'<>]+?\.(?:jpe?g|png|webp|gif)(?:\?[^\s\"'<>]*)?",
    re.IGNORECASE,
)
IMAGE_SRC_ATTRS = ("src", "data-src", "data-lazy-src")


def generate_image_cards(
    brief_type,
    limit=12,
    output_dir=None,
    force_refresh_images=False,
    source_brief_path=None,
    session=None,
    open_preview=False,
    style_settings=None,
):
    if brief_type not in BRIEF_TYPES:
        raise ValueError(f"Unsupported brief type: {brief_type}")

    source_path = Path(source_brief_path) if source_brief_path else DEFAULT_BRIEF_DIR / f"{brief_type}_brief.json"
    payload = load_brief_payload(source_path)
    all_items = payload.get("items", [])
    items = all_items if limit is None else all_items[: max(1, int(limit or 12))]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = Path(output_dir or DEFAULT_VISUAL_BRIEF_DIR / brief_type / timestamp)
    run_dir.mkdir(parents=True, exist_ok=True)

    session = session or requests.Session()
    cards = []
    rendered_paths = []

    for index, item in enumerate(items, start=1):
        original_url = normalize_source_url(item.get("original_url"))
        item = dict(item)
        item["original_url"] = original_url
        image = resolve_card_image(
            original_url,
            session=session,
            cache_dir=DEFAULT_IMAGE_CACHE_DIR,
            force_refresh=force_refresh_images,
        )
        card_path = run_dir / f"card_{index:02d}.png"
        card_html = render_card_html(payload, item, index, image, style_settings=style_settings)
        render_html_to_png(card_html, card_path)
        rendered_paths.append(card_path)
        cards.append(
            {
                "index": index,
                "title": item.get("title"),
                "source_name": item.get("source_name"),
                "original_url": item.get("original_url"),
                "published_at": item.get("published_at"),
                "source_type": item.get("source_type"),
                "canonical_url": item.get("canonical_url"),
                "title_hash": item.get("title_hash"),
                "item_key": item.get("item_key"),
                "image_status": image["status"],
                "image_url": image.get("image_url"),
                "image_reason": image.get("reason"),
                "card_path": str(card_path),
            }
        )

    preview_path = write_preview_html(run_dir, payload, rendered_paths)
    manifest_path = write_manifest(run_dir, payload, source_path, cards, preview_path)
    if open_preview:
        webbrowser.open(preview_path.resolve().as_uri())

    return {
        "brief_type": brief_type,
        "items": len(cards),
        "output_dir": run_dir,
        "manifest_path": manifest_path,
        "preview_path": preview_path,
        "cards": cards,
    }


def load_brief_payload(path):
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Brief JSON not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_card_image(article_url, session=None, cache_dir=DEFAULT_IMAGE_CACHE_DIR, force_refresh=False):
    article_url = normalize_source_url(article_url)
    if not article_url:
        return _fallback_image("missing_article_url")

    session = session or requests.Session()
    try:
        response = session.get(article_url, timeout=REQUEST_TIMEOUT, headers=_html_headers())
        response.raise_for_status()
    except Exception as exc:
        return _fallback_image(f"article_fetch_error: {exc}")

    image_urls = extract_image_candidates(response.text, article_url)
    if not image_urls:
        return _fallback_image("image_not_found")

    last_error = None
    last_image_url = None
    for image_url in image_urls:
        last_image_url = image_url
        try:
            local_path, mime_type = download_image(
                image_url,
                session=session,
                cache_dir=cache_dir,
                force_refresh=force_refresh,
            )
        except Exception as exc:
            last_error = exc
            continue

        return {
            "status": "ok",
            "image_url": image_url,
            "local_path": local_path,
            "mime_type": mime_type,
            "data_uri": image_to_data_uri(local_path, mime_type),
        }

    return _fallback_image(f"image_fetch_error: {last_error}", image_url=last_image_url)


def extract_image_url(html_text, base_url):
    candidates = extract_image_candidates(html_text, base_url)
    return candidates[0] if candidates else None


def extract_image_candidates(html_text, base_url):
    raw_candidates = []
    og_candidates = []
    twitter_candidates = []
    text = html_text or ""

    for match in META_PATTERN.finditer(text):
        attrs = _attrs_to_dict(match.group("attrs"))
        name = (attrs.get("property") or attrs.get("name") or "").lower()
        content = attrs.get("content")
        if name in {"og:image", "og:image:secure_url"} and content:
            og_candidates.append(content)
        elif name == "twitter:image" and content:
            twitter_candidates.append(content)
    raw_candidates.extend(og_candidates + twitter_candidates)

    for match in IMG_PATTERN.finditer(text):
        attrs = _attrs_to_dict(match.group("attrs"))
        if _is_small_declared_image(attrs):
            continue
        for attr_name in IMAGE_SRC_ATTRS:
            if attrs.get(attr_name):
                raw_candidates.append(attrs[attr_name])
        if attrs.get("srcset"):
            raw_candidates.extend(_srcset_candidates(attrs["srcset"]))

    raw_candidates.extend(match.group(0) for match in IMAGE_URL_PATTERN.finditer(text))

    candidates = []
    seen = set()
    for candidate in raw_candidates:
        normalized = html.unescape(candidate or "").strip()
        if not _usable_image_candidate(normalized):
            continue
        absolute = urljoin(base_url, normalized)
        if absolute not in seen:
            candidates.append(absolute)
            seen.add(absolute)
    return candidates


def download_image(image_url, session=None, cache_dir=DEFAULT_IMAGE_CACHE_DIR, force_refresh=False):
    session = session or requests.Session()
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_key = hashlib.sha256(image_url.encode("utf-8")).hexdigest()
    if not force_refresh:
        cached = next(cache_dir.glob(f"{cache_key}.*"), None)
        if cached:
            return cached, _mime_for_path(cached)

    response = session.get(image_url, timeout=REQUEST_TIMEOUT, headers=_image_headers(), stream=True)
    response.raise_for_status()
    content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if not content_type.startswith("image/"):
        raise ValueError(f"URL did not return an image: {content_type or 'unknown'}")

    extension = _extension_for_mime(content_type)
    image_path = cache_dir / f"{cache_key}{extension}"
    if image_path.exists() and not force_refresh:
        return image_path, content_type

    chunks = []
    total = 0
    for chunk in response.iter_content(chunk_size=65536):
        if not chunk:
            continue
        total += len(chunk)
        if total > MAX_IMAGE_BYTES:
            raise ValueError("Image is larger than 10 MB")
        chunks.append(chunk)

    image_path.write_bytes(b"".join(chunks))
    return image_path, content_type


def render_card_html(payload, item, index, image, style_settings=None):
    style = _normalize_style(style_settings)
    title = _text(item.get("title"))
    summary = _text(item.get("summary"))
    impact_note = _text(item.get("impact_note"))
    hot_keywords = ", ".join(item.get("hot_keywords") or [])
    source_name = _text(item.get("source_name"))
    original_url = item.get("original_url") or ""
    domain = urlparse(original_url).netloc or original_url
    hero = _hero_markup(image, style)
    title_markup = f"<h1>{_escape(title)}</h1>" if style["show_title"] else ""
    summary_markup = f'<div class="summary">{_escape(summary)}</div>' if style["show_summary"] else ""
    impact_markup = f'<div class="impact">{_escape(impact_note)}</div>' if style["show_impact"] else ""
    hot_markup = f'<div class="hotwords">{_escape(hot_keywords)}</div>' if style["show_hot_keywords"] and hot_keywords else ""
    source_markup = f'<span class="source">{_escape(source_name)}</span>' if style["show_source"] else ""
    url_markup = f'<div class="url">{_escape(domain)}</div>' if style["show_url"] else ""

    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
:root {{
  --hero-h: 610px;
  --body-pad-y: 44px;
  --body-pad-x: 48px;
  --body-gap: 24px;
  --title-size: {style["title_size"]}px;
  --summary-size: {style["summary_size"]}px;
  --impact-size: {style["impact_size"]}px;
  --footer-size: 24px;
  --text-color: {style["text_color"]};
  --accent-color: {style["accent_color"]};
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  width: {CARD_WIDTH}px;
  height: {CARD_HEIGHT}px;
  font-family: {style["font_family"]}, Arial, Helvetica, sans-serif;
  color: var(--text-color);
  background: #eef3f7;
}}
.card {{
  width: {CARD_WIDTH}px;
  height: {CARD_HEIGHT}px;
  overflow: hidden;
  background: #f8fbfd;
  border: 1px solid #d2dce7;
}}
.hero {{
  height: var(--hero-h);
  position: relative;
  overflow: hidden;
  background: #12364d;
}}
.hero img {{
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}}
.fallback {{
  height: 100%;
  padding: 54px;
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
  background:
    linear-gradient(135deg, rgba(17, 94, 89, .94), rgba(15, 42, 62, .96)),
    repeating-linear-gradient(120deg, rgba(255,255,255,.08) 0 2px, transparent 2px 28px);
  color: white;
}}
.fallback-title {{ font-size: 58px; line-height: 1.03; font-weight: 800; max-width: 770px; }}
.fallback-kicker {{ font-size: 25px; text-transform: uppercase; margin-bottom: 18px; color: #f6c85f; }}
.brand-watermark {{
  position: absolute;
  top: 28px;
  right: 34px;
  max-width: 520px;
  padding: 9px 14px;
  border-radius: 4px;
  background: rgba(8, 23, 36, .30);
  color: rgba(255, 255, 255, .68);
  font-size: 25px;
  font-weight: 800;
  letter-spacing: 0;
  text-align: right;
}}
.body {{
  height: calc({CARD_HEIGHT}px - var(--hero-h));
  padding: var(--body-pad-y) var(--body-pad-x) 30px;
  display: flex;
  flex-direction: column;
  gap: var(--body-gap);
}}
h1 {{
  margin: 0;
  font-size: var(--title-size);
  line-height: 1.08;
  letter-spacing: 0;
  color: var(--text-color);
}}
.summary {{
  font-size: var(--summary-size);
  line-height: 1.28;
  color: #263746;
}}
.impact {{
  border-left: 7px solid var(--accent-color);
  padding-left: 18px;
  font-size: var(--impact-size);
  line-height: 1.28;
  color: #32495a;
  background: #eef8f6;
  padding-top: 10px;
  padding-bottom: 10px;
  padding-right: 12px;
}}
.hotwords {{
  color: var(--accent-color);
  font-size: 23px;
  font-weight: 800;
}}
.footer {{
  margin-top: auto;
  padding-top: 16px;
  border-top: 1px solid #c6d3df;
  display: flex;
  justify-content: space-between;
  gap: 26px;
  color: #415466;
  font-size: var(--footer-size);
  flex-wrap: wrap;
}}
.source {{ font-weight: 800; color: #0f2a3d; }}
.url {{ max-width: 620px; overflow-wrap: anywhere; text-align: right; }}
</style>
</head>
<body>
<main class="card">
  <section class="hero">{hero}</section>
  <section class="body">
    {title_markup}
    {summary_markup}
    {impact_markup}
    {hot_markup}
    <div class="footer">
      <div>{source_markup}</div>
      {url_markup}
    </div>
  </section>
</main>
<script>
function fitCard() {{
  const root = document.documentElement;
  const body = document.querySelector('.body');
  const values = {{
    hero: 610,
    pad: 44,
    gap: 24,
    title: 54,
    summary: 31,
    impact: 27,
    footer: 24
  }};
  const minimums = {{
    hero: 260,
    pad: 22,
    gap: 12,
    title: 32,
    summary: 20,
    impact: 18,
    footer: 16
  }};
  const apply = () => {{
    root.style.setProperty('--hero-h', `${{values.hero}}px`);
    root.style.setProperty('--body-pad-y', `${{values.pad}}px`);
    root.style.setProperty('--body-gap', `${{values.gap}}px`);
    root.style.setProperty('--title-size', `${{values.title}}px`);
    root.style.setProperty('--summary-size', `${{values.summary}}px`);
    root.style.setProperty('--impact-size', `${{values.impact}}px`);
    root.style.setProperty('--footer-size', `${{values.footer}}px`);
  }};
  const overflows = () => body.scrollHeight > body.clientHeight + 1;
  apply();
  for (let step = 0; step < 120 && overflows(); step += 1) {{
    if (values.hero > minimums.hero) values.hero -= 10;
    if (values.title > minimums.title) values.title -= 1;
    if (values.summary > minimums.summary) values.summary -= 1;
    if (values.impact > minimums.impact) values.impact -= 1;
    if (values.footer > minimums.footer && step % 2 === 0) values.footer -= 1;
    if (values.pad > minimums.pad && step % 2 === 0) values.pad -= 1;
    if (values.gap > minimums.gap && step % 3 === 0) values.gap -= 1;
    apply();
  }}
}}
fitCard();
</script>
</body>
</html>"""


def render_html_to_png(card_html, output_path):
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright is not installed. Install it with `pip install playwright`.") from exc

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": CARD_WIDTH, "height": CARD_HEIGHT}, device_scale_factor=1)
            page.set_content(card_html, wait_until="networkidle")
            page.evaluate("fitCard()")
            page.screenshot(path=str(output_path), full_page=False)
            browser.close()
    except PlaywrightError as exc:
        raise RuntimeError(
            "Playwright Chromium is not installed. Run `python -m playwright install chromium`."
        ) from exc


def write_manifest(run_dir, payload, source_path, cards, preview_path):
    manifest = {
        "brief_type": payload.get("brief_type"),
        "title": payload.get("title"),
        "generated_at": datetime.now().replace(microsecond=0).isoformat(),
        "source_brief_json": str(Path(source_path)),
        "preview_path": str(Path(preview_path)),
        "cards": cards,
    }
    path = Path(run_dir) / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_preview_html(run_dir, payload, card_paths):
    title = _escape(payload.get("title") or "Visual Brief")
    images = "\n".join(
        f'<img src="{_escape(path.name)}" alt="Card {index}">'
        for index, path in enumerate(card_paths, start=1)
    )
    preview = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
body {{ margin: 0; padding: 28px; background: #e5e7eb; font-family: Arial, sans-serif; }}
h1 {{ margin: 0 0 24px; color: #102a43; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 22px; }}
img {{ width: 100%; border: 1px solid #cbd5e1; background: white; }}
</style>
</head>
<body>
<h1>{title}</h1>
<div class="grid">{images}</div>
</body>
</html>"""
    path = Path(run_dir) / "preview.html"
    path.write_text(preview, encoding="utf-8")
    return path


def image_to_data_uri(path, mime_type):
    raw = Path(path).read_bytes()
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _hero_markup(image, style=None):
    style = _normalize_style(style)
    watermark_text = _escape(style["watermark"])
    watermark = f'<div class="brand-watermark">{watermark_text}</div>' if watermark_text else ""
    if image.get("status") == "ok":
        return (
            f'<img src="{_escape(image["data_uri"])}" alt="">'
            f"{watermark}"
        )
    return (
        '<div class="fallback">'
        '<div class="fallback-kicker">Maritime Brief</div>'
        '<div class="fallback-title">Shipping, ports and logistics intelligence</div>'
        '</div>'
        f"{watermark}"
    )


def _fallback_image(reason, image_url=None):
    return {"status": "fallback", "reason": reason, "image_url": image_url}


def _normalize_style(style):
    data = dict(style or {})
    return {
        "font_family": _css_font(data.get("font_family") or "Arial"),
        "title_size": _int_range(data.get("title_size"), 32, 72, 54),
        "summary_size": _int_range(data.get("summary_size"), 18, 42, 31),
        "impact_size": _int_range(data.get("impact_size"), 16, 36, 27),
        "text_color": _css_color(data.get("text_color") or "#102033", "#102033"),
        "accent_color": _css_color(data.get("accent_color") or "#0f766e", "#0f766e"),
        "watermark": _text(data.get("watermark") or "Maritime Intelligence Hub"),
        "show_title": bool(data.get("show_title", True)),
        "show_summary": bool(data.get("show_summary", True)),
        "show_impact": bool(data.get("show_impact", True)),
        "show_source": bool(data.get("show_source", True)),
        "show_url": bool(data.get("show_url", True)),
        "show_hot_keywords": bool(data.get("show_hot_keywords", True)),
    }


def _int_range(value, minimum, maximum, default):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def _css_color(value, default):
    text = str(value or "").strip()
    if re.fullmatch(r"#[0-9a-fA-F]{6}", text):
        return text
    return default


def _css_font(value):
    return re.sub(r"[^a-zA-Z0-9 ,_-]", "", str(value or "Arial")).strip() or "Arial"


def _attrs_to_dict(attrs_text):
    attrs = {}
    for match in ATTR_PATTERN.finditer(attrs_text or ""):
        name = match.group("name").lower()
        attrs[name] = html.unescape(match.group("value") or "").strip()
    return attrs


def _srcset_candidates(srcset):
    candidates = []
    for part in str(srcset or "").split(","):
        values = part.strip().split()
        if values:
            candidates.append((values[0], _srcset_score(values[1] if len(values) > 1 else "")))
    candidates.sort(key=lambda item: item[1], reverse=True)
    return [url for url, _score in candidates]


def _srcset_score(descriptor):
    text = str(descriptor or "").strip().lower()
    try:
        if text.endswith("w"):
            return float(text[:-1])
        if text.endswith("x"):
            return float(text[:-1]) * 1000
    except ValueError:
        return 0
    return 0


def _is_small_declared_image(attrs):
    width = _declared_image_size(attrs.get("width"))
    height = _declared_image_size(attrs.get("height"))
    if width is None or height is None:
        return False
    return width < 160 or height < 100


def _declared_image_size(value):
    match = re.search(r"\d+", str(value or ""))
    if not match:
        return None
    return int(match.group(0))


def _usable_image_candidate(value):
    if not value:
        return False
    text = str(value).strip()
    lowered = text.lower()
    if lowered.startswith("data:"):
        return False

    parsed = urlparse(html.unescape(text))
    path = unquote(parsed.path or text).lower()
    filename = Path(path).name
    if filename.endswith(".svg"):
        return False
    if any(marker in path for marker in ("/wp-content/themes/", "/wp-content/plugins/", "/assets/modals/")):
        return False

    stem = Path(filename).stem
    tokens = {token for token in re.split(r"[^a-z0-9]+", stem) if token}
    if any(marker in stem for marker in ("spacer", "tracking", "announcement")):
        return False
    if tokens and tokens <= {"logo", "logos", "avatar", "icon", "site", "brand"}:
        return False
    if ("logo" in tokens or "logos" in tokens) and len(tokens) <= 2:
        return False

    return True


def _extension_for_mime(mime_type):
    return {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }.get(mime_type, ".img")


def _mime_for_path(path):
    suffix = Path(path).suffix.lower()
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(suffix, "image/jpeg")


def _html_headers():
    return {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}


def _image_headers():
    return {"User-Agent": USER_AGENT, "Accept": "image/*,*/*;q=0.8"}


def _format_datetime(value):
    if not value:
        return "Unknown time"
    return str(value).replace("T", " ")[:19]


def _text(value):
    return " ".join(str(value or "").split())


def _escape(value):
    return html.escape(str(value or ""), quote=True)
