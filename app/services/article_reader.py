"""Bounded article text extraction with Jina -> Trafilatura -> BeautifulSoup fallback."""

import os
import re

import requests


MIN_TEXT_LENGTH = 180
MAX_TEXT_LENGTH = 8000


def read_article(url, session=None, timeout=25):
    session = session or requests.Session()
    errors = []
    jina_base = os.getenv("JINA_READER_URL", "https://r.jina.ai/").strip().rstrip("/")
    jina_url = f"{jina_base}/{url}"
    try:
        response = session.get(
            jina_url,
            timeout=timeout,
            headers={"Accept": "text/plain", "X-Return-Format": "markdown"},
        )
        response.raise_for_status()
        text = _clean(response.text)
        if len(text) >= MIN_TEXT_LENGTH:
            return _result(text, "jina", errors)
        errors.append("jina:content-too-short")
    except Exception as exc:
        errors.append(f"jina:{exc}")

    try:
        response = session.get(url, timeout=timeout, headers={"User-Agent": "MaritimeIntelligenceHub/1.0"})
        response.raise_for_status()
        html_text = response.text
        try:
            import trafilatura

            text = _clean(trafilatura.extract(html_text) or "")
        except Exception as exc:
            errors.append(f"trafilatura:{exc}")
            text = ""
        if len(text) >= MIN_TEXT_LENGTH:
            return _result(text, "trafilatura", errors)
        errors.append("trafilatura:content-too-short")

        try:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(html_text, "html.parser")
            for node in soup(["script", "style", "noscript", "nav", "footer", "header"]):
                node.decompose()
            text = _clean(soup.get_text(" ", strip=True))
        except Exception as exc:
            errors.append(f"beautifulsoup:{exc}")
            text = ""
        if len(text) >= MIN_TEXT_LENGTH:
            return _result(text, "beautifulsoup", errors)
        errors.append("beautifulsoup:content-too-short")
    except Exception as exc:
        errors.append(f"html:{exc}")
    return _result("", "none", errors)


def _result(text, provider, errors):
    return {
        "text": text[:MAX_TEXT_LENGTH],
        "provider": provider,
        "status": "ok" if text else "error",
        "content_length": len(text),
        "errors": errors,
    }


def _clean(value):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(value or ""))).strip()
