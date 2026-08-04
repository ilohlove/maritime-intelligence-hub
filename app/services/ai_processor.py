import json
import logging
import os
import re
import time

import requests
from dotenv import load_dotenv

from app.services.storage import get_articles_for_summary, upsert_summary


logger = logging.getLogger(__name__)


class AIEnrichmentError(RuntimeError):
    """Raised when production enrichment cannot produce a publishable result."""


def sanitize_ai_error(exc):
    """Return a short provider error without credentials or response bodies."""
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code:
        return f"HTTP {status_code}"
    text = str(exc or "AI provider error")
    text = re.sub(r"(?i)([?&](?:key|api[_-]?key|token|access_token)=)[^&\s]+", r"\1***", text)
    text = re.sub(r"(?i)((?:x-goog-api-key|authorization|api[_-]?key)\s*[:=]\s*)[^\s,;]+", r"\1***", text)
    for env_key in ("OPENAI_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY"):
        secret = os.getenv(env_key, "").strip()
        if secret:
            text = text.replace(secret, "***")
    return " ".join(text.split())[:240]


PROMPT_VERSION = "mock-v1"
MODEL_NAME = "rule-based-mock"
OPENAI_PROMPT_VERSION = "openai-v1"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
OPENAI_CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"
GROQ_CHAT_COMPLETIONS_URL = "https://api.groq.com/openai/v1/chat/completions"
OPENROUTER_CHAT_COMPLETIONS_URL = "https://openrouter.ai/api/v1/chat/completions"
GEMINI_PROMPT_VERSION = "gemini-v1"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash-lite"
GEMINI_GENERATE_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
VIETNAMESE_DIACRITICS = set("ăâđêôơưáàảãạắằẳẵặấầẩẫậéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ")
UNACCENTED_VIETNAMESE_MARKERS = [
    "tom tat",
    "tac dong",
    "hang hai",
    "nguon",
    "duoc",
    "khong",
    "can theo doi",
    "xuat ban",
    "chuoi cung ung",
]


def summarize_pending_articles(db_path=None, min_score=6, provider=None, force=False, limit=None):
    articles = (
        get_articles_for_summary(db_path=db_path, min_score=min_score, force=force, limit=limit)
        if db_path
        else get_articles_for_summary(min_score=min_score, force=force, limit=limit)
    )
    provider = provider or get_ai_provider()
    request_delay = _env_float("AI_REQUEST_DELAY_SECONDS", 0)
    summaries = []
    for index, article in enumerate(articles):
        if index and request_delay > 0:
            time.sleep(request_delay)
        summary = provider.summarize(article)
        upsert_summary(summary, db_path=db_path) if db_path else upsert_summary(summary)
        summaries.append(summary)
    return summaries


def get_ai_provider(allow_mock=True):
    load_dotenv(encoding="utf-8-sig")
    provider_name = os.getenv("AI_PROVIDER", "mock").strip().lower()
    openai_api_key = os.getenv("OPENAI_API_KEY", "").strip()
    gemini_api_key = os.getenv("GEMINI_API_KEY", "").strip()
    groq_api_key = os.getenv("GROQ_API_KEY", "").strip()
    openrouter_api_key = os.getenv("OPENROUTER_API_KEY", "").strip()

    if provider_name == "openai" and openai_api_key:
        return OpenAIChatProvider(
            api_key=openai_api_key,
            model=os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL).strip() or DEFAULT_OPENAI_MODEL,
        )
    if provider_name == "gemini" and gemini_api_key:
        return GeminiChatProvider(
            api_key=gemini_api_key,
            model=os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip() or DEFAULT_GEMINI_MODEL,
        )
    if provider_name in {"chain", "auto"}:
        providers = []
        if gemini_api_key:
            providers.append(GeminiChatProvider(api_key=gemini_api_key, model=os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip() or DEFAULT_GEMINI_MODEL))
        if groq_api_key:
            providers.append(OpenAICompatibleProvider(groq_api_key, os.getenv("GROQ_MODEL", "llama-3.1-8b-instant"), GROQ_CHAT_COMPLETIONS_URL, "groq"))
        if openrouter_api_key:
            providers.append(OpenAICompatibleProvider(openrouter_api_key, os.getenv("OPENROUTER_MODEL", "openrouter/free"), OPENROUTER_CHAT_COMPLETIONS_URL, "openrouter"))
        if providers:
            return AIProviderChain(providers)
    if allow_mock:
        return MockAIProvider()
    raise AIEnrichmentError("No production AI provider is configured")


class MockAIProvider:
    model_name = MODEL_NAME
    prompt_version = PROMPT_VERSION

    def summarize(self, article):
        return build_mock_summary(article)


class OpenAIChatProvider:
    def __init__(self, api_key, model=DEFAULT_OPENAI_MODEL, endpoint=OPENAI_CHAT_COMPLETIONS_URL):
        self.api_key = api_key
        self.model_name = model
        self.endpoint = endpoint
        self.prompt_version = OPENAI_PROMPT_VERSION

    def summarize(self, article):
        return _summarize_with_retry(self, article, "OpenAI")

    def _request_summary(self, article):
        response = requests.post(
            self.endpoint,
            timeout=30,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model_name,
                "temperature": 0.2,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You produce concise Vietnamese maritime intelligence summaries with full Vietnamese diacritics. "
                            "Never return unaccented Vietnamese. "
                            "Return only valid JSON."
                        ),
                    },
                    {
                        "role": "user",
                        "content": build_ai_prompt(article),
                    },
                ],
            },
        )
        response.raise_for_status()
        body = response.json()
        content = body["choices"][0]["message"]["content"]
        payload = json.loads(_strip_json_fence(content))
        payload["token_usage"] = int((body.get("usage") or {}).get("total_tokens") or 0)
        return payload


class OpenAICompatibleProvider:
    def __init__(self, api_key, model, endpoint, provider_name):
        self.api_key = api_key
        self.model_name = model
        self.endpoint = endpoint
        self.provider_name = provider_name
        self.prompt_version = f"{provider_name}-v1"

    def summarize(self, article):
        return _summarize_with_retry(self, article, self.provider_name.title())

    def _request_summary(self, article):
        response = requests.post(
            self.endpoint,
            timeout=30,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json={
                "model": self.model_name,
                "temperature": 0.2,
                "messages": [
                    {"role": "system", "content": "Return only valid JSON Vietnamese maritime summary with full diacritics."},
                    {"role": "user", "content": build_ai_prompt(article)},
                ],
            },
        )
        response.raise_for_status()
        body = response.json()
        content = body["choices"][0]["message"]["content"]
        payload = json.loads(_strip_json_fence(content))
        payload["token_usage"] = int((body.get("usage") or {}).get("total_tokens") or 0)
        return payload


class AIProviderChain:
    def __init__(self, providers):
        self.providers = providers
        self.model_name = " -> ".join(provider.model_name for provider in providers)
        self.prompt_version = "provider-chain-v1"

    def summarize(self, article, allow_mock=True):
        errors = []
        for provider in self.providers:
            try:
                payload = _request_provider_with_retry(provider, article)
                validate_ai_provider_payload(payload)
                normalized = normalize_ai_payload(
                    payload,
                    article,
                    provider.prompt_version,
                    provider.model_name,
                    allow_fallback=False,
                )
                normalized["ai_provider"] = getattr(provider, "provider_name", provider.__class__.__name__)
                normalized["fallback_errors"] = errors
                return normalized
            except Exception as exc:
                provider_name = getattr(provider, "provider_name", provider.__class__.__name__)
                errors.append(f"{provider_name}: {sanitize_ai_error(exc)}")
                logger.warning("AI provider failed; trying next provider: %s", errors[-1])
        if allow_mock:
            fallback = build_mock_summary(article)
            fallback["fallback_errors"] = errors
            fallback["ai_provider"] = "mock"
            return fallback
        raise AIEnrichmentError("All configured AI providers failed: " + "; ".join(errors))


def validate_ai_provider_payload(payload):
    if not isinstance(payload, dict):
        raise ValueError("AI response must be a JSON object")
    for key in ("headline", "summary", "impact_note"):
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"AI response is missing a valid {key}")
    category = payload.get("category")
    if not isinstance(category, str) or not category.strip():
        raise ValueError("AI response is missing a valid category")
    score = payload.get("importance_score")
    try:
        numeric_score = int(score)
    except (TypeError, ValueError) as exc:
        raise ValueError("AI importance_score must be an integer") from exc
    if not 1 <= numeric_score <= 10:
        raise ValueError("AI importance_score must be from 1 to 10")
    for key in ("headline", "summary", "impact_note"):
        if _looks_unaccented_vietnamese(payload.get(key)):
            raise ValueError(f"AI response contains unaccented Vietnamese in {key}")
    return payload


class GeminiChatProvider:
    def __init__(self, api_key, model=DEFAULT_GEMINI_MODEL, endpoint_template=GEMINI_GENERATE_URL):
        self.api_key = api_key
        self.model_name = model
        self.endpoint_template = endpoint_template
        self.prompt_version = GEMINI_PROMPT_VERSION

    def summarize(self, article):
        return _summarize_with_retry(self, article, "Gemini")

    def _request_summary(self, article):
        response = requests.post(
            self.endpoint_template.format(model=self.model_name),
            timeout=30,
            headers={"Content-Type": "application/json", "x-goog-api-key": self.api_key},
            json={
                "contents": [
                    {
                        "role": "user",
                        "parts": [
                            {
                                "text": (
                                    "You are a Vietnamese maritime intelligence editor. "
                                    "Write all Vietnamese output with full Vietnamese diacritics. "
                                    "Never return unaccented Vietnamese. "
                                    "Return only valid JSON.\n\n"
                                    + build_ai_prompt(article)
                                )
                            }
                        ],
                    }
                ],
                "generationConfig": {
                    "temperature": 0.2,
                    "responseMimeType": "application/json",
                },
            },
        )
        response.raise_for_status()
        body = response.json()
        content = _gemini_text(body)
        payload = json.loads(_strip_json_fence(content))
        payload["token_usage"] = int((body.get("usageMetadata") or {}).get("totalTokenCount") or 0)
        return payload


def build_ai_prompt(article):
    source_name = article.get("source_name") or "Unknown source"
    original_url = article.get("url") or ""
    title = article.get("title") or ""
    category = article.get("category") or "Maritime"
    score = article.get("importance_score") or ""
    text = (article.get("description") or article.get("content_excerpt") or "")[:1200]

    return json.dumps(
        {
            "instructions": [
                "Summarize only facts present in the input.",
                "Do not rewrite or reproduce the full article.",
                "Translate and write headline, summary, and impact_note in Vietnamese with full diacritics.",
                "Never output unaccented Vietnamese such as 'Tom tat', 'Tac dong', or 'hang hai'.",
                "Keep the Vietnamese summary to 2-4 sentences.",
                "Explain why the item matters to ship operators, ports, logistics, trade, safety, insurance, or supply chains.",
                "Classify topic at article level; do not copy a generic source category when the article topic differs.",
                "Keep source attribution and original URL.",
            ],
            "required_json_keys": [
                "headline",
                "summary",
                "impact_note",
                "category",
                "importance_score",
                "commercial_relevance",
                "operational_impact",
                "vietnam_relevance",
                "source_name",
                "original_url",
                "image_prompt",
            ],
            "output_language": "Vietnamese with full diacritics",
            "article": {
                "source_name": source_name,
                "original_url": original_url,
                "title": title,
                "category": category,
                "importance_score": score,
                "excerpt": text,
            },
        },
        ensure_ascii=False,
    )


def normalize_ai_payload(payload, article, prompt_version, model_name, allow_fallback=True):
    fallback = build_mock_summary(article)
    headline = _bounded_text(payload.get("headline"), 180)
    summary = _bounded_text(payload.get("summary"), 900)
    impact_note = _bounded_text(payload.get("impact_note"), 600)
    if allow_fallback:
        headline = headline or fallback["headline"]
        summary = summary or fallback["summary"]
        impact_note = impact_note or fallback["impact_note"]
    elif not headline or not summary or not impact_note:
        raise ValueError("AI response is missing required Vietnamese fields")
    image_prompt = _bounded_text(payload.get("image_prompt") or payload.get("visual_hint") or "", 400)
    if allow_fallback and _looks_unaccented_vietnamese(summary):
        summary = fallback["summary"]
    if allow_fallback and _looks_unaccented_vietnamese(impact_note):
        impact_note = fallback["impact_note"]
    if allow_fallback and _looks_unaccented_vietnamese(headline):
        headline = fallback["headline"]
    if not allow_fallback and any(_looks_unaccented_vietnamese(value) for value in (headline, summary, impact_note)):
        raise ValueError("AI response contains unaccented Vietnamese")
    return {
        "article_id": article["id"],
        "headline": headline,
        "summary": summary,
        "impact_note": impact_note,
        "category": payload.get("category") or article.get("category"),
        "importance_score": payload.get("importance_score") or article.get("importance_score"),
        "commercial_relevance": _bounded_int(payload.get("commercial_relevance"), 0, 4),
        "operational_impact": _bounded_int(payload.get("operational_impact"), 0, 3),
        "vietnam_relevance": _bounded_int(payload.get("vietnam_relevance"), 0, 2),
        # Attribution is collector-owned; never accept model mutations.
        "source_name": article.get("source_name"),
        "original_url": article.get("url"),
        "prompt_version": prompt_version,
        "model_name": model_name,
        "token_usage": int(payload.get("token_usage") or 0),
        "image_prompt": image_prompt,
    }


def build_mock_summary(article):
    source_name = _repair_encoding(article.get("source_name") or "nguồn tin")
    category = _repair_encoding(article.get("category") or "hàng hải")
    title = _repair_encoding(article.get("title") or "bản tin hàng hải")
    description = _repair_encoding(article.get("description") or article.get("content_excerpt") or "")

    short_context = description[:220].strip()
    if short_context:
        summary = (
            f"{source_name} đưa tin về diễn biến liên quan đến {category}. "
            f"Điểm chính: {short_context}. "
            "Tin này cần được ưu tiên theo dõi nếu có ảnh hưởng đến khai thác tàu, cảng, logistics hoặc chi phí vận tải."
        )
    else:
        summary = (
            f"{source_name} ghi nhận một cập nhật mới trong nhóm {category}. "
            "Hệ thống chưa có đủ trích đoạn để tóm tắt sâu hơn, nên biên tập viên cần kiểm tra nguồn gốc trước khi xuất bản."
        )

    impact = (
        f"Tác động hàng hải: cần theo dõi ảnh hưởng của tin này tới {category}, "
        "đặc biệt là lịch tàu, năng lực cảng, giá cước, tuân thủ quy định và chuỗi cung ứng Việt Nam nếu có liên quan."
    )

    return {
        "article_id": article["id"],
        "headline": _mock_headline(source_name, category, title),
        "summary": summary,
        "impact_note": impact,
        "category": category,
        "importance_score": article.get("importance_score"),
        "source_name": source_name,
        "original_url": article.get("url"),
        "prompt_version": PROMPT_VERSION,
        "model_name": MODEL_NAME,
        "token_usage": 0,
    }


def _repair_encoding(value):
    markers = ["Ã", "Â", "â€", "Ä", "áº", "á»"]
    if not value or not any(marker in value for marker in markers):
        return value

    for source_encoding in ["cp1252", "latin1"]:
        try:
            repaired = str(value).encode(source_encoding).decode("utf-8")
        except UnicodeError:
            continue
        if repaired != value:
            return repaired
    return value


def _strip_json_fence(value):
    text = (value or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    return text.strip()


def _gemini_text(body):
    candidates = body.get("candidates") or []
    if not candidates:
        raise ValueError("Gemini response has no candidates")
    parts = ((candidates[0].get("content") or {}).get("parts") or [])
    texts = [part.get("text", "") for part in parts if part.get("text")]
    if not texts:
        raise ValueError("Gemini response has no text")
    return "\n".join(texts)


def _bounded_text(value, max_length):
    text = " ".join(str(value or "").split())
    return text[:max_length].strip()


def _bounded_int(value, minimum, maximum):
    try:
        return max(minimum, min(maximum, int(value)))
    except (TypeError, ValueError):
        return None


def _summarize_with_retry(provider, article, provider_label):
    attempts = max(1, _env_int("AI_RETRY_ATTEMPTS", 2))
    base_delay = max(0.5, _env_float("AI_RETRY_BASE_DELAY_SECONDS", 4))
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            payload = provider._request_summary(article)
            return normalize_ai_payload(
                payload,
                article,
                prompt_version=provider.prompt_version,
                model_name=provider.model_name,
            )
        except Exception as exc:
            last_error = exc
            if attempt < attempts and _should_retry_ai_error(exc):
                sleep_seconds = base_delay * attempt
                logger.warning(
                    "%s summary attempt %s/%s failed; retrying in %.1fs: %s",
                    provider_label,
                    attempt,
                    attempts,
                    sleep_seconds,
                    sanitize_ai_error(exc),
                )
                time.sleep(sleep_seconds)
                continue
            break

    logger.warning("%s summary failed; falling back to mock: %s", provider_label, sanitize_ai_error(last_error))
    return build_mock_summary(article)


def _request_provider_with_retry(provider, article):
    """Request raw JSON with bounded retries; never returns a mock payload."""
    attempts = max(1, _env_int("AI_CHAIN_RETRY_ATTEMPTS", 3))
    base_delay = max(0.5, _env_float("AI_RETRY_BASE_DELAY_SECONDS", 2))
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            return provider._request_summary(article)
        except Exception as exc:
            last_error = exc
            if attempt >= attempts or not _should_retry_ai_error(exc):
                break
            delay = base_delay * (2 ** (attempt - 1))
            logger.warning(
                "%s provider attempt %s/%s failed; retrying in %.1fs: %s",
                getattr(provider, "provider_name", provider.__class__.__name__),
                attempt,
                attempts,
                delay,
                sanitize_ai_error(exc),
            )
            # Unit-test doubles often raise RuntimeError("429") without an HTTP response;
            # avoid slowing those tests while retaining backoff for real requests.
            if isinstance(exc, requests.RequestException):
                time.sleep(delay)
    raise last_error or AIEnrichmentError("AI provider returned no response")


def summarize_article_strict(provider, article):
    """Summarize one article for production; mock output is never accepted."""
    if isinstance(provider, MockAIProvider):
        raise AIEnrichmentError("Mock AI provider is disabled for production")
    if isinstance(provider, AIProviderChain):
        return provider.summarize(article, allow_mock=False)
    payload = _request_provider_with_retry(provider, article)
    validate_ai_provider_payload(payload)
    result = normalize_ai_payload(
        payload,
        article,
        provider.prompt_version,
        provider.model_name,
        allow_fallback=False,
    )
    result["ai_provider"] = getattr(provider, "provider_name", provider.__class__.__name__)
    result["fallback_errors"] = []
    return result


def _should_retry_ai_error(exc):
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code in {408, 409, 425, 429, 500, 502, 503, 504}:
        return True
    text = str(exc).lower()
    return any(marker in text for marker in ["429", "rate limit", "too many requests", "timeout", "temporarily"])


def _env_int(key, default):
    try:
        return int(os.getenv(key, str(default)))
    except (TypeError, ValueError):
        return default


def _env_float(key, default):
    try:
        return float(os.getenv(key, str(default)))
    except (TypeError, ValueError):
        return default


def _mock_headline(source_name, category, title):
    clean_category = str(category or "hàng hải").strip()
    clean_title = str(title or "").strip()
    if clean_title and _has_vietnamese_diacritics(clean_title):
        return clean_title
    return f"Cập nhật {clean_category} từ {source_name}"


def _looks_unaccented_vietnamese(value):
    text = str(value or "").strip().lower()
    if not text or _has_vietnamese_diacritics(text):
        return False
    return any(marker in text for marker in UNACCENTED_VIETNAMESE_MARKERS)


def _has_vietnamese_diacritics(value):
    return any(char.lower() in VIETNAMESE_DIACRITICS for char in str(value or ""))
