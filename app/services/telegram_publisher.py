from pathlib import Path

import requests


TELEGRAM_API_BASE = "https://api.telegram.org"


class TelegramAPIError(RuntimeError):
    pass


def check_bot(token, session=None):
    session = session or requests.Session()
    response = session.get(f"{TELEGRAM_API_BASE}/bot{token}/getMe", timeout=20)
    payload = _telegram_payload(response)
    return payload.get("result", {})


def check_chat(token, chat_id, session=None):
    session = session or requests.Session()
    response = session.get(
        f"{TELEGRAM_API_BASE}/bot{token}/getChat",
        params={"chat_id": chat_id},
        timeout=20,
    )
    payload = _telegram_payload(response)
    return payload.get("result", {})


def send_message(token, chat_id, text, session=None):
    session = session or requests.Session()
    message = str(text or "").strip()
    if not message:
        return None
    response = session.post(
        f"{TELEGRAM_API_BASE}/bot{token}/sendMessage",
        data={"chat_id": chat_id, "text": message},
        timeout=20,
    )
    payload = _telegram_payload(response)
    return payload.get("result", {})


def send_photo(token, chat_id, image_path, caption=None, session=None):
    session = session or requests.Session()
    image_path = Path(image_path)
    if not image_path.exists():
        raise FileNotFoundError(f"Image not found: {image_path}")

    data = {"chat_id": chat_id}

    with image_path.open("rb") as file:
        response = session.post(
            f"{TELEGRAM_API_BASE}/bot{token}/sendPhoto",
            data=data,
            files={"photo": file},
            timeout=45,
        )
    payload = _telegram_payload(response)
    return payload.get("result", {})


def send_photos(token, chat_id, cards, session=None):
    session = session or requests.Session()
    sent = []
    for card in cards:
        image_path = card.get("card_path") if isinstance(card, dict) else card
        sent.append(send_photo(token, chat_id, image_path, session=session))
    return sent


def _telegram_payload(response):
    try:
        payload = response.json()
    except ValueError:
        response.raise_for_status()
        raise TelegramAPIError("Telegram returned a non-JSON response")

    if response.status_code >= 400 or not payload.get("ok"):
        description = payload.get("description") or str(payload)
        error_code = payload.get("error_code") or response.status_code
        raise TelegramAPIError(f"{error_code}: {description}")
    return payload
