import html
import re
from dataclasses import dataclass

from app.services.storage import get_article_for_summary_by_id, get_articles_for_summary, upsert_summary


EVERNOTE_SUMMARIZER_URL = "https://evernote.com/ai-rewrite/summarize-news-story-with-ai"
PROMPT_VERSION = "evernote-web-v1"
MODEL_NAME = "evernote-web"
MIN_TEXT_LENGTH = 300
MAX_TEXT_LENGTH = 4500
DEFAULT_TIMEOUT_MS = 60000


class EvernoteSummarizerError(RuntimeError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class EvernoteSummaryResult:
    ok: bool
    status: str
    article_id: int | None
    message: str
    summary: dict | None = None
    prompt: str | None = None


def summarize_article_id_with_evernote(article_id, db_path=None, dry_run=False, save=True, timeout_ms=DEFAULT_TIMEOUT_MS):
    article = get_article_for_summary_by_id(article_id, db_path=db_path) if db_path else get_article_for_summary_by_id(article_id)
    if not article:
        return EvernoteSummaryResult(False, "not_found", article_id, f"Article not found: {article_id}")
    return summarize_article_with_evernote(article, db_path=db_path, dry_run=dry_run, save=save, timeout_ms=timeout_ms)


def summarize_candidates_with_evernote(min_score=8, limit=3, db_path=None, dry_run=False, save=True, timeout_ms=DEFAULT_TIMEOUT_MS):
    articles = (
        get_articles_for_summary(db_path=db_path, min_score=min_score, limit=limit)
        if db_path
        else get_articles_for_summary(min_score=min_score, limit=limit)
    )
    return [
        summarize_article_with_evernote(article, db_path=db_path, dry_run=dry_run, save=save, timeout_ms=timeout_ms)
        for article in articles
    ]


def summarize_article_with_evernote(article, db_path=None, dry_run=False, save=True, timeout_ms=DEFAULT_TIMEOUT_MS):
    article_text = clean_article_text(article)
    article_id = article.get("id")
    if len(article_text) < MIN_TEXT_LENGTH:
        return EvernoteSummaryResult(
            False,
            "text_too_short",
            article_id,
            f"Article text has {len(article_text)} characters; minimum is {MIN_TEXT_LENGTH}",
        )

    prompt = build_evernote_prompt(article, article_text)
    if dry_run:
        return EvernoteSummaryResult(True, "dry_run", article_id, "Prompt generated without calling Evernote", prompt=prompt)

    try:
        raw_summary = request_evernote_summary(prompt, timeout_ms=timeout_ms)
    except EvernoteSummarizerError as exc:
        return EvernoteSummaryResult(False, exc.status, article_id, exc.message)

    summary = normalize_evernote_output(raw_summary, article)
    if not summary["summary"]:
        return EvernoteSummaryResult(False, "empty_output", article_id, "Evernote returned empty summary")

    if save:
        upsert_summary(summary, db_path=db_path) if db_path else upsert_summary(summary)

    return EvernoteSummaryResult(True, "ok", article_id, "Evernote summary imported", summary=summary)


def clean_article_text(article, max_length=MAX_TEXT_LENGTH):
    raw = article.get("article_text") or article.get("description") or article.get("content_excerpt") or ""
    text = re.sub(r"<(script|style|nav|footer|header)[^>]*>.*?</\1>", " ", raw, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_length].strip()


def build_evernote_prompt(article, article_text):
    return "\n".join(
        [
            "Tóm tắt tin hàng hải sau bằng tiếng Việt.",
            "",
            "Yêu cầu:",
            "- 2-4 câu ngắn.",
            "- Nêu rõ sự kiện chính.",
            "- Không bịa thêm thông tin.",
            "- Không copy nguyên văn bài báo.",
            "- Cuối cùng thêm 1 câu tác động tới shipping/port/logistics nếu có.",
            "",
            f"Nguồn: {article.get('source_name') or ''}",
            f"Link gốc: {article.get('url') or article.get('original_url') or ''}",
            f"Tiêu đề: {article.get('title') or ''}",
            "",
            "Nội dung:",
            article_text,
        ]
    )


def normalize_evernote_output(raw_summary, article):
    summary_text = _bounded_text(raw_summary, 900)
    return {
        "article_id": article["id"],
        "headline": _bounded_text(article.get("title") or "", 180),
        "summary": summary_text,
        "impact_note": "",
        "category": article.get("category"),
        "importance_score": article.get("importance_score"),
        "source_name": article.get("source_name"),
        "original_url": article.get("url"),
        "prompt_version": PROMPT_VERSION,
        "model_name": MODEL_NAME,
        "token_usage": 0,
    }


def request_evernote_summary(prompt, timeout_ms=DEFAULT_TIMEOUT_MS):
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise EvernoteSummarizerError(
            "playwright_missing",
            "Playwright is not installed. Install it and run `python -m playwright install chromium`.",
        ) from exc

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(EVERNOTE_SUMMARIZER_URL, wait_until="domcontentloaded", timeout=timeout_ms)
                page.wait_for_timeout(5000)
                if _page_requires_login_or_challenge(page):
                    raise EvernoteSummarizerError("login_required", "Evernote page requires login or challenge")

                input_box = _find_input_box(page)
                if input_box is None:
                    raise EvernoteSummarizerError("ui_changed", "Could not find Evernote text input")

                input_box.fill(prompt, timeout=timeout_ms)
                button = _find_submit_button(page)
                if button is None:
                    raise EvernoteSummarizerError("ui_changed", "Could not find Evernote summarize button")

                button.click(timeout=timeout_ms)
                output = _wait_for_output(page, timeout_ms=timeout_ms)
                if not output:
                    raise EvernoteSummarizerError("empty_output", "Evernote output was empty")
                return output
            finally:
                browser.close()
    except PlaywrightTimeoutError as exc:
        raise EvernoteSummarizerError("timeout", "Evernote summarizer timed out") from exc


def _find_input_box(page):
    selectors = [
        "textarea",
        "[contenteditable='true']",
        "div[role='textbox']",
        "input[type='text']",
    ]
    for selector in selectors:
        locator = page.locator(selector)
        try:
            count = locator.count()
        except Exception:
            continue
        for index in range(count):
            candidate = locator.nth(index)
            try:
                if candidate.is_visible(timeout=1500) and candidate.is_enabled(timeout=1500):
                    return candidate
            except Exception:
                continue
    return None


def _find_submit_button(page):
    candidates = [
        page.get_by_role("button", name=re.compile("^summarize text$", re.IGNORECASE)),
        page.locator("button").filter(has_text=re.compile("^summarize text$", re.IGNORECASE)),
        page.locator(".main-cta").filter(has_text=re.compile("summarize", re.IGNORECASE)),
        page.get_by_role("button", name=re.compile("generate|create", re.IGNORECASE)),
        page.locator("button").filter(has_text=re.compile("summarize|generate|create", re.IGNORECASE)),
    ]
    for locator in candidates:
        try:
            first = locator.first
            if first.count() and first.is_visible(timeout=1500):
                return first
        except Exception:
            continue
    return None


def _wait_for_output(page, timeout_ms=DEFAULT_TIMEOUT_MS):
    output_selectors = [
        "[data-testid*='output']",
        "[class*='output']",
        "[class*='result']",
        "[aria-live]",
        "main",
    ]
    page.wait_for_timeout(3000)
    try:
        page.wait_for_function(
            "() => !document.body.innerText.toLowerCase().includes('converting your text')",
            timeout=min(timeout_ms, 45000),
        )
    except Exception:
        raise EvernoteSummarizerError("converting_timeout", "Evernote stayed on 'Converting your text' and did not return a summary")
    for selector in output_selectors:
        try:
            text = page.locator(selector).last.inner_text(timeout=min(timeout_ms, 10000))
        except Exception:
            continue
        cleaned = _extract_summary_like_text(text)
        if cleaned:
            return cleaned
    return ""


def _extract_summary_like_text(text):
    cleaned = _bounded_text(text, 1600)
    if not cleaned:
        return ""
    lowered = cleaned.lower()
    blocked = [
        "about us",
        "converting your text",
        "terms of service",
        "privacy policy",
        "trusted by millions",
        "frequently asked questions",
        "unlimited access to all our tools",
    ]
    if any(marker in lowered for marker in blocked):
        return ""
    return cleaned


def _page_requires_login_or_challenge(page):
    try:
        text = page.locator("body").inner_text(timeout=5000).lower()
    except Exception:
        return False
    return any(marker in text for marker in ["captcha", "sign in to continue", "login required"])


def _bounded_text(value, max_length):
    return re.sub(r"\s+", " ", str(value or "")).strip()[:max_length].strip()
