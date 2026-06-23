import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.cli import run_cli
from app.services.visual_brief_renderer import (
    extract_image_url,
    generate_image_cards,
    render_card_html,
    render_html_to_png,
    resolve_card_image,
)


class VisualBriefRendererTests(unittest.TestCase):
    def test_extract_image_url_prefers_og_image(self):
        html = """
        <html><head>
          <meta name="twitter:image" content="https://example.com/twitter.jpg">
          <meta property="og:image" content="https://example.com/og.jpg">
        </head></html>
        """

        image_url = extract_image_url(html, "https://example.com/news/story")

        self.assertEqual(image_url, "https://example.com/og.jpg")

    def test_extract_image_url_supports_twitter_image(self):
        html = """<meta name="twitter:image" content="https://example.com/twitter.jpg">"""

        image_url = extract_image_url(html, "https://example.com/news/story")

        self.assertEqual(image_url, "https://example.com/twitter.jpg")

    def test_extract_image_url_resolves_relative_img_src(self):
        html = """<article><img src="/media/story.jpg" alt="Story"></article>"""

        image_url = extract_image_url(html, "https://example.com/news/story")

        self.assertEqual(image_url, "https://example.com/media/story.jpg")

    def test_resolve_card_image_falls_back_without_image_metadata(self):
        session = Mock()
        session.get.return_value = _response(text="<html><head></head><body>No image</body></html>")

        image = resolve_card_image("https://example.com/news/story", session=session)

        self.assertEqual(image["status"], "fallback")
        self.assertEqual(image["reason"], "image_not_found")

    def test_resolve_card_image_downloads_and_caches_image(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            session = Mock()
            session.get.side_effect = [
                _response(text='<meta property="og:image" content="/story.png">'),
                _response(content=b"fake-png", content_type="image/png"),
            ]

            image = resolve_card_image(
                "https://example.com/news/story",
                session=session,
                cache_dir=Path(temp_dir),
            )

            self.assertEqual(image["status"], "ok")
            self.assertEqual(image["image_url"], "https://example.com/story.png")
            self.assertTrue(Path(image["local_path"]).exists())
            self.assertTrue(image["data_uri"].startswith("data:image/png;base64,"))

    def test_resolve_card_image_normalizes_markdown_article_url(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            session = Mock()
            session.get.side_effect = [
                _response(text='<meta property="og:image" content="/story.png">'),
                _response(content=b"fake-png", content_type="image/png"),
            ]

            image = resolve_card_image(
                "[https://example.com/news/story](https://example.com/news/story)",
                session=session,
                cache_dir=Path(temp_dir),
            )

            self.assertEqual(image["status"], "ok")
            self.assertEqual(session.get.call_args_list[0].args[0], "https://example.com/news/story")

    def test_generate_image_cards_writes_manifest_preview_and_cards(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            brief_path = temp_path / "morning_brief.json"
            output_dir = temp_path / "cards"
            brief_path.write_text(json.dumps(_brief_payload(), ensure_ascii=False), encoding="utf-8")

            with patch("app.services.visual_brief_renderer.resolve_card_image", return_value={"status": "fallback"}):
                with patch("app.services.visual_brief_renderer.render_html_to_png", side_effect=_write_fake_png):
                    result = generate_image_cards(
                        "morning",
                        limit=1,
                        output_dir=output_dir,
                        source_brief_path=brief_path,
                    )

            manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))

            self.assertEqual(result["items"], 1)
            self.assertTrue((output_dir / "card_01.png").exists())
            self.assertTrue((output_dir / "preview.html").exists())
            self.assertEqual(manifest["cards"][0]["source_name"], "Safety4Sea")
            self.assertEqual(manifest["cards"][0]["original_url"], "https://example.com/story")
            self.assertEqual(manifest["cards"][0]["image_status"], "fallback")

    def test_render_html_to_png_smoke(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "card.png"
            html = render_card_html(_brief_payload(), _brief_payload()["items"][0], 1, {"status": "fallback"})

            try:
                render_html_to_png(html, output_path)
            except RuntimeError as exc:
                if "playwright" in str(exc).lower() or "chromium" in str(exc).lower():
                    self.skipTest(str(exc))
                raise

            self.assertTrue(output_path.exists())
            self.assertGreater(output_path.stat().st_size, 0)

    def test_card_html_keeps_long_text_and_avoids_text_clipping(self):
        payload = _brief_payload()
        item = dict(payload["items"][0])
        item["summary"] = " ".join(["Long summary content"] * 45)
        item["impact_note"] = " ".join(["Important maritime impact"] * 32)

        html = render_card_html(payload, item, 1, {"status": "fallback"})

        self.assertIn(item["summary"], html)
        self.assertIn(item["impact_note"], html)
        self.assertIn("function fitCard()", html)
        self.assertNotIn("max-height", html)

    def test_cli_generate_image_cards_invokes_renderer(self):
        with patch("app.cli.generate_image_cards") as renderer:
            renderer.return_value = {
                "items": 1,
                "output_dir": Path("out"),
                "manifest_path": Path("out/manifest.json"),
                "preview_path": Path("out/preview.html"),
            }

            code = run_cli(["generate-image-cards", "--type", "morning", "--limit", "1"])

            self.assertEqual(code, 0)
            renderer.assert_called_once()


def _brief_payload():
    return {
        "brief_type": "morning",
        "title": "Maritime Intelligence Hub - Morning Brief",
        "generated_at": "2026-06-10T08:00:00",
        "items": [
            {
                "title": "Port safety notice issued for pilotage operations",
                "summary": "Short operational safety update for port and vessel operators.",
                "impact_note": "Operators should monitor berth planning and pilotage windows.",
                "source_name": "Safety4Sea",
                "original_url": "https://example.com/story",
                "published_at": "2026-06-10T01:00:00+00:00",
                "category": "Safety",
                "importance_score": 8,
                "hotness_score": 9,
            }
        ],
    }


def _response(text="", content=b"", content_type="text/html"):
    response = Mock()
    response.text = text
    response.headers = {"Content-Type": content_type}
    response.raise_for_status.return_value = None
    response.iter_content.return_value = [content] if content else []
    return response


def _write_fake_png(_html, output_path):
    Path(output_path).write_bytes(b"\x89PNG\r\n\x1a\nfake")


if __name__ == "__main__":
    unittest.main()
