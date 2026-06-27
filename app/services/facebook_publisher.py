import json
from pathlib import Path

import requests


FACEBOOK_GRAPH_API_BASE = "https://graph.facebook.com/v20.0"
SOURCE_LINK_DESCRIPTION_PREFIX = "Link nguồn: "


class FacebookAPIError(RuntimeError):
    def __init__(self, message, status_code=None, error_type=None, error_code=None, uploaded_photo_ids=None):
        super().__init__(message)
        self.status_code = status_code
        self.error_type = error_type
        self.error_code = error_code
        self.uploaded_photo_ids = uploaded_photo_ids or []


def check_page(page_id, access_token, session=None):
    page_id = _required_text(page_id, "Facebook Page ID")
    access_token = _required_text(access_token, "Facebook Page access token")
    session = session or requests.Session()
    response = session.get(
        f"{FACEBOOK_GRAPH_API_BASE}/{page_id}",
        params={"fields": "id,name", "access_token": access_token},
        timeout=20,
    )
    return _facebook_payload(response)


def publish_photo_post(page_id, access_token, cards, message, dry_run=False, session=None):
    page_id = _required_text(page_id, "Facebook Page ID")
    access_token = _required_text(access_token, "Facebook Page access token")
    cards = list(cards or [])
    if not cards:
        raise ValueError("No Facebook image cards to publish.")

    image_paths = [_card_image_path(card) for card in cards]
    for image_path in image_paths:
        if not image_path.exists():
            raise FileNotFoundError(f"Image not found: {image_path}")

    photo_descriptions = _photo_descriptions(cards)
    attached_media = [{"media_fbid": f"dry_run_photo_{index}"} for index, _ in enumerate(image_paths, start=1)]
    post_payload = _post_payload(access_token, message, attached_media)
    if dry_run:
        return {
            "dry_run": True,
            "page_id": page_id,
            "message": str(message or "").strip(),
            "image_paths": [str(path) for path in image_paths],
            "photo_descriptions": photo_descriptions,
            "post_payload": _redacted_payload(post_payload),
            "uploaded_photo_ids": [item["media_fbid"] for item in attached_media],
            "post_id": None,
            "fallback": False,
        }

    session = session or requests.Session()
    uploaded_photo_ids = []
    try:
        for image_path, description in zip(image_paths, photo_descriptions):
            photo = upload_unpublished_photo(page_id, access_token, image_path, description=description, session=session)
            photo_id = str(photo.get("id") or "").strip()
            if not photo_id:
                raise FacebookAPIError("Facebook photo upload returned no photo id.")
            uploaded_photo_ids.append(photo_id)

        attached_media = [{"media_fbid": photo_id} for photo_id in uploaded_photo_ids]
        try:
            post = create_photo_feed_post(page_id, access_token, message, attached_media, session=session)
            return {
                "dry_run": False,
                "page_id": page_id,
                "message": str(message or "").strip(),
                "image_paths": [str(path) for path in image_paths],
                "photo_descriptions": photo_descriptions,
                "uploaded_photo_ids": uploaded_photo_ids,
                "post_id": post.get("id"),
                "fallback": False,
                "post": post,
            }
        except FacebookAPIError as exc:
            exc.uploaded_photo_ids = uploaded_photo_ids
            if _should_fallback_to_single_photo_posts(exc):
                fallback_posts = publish_single_photo_posts(
                    page_id,
                    access_token,
                    image_paths,
                    photo_descriptions,
                    message,
                    session=session,
                )
                return {
                    "dry_run": False,
                    "page_id": page_id,
                    "message": str(message or "").strip(),
                    "image_paths": [str(path) for path in image_paths],
                    "photo_descriptions": photo_descriptions,
                    "uploaded_photo_ids": uploaded_photo_ids,
                    "post_id": fallback_posts[0].get("id") if fallback_posts else None,
                    "fallback": True,
                    "posts": fallback_posts,
                }
            raise
    except FacebookAPIError as exc:
        if uploaded_photo_ids and not exc.uploaded_photo_ids:
            exc.uploaded_photo_ids = uploaded_photo_ids
        raise


def upload_unpublished_photo(page_id, access_token, image_path, description=None, session=None):
    session = session or requests.Session()
    image_path = Path(image_path)
    data = {"access_token": access_token, "published": "false"}
    if description:
        data["caption"] = str(description).strip()
    with image_path.open("rb") as file:
        response = session.post(
            f"{FACEBOOK_GRAPH_API_BASE}/{page_id}/photos",
            data=data,
            files={"source": file},
            timeout=60,
        )
    return _facebook_payload(response)


def create_photo_feed_post(page_id, access_token, message, attached_media, session=None):
    session = session or requests.Session()
    response = session.post(
        f"{FACEBOOK_GRAPH_API_BASE}/{page_id}/feed",
        data=_post_payload(access_token, message, attached_media),
        timeout=30,
    )
    return _facebook_payload(response)


def publish_single_photo_posts(page_id, access_token, image_paths, photo_descriptions=None, message=None, session=None):
    session = session or requests.Session()
    posts = []
    descriptions = list(photo_descriptions or [])
    for index, image_path in enumerate(image_paths, start=1):
        description = descriptions[index - 1] if index <= len(descriptions) else ""
        data = {
            "access_token": access_token,
            "published": "true",
            "message": _single_photo_message(message if index == 1 else "", description),
        }
        with Path(image_path).open("rb") as file:
            response = session.post(
                f"{FACEBOOK_GRAPH_API_BASE}/{page_id}/photos",
                data=data,
                files={"source": file},
                timeout=60,
            )
        posts.append(_facebook_payload(response))
    return posts


def source_link_description(card):
    if not isinstance(card, dict):
        return ""
    url = str(card.get("original_url") or card.get("canonical_url") or "").strip()
    if not url:
        return ""
    return f"{SOURCE_LINK_DESCRIPTION_PREFIX}{url}"


def validate_cards_publish_safety(cards):
    errors = []
    for index, card in enumerate(cards or [], start=1):
        if not isinstance(card, dict):
            continue
        if not card.get("source_name"):
            errors.append(f"Card {index}: missing source_name")
        if not (card.get("original_url") or card.get("canonical_url")):
            errors.append(f"Card {index}: missing original_url")
    return {"ready": not errors, "errors": errors}


def _post_payload(access_token, message, attached_media):
    data = {"access_token": access_token, "message": str(message or "").strip()}
    for index, media in enumerate(attached_media):
        data[f"attached_media[{index}]"] = json.dumps(media, ensure_ascii=False)
    return data


def _photo_descriptions(cards):
    return [source_link_description(card) for card in cards or []]


def _single_photo_message(message, description):
    parts = [str(message or "").strip(), str(description or "").strip()]
    return "\n\n".join(part for part in parts if part)


def _facebook_payload(response):
    try:
        payload = response.json()
    except ValueError:
        response.raise_for_status()
        raise FacebookAPIError("Facebook returned a non-JSON response", status_code=response.status_code)

    if response.status_code >= 400 or payload.get("error"):
        error = payload.get("error") or {}
        message = error.get("message") or str(payload)
        error_type = error.get("type")
        error_code = error.get("code")
        raise FacebookAPIError(
            f"{response.status_code}: {message}",
            status_code=response.status_code,
            error_type=error_type,
            error_code=error_code,
        )
    return payload


def _card_image_path(card):
    if isinstance(card, dict):
        value = card.get("card_path") or card.get("image_path") or card.get("path")
    else:
        value = card
    if not value:
        raise ValueError("Facebook card has no image path.")
    return Path(value)


def _required_text(value, label):
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is required.")
    return text


def _redacted_payload(payload):
    redacted = dict(payload)
    if redacted.get("access_token"):
        redacted["access_token"] = "***"
    return redacted


def _should_fallback_to_single_photo_posts(exc):
    text = str(exc).lower()
    return "attached_media" in text or "multiple" in text or "multi-photo" in text
