import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from app.services.telegram_publisher import TelegramAPIError, check_bot, check_chat, send_message, send_photos


class TelegramPublisherTests(unittest.TestCase):
    def test_check_bot_returns_result(self):
        session = Mock()
        session.get.return_value = _response({"ok": True, "result": {"username": "mih_bot"}})

        result = check_bot("token", session=session)

        self.assertEqual(result["username"], "mih_bot")
        session.get.assert_called_once()

    def test_check_chat_returns_result(self):
        session = Mock()
        session.get.return_value = _response({"ok": True, "result": {"title": "Channel"}})

        result = check_chat("token", "-100123", session=session)

        self.assertEqual(result["title"], "Channel")

    def test_telegram_error_includes_description(self):
        session = Mock()
        session.get.return_value = _response(
            {"ok": False, "error_code": 400, "description": "Bad Request: chat not found"},
            status_code=400,
        )

        with self.assertRaisesRegex(TelegramAPIError, "chat not found"):
            check_chat("token", "bad-chat", session=session)

    def test_send_message_posts_plain_text(self):
        session = Mock()
        session.post.return_value = _response({"ok": True, "result": {"message_id": 7}})

        result = send_message("token", "chat-id", "13/06/2026", session=session)

        self.assertEqual(result["message_id"], 7)
        _, kwargs = session.post.call_args
        self.assertEqual(kwargs["data"], {"chat_id": "chat-id", "text": "13/06/2026"})

    def test_send_photos_posts_each_card_without_caption(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "card.png"
            image_path.write_bytes(b"fake-png")
            session = Mock()
            session.post.return_value = _response({"ok": True, "result": {"message_id": 1}})

            sent = send_photos(
                "token",
                "chat-id",
                [
                    {
                        "card_path": str(image_path),
                        "title": "Port <update>",
                        "source_name": "Safety&Sea",
                    }
                ],
                session=session,
            )

        self.assertEqual(len(sent), 1)
        _, kwargs = session.post.call_args
        self.assertEqual(kwargs["data"]["chat_id"], "chat-id")
        self.assertNotIn("caption", kwargs["data"])
        self.assertNotIn("parse_mode", kwargs["data"])


def _response(payload, status_code=200):
    response = Mock()
    response.status_code = status_code
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


if __name__ == "__main__":
    unittest.main()
