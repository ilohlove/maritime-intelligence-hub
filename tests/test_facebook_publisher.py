import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from app.services.facebook_publisher import (
    FacebookAPIError,
    check_page,
    publish_photo_post,
    validate_cards_publish_safety,
)


class FacebookPublisherTests(unittest.TestCase):
    def test_check_page_returns_page_info(self):
        session = Mock()
        session.get.return_value = _response({"id": "page-1", "name": "Maritime Hub"})

        result = check_page("page-1", "token", session=session)

        self.assertEqual(result["name"], "Maritime Hub")
        session.get.assert_called_once()

    def test_api_error_includes_message(self):
        session = Mock()
        session.get.return_value = _response(
            {"error": {"message": "Invalid OAuth access token.", "type": "OAuthException", "code": 190}},
            status_code=400,
        )

        with self.assertRaisesRegex(FacebookAPIError, "Invalid OAuth"):
            check_page("page-1", "bad-token", session=session)

    def test_dry_run_does_not_post(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "card.png"
            image_path.write_bytes(b"fake-png")
            session = Mock()

            result = publish_photo_post(
                "page-1",
                "token",
                [_card(image_path)],
                "Caption",
                dry_run=True,
                session=session,
            )

        self.assertTrue(result["dry_run"])
        self.assertEqual(result["image_paths"][0], str(image_path))
        self.assertEqual(result["planned_comments"][0]["message"], "Link nguồn: https://example.com/story")
        session.post.assert_not_called()

    def test_multi_photo_post_uploads_each_photo_creates_feed_post_and_comments_links(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            first = Path(temp_dir) / "card-1.png"
            second = Path(temp_dir) / "card-2.png"
            first.write_bytes(b"fake-png-1")
            second.write_bytes(b"fake-png-2")
            session = Mock()
            session.post.side_effect = [
                _response({"id": "photo-1"}),
                _response({"id": "photo-2"}),
                _response({"id": "page-1_post-1"}),
                _response({"id": "comment-1"}),
                _response({"id": "comment-2"}),
            ]

            result = publish_photo_post(
                "page-1",
                "token",
                [_card(first, "https://example.com/first"), _card(second, "https://example.com/second")],
                "Caption",
                session=session,
            )

        self.assertEqual(result["post_id"], "page-1_post-1")
        self.assertEqual(result["uploaded_photo_ids"], ["photo-1", "photo-2"])
        self.assertEqual([item["comment_id"] for item in result["source_link_comments"]], ["comment-1", "comment-2"])
        self.assertEqual(result["comment_errors"], [])
        self.assertEqual(session.post.call_count, 5)
        _, feed_kwargs = session.post.call_args_list[2]
        self.assertIn("attached_media[0]", feed_kwargs["data"])
        self.assertIn("attached_media[1]", feed_kwargs["data"])
        first_comment_url, first_comment_kwargs = session.post.call_args_list[3]
        second_comment_url, second_comment_kwargs = session.post.call_args_list[4]
        self.assertTrue(first_comment_url[0].endswith("/photo-1/comments"))
        self.assertEqual(first_comment_kwargs["data"]["message"], "Link nguồn: https://example.com/first")
        self.assertTrue(second_comment_url[0].endswith("/photo-2/comments"))
        self.assertEqual(second_comment_kwargs["data"]["message"], "Link nguồn: https://example.com/second")

    def test_comment_failure_does_not_fail_successful_post(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            first = Path(temp_dir) / "card-1.png"
            second = Path(temp_dir) / "card-2.png"
            first.write_bytes(b"fake-png-1")
            second.write_bytes(b"fake-png-2")
            session = Mock()
            session.post.side_effect = [
                _response({"id": "photo-1"}),
                _response({"id": "photo-2"}),
                _response({"id": "page-1_post-1"}),
                _response({"id": "comment-1"}),
                _response({"error": {"message": "Comment blocked", "type": "OAuthException", "code": 200}}, status_code=400),
            ]

            result = publish_photo_post(
                "page-1",
                "token",
                [_card(first, "https://example.com/first"), _card(second, "https://example.com/second")],
                "Caption",
                session=session,
            )

        self.assertEqual(result["post_id"], "page-1_post-1")
        self.assertEqual(len(result["source_link_comments"]), 1)
        self.assertEqual(len(result["comment_errors"]), 1)
        self.assertIn("Comment blocked", result["comment_errors"][0]["error"])

    def test_fallback_single_photo_posts_comment_each_returned_photo_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            first = Path(temp_dir) / "card-1.png"
            second = Path(temp_dir) / "card-2.png"
            first.write_bytes(b"fake-png-1")
            second.write_bytes(b"fake-png-2")
            session = Mock()
            session.post.side_effect = [
                _response({"id": "photo-1"}),
                _response({"id": "photo-2"}),
                _response(
                    {"error": {"message": "attached_media is not supported", "type": "OAuthException", "code": 100}},
                    status_code=400,
                ),
                _response({"id": "fallback-photo-1"}),
                _response({"id": "fallback-photo-2"}),
                _response({"id": "comment-1"}),
                _response({"id": "comment-2"}),
            ]

            result = publish_photo_post(
                "page-1",
                "token",
                [_card(first, "https://example.com/first"), _card(second, "https://example.com/second")],
                "Caption",
                session=session,
            )

        self.assertTrue(result["fallback"])
        self.assertEqual(result["post_id"], "fallback-photo-1")
        self.assertEqual(
            [item["photo_id"] for item in result["source_link_comments"]],
            ["fallback-photo-1", "fallback-photo-2"],
        )
        self.assertEqual(session.post.call_count, 7)

    def test_missing_image_path_fails_before_api_call(self):
        session = Mock()

        with self.assertRaises(FileNotFoundError):
            publish_photo_post("page-1", "token", [_card("missing.png")], "Caption", session=session)

        session.post.assert_not_called()

    def test_post_creation_failure_does_not_return_success(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "card.png"
            image_path.write_bytes(b"fake-png")
            session = Mock()
            session.post.side_effect = [
                _response({"id": "photo-1"}),
                _response({"error": {"message": "Unknown error", "type": "OAuthException", "code": 1}}, status_code=500),
            ]

            with self.assertRaises(FacebookAPIError) as raised:
                publish_photo_post("page-1", "token", [_card(image_path)], "Caption", session=session)

        self.assertEqual(raised.exception.uploaded_photo_ids, ["photo-1"])

    def test_publish_safety_requires_source_and_url(self):
        safety = validate_cards_publish_safety([{"card_path": "card.png", "source_name": "Source"}])

        self.assertFalse(safety["ready"])
        self.assertIn("missing original_url", safety["errors"][0])


def _card(path, original_url="https://example.com/story"):
    return {
        "card_path": str(path),
        "title": "Port update",
        "source_name": "Safety4Sea",
        "original_url": original_url,
        "item_key": "url:test",
    }


def _response(payload, status_code=200):
    response = Mock()
    response.status_code = status_code
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


if __name__ == "__main__":
    unittest.main()
