import hashlib
import json
import os
import random
import re
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from app.config import ROOT_DIR
from app.services.facebook_publisher import validate_cards_publish_safety
from app.services.storage import (
    cancel_facebook_group_delivery,
    count_facebook_group_attempts_since,
    expire_facebook_group_deliveries,
    get_facebook_group_delivery,
    get_facebook_group_delivery_by_id,
    get_facebook_group_last_delivery_times,
    list_facebook_group_deliveries,
    mark_items_published,
    record_facebook_group_delivery,
)


FACEBOOK_HOME_URL = "https://www.facebook.com/"
DELIVERED_STATUSES = {"published", "pending"}
PROFILE_DIR = Path(os.getenv("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "Maritime Intelligence Hub" / "browser_profiles" / "facebook"
DIAGNOSTIC_DIR = ROOT_DIR / "logs" / "facebook_groups"
_PUBLISH_LOCK = threading.Lock()
VIETNAM_TIMEZONE = timezone(timedelta(hours=7))


class FacebookGroupPublisherError(RuntimeError):
    pass


class FacebookSafetyStop(FacebookGroupPublisherError):
    pass


class FacebookLoginRequired(FacebookSafetyStop):
    pass


def normalize_group_url(value):
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("Facebook group URL is required.")
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlsplit(raw)
    host = (parsed.hostname or "").lower()
    if host != "facebook.com" and not host.endswith(".facebook.com"):
        raise ValueError("Group URL must use facebook.com.")
    match = re.match(r"^/groups/([^/?#]+)", parsed.path or "", flags=re.IGNORECASE)
    if not match:
        raise ValueError("Group URL must have the form https://www.facebook.com/groups/<group>.")
    group_slug = match.group(1).strip()
    if not group_slug:
        raise ValueError("Facebook group identifier is missing.")
    return f"https://www.facebook.com/groups/{group_slug}"


def normalize_groups(groups):
    normalized = []
    seen_ids = set()
    seen_urls = set()
    errors = []
    for index, raw_group in enumerate(groups or [], start=1):
        if not isinstance(raw_group, dict):
            errors.append(f"Group {index}: invalid configuration")
            continue
        try:
            url = normalize_group_url(raw_group.get("url"))
        except ValueError as exc:
            errors.append(f"Group {index}: {exc}")
            continue
        group_id = str(raw_group.get("id") or "").strip()
        if not group_id:
            group_id = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
        if group_id in seen_ids:
            errors.append(f"Group {index}: duplicate id '{group_id}'")
            continue
        if url in seen_urls:
            errors.append(f"Group {index}: duplicate URL '{url}'")
            continue
        try:
            priority = max(1, int(raw_group.get("priority") or 100))
        except (TypeError, ValueError):
            errors.append(f"Group {index}: priority must be a positive integer")
            continue
        seen_ids.add(group_id)
        seen_urls.add(url)
        normalized.append(
            {
                "id": group_id,
                "name": str(raw_group.get("name") or group_id).strip(),
                "url": url,
                "enabled": bool(raw_group.get("enabled", True)),
                "caption_template": str(raw_group.get("caption_template") or "").strip(),
                "priority": priority,
            }
        )
    return normalized, errors


def validate_group_config(groups, require_enabled=True):
    normalized, errors = normalize_groups(groups)
    if require_enabled and not any(group["enabled"] for group in normalized):
        errors.append("At least one Facebook group must be enabled.")
    return {"ready": not errors, "groups": normalized, "errors": errors}


def validate_group_caption_templates(groups):
    errors = []
    seen = {}
    for group in groups or []:
        if not isinstance(group, dict) or not group.get("enabled", True):
            continue
        name = str(group.get("name") or group.get("id") or "Group")
        template = str(group.get("caption_template") or "").strip()
        if not template:
            errors.append(f"{name}: a group-specific caption is required")
            continue
        key = re.sub(r"\s+", " ", template).strip().casefold()
        if key in seen:
            errors.append(f"{name}: caption duplicates {seen[key]}")
        else:
            seen[key] = name
    return {"ready": not errors, "errors": errors}


def build_batch_id(cards, brief_label=""):
    keys = []
    for index, card in enumerate(cards or [], start=1):
        if isinstance(card, dict):
            key = card.get("item_key") or card.get("dedupe_key") or card.get("canonical_url") or card.get("original_url")
        else:
            key = str(card)
        keys.append(str(key or f"card-{index}"))
    material = json.dumps({"brief_label": str(brief_label or ""), "items": keys}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def build_group_caption(base_caption, cards):
    caption = str(base_caption or "").strip()
    sources = []
    seen = set()
    for card in cards or []:
        if not isinstance(card, dict):
            continue
        source_name = str(card.get("source_name") or "").strip()
        url = str(card.get("original_url") or card.get("canonical_url") or "").strip()
        if not source_name or not url:
            continue
        item = f"- {source_name}: {url}"
        if item not in seen:
            seen.add(item)
            sources.append(item)
    source_block = "Nguồn bài viết:\n" + "\n".join(sources) if sources else ""
    return "\n\n".join(part for part in [caption, source_block] if part)


def publish_to_groups(
    cards,
    groups,
    default_caption,
    caption_renderer=None,
    brief_label="",
    dry_run=False,
    delay_min_seconds=900,
    delay_max_seconds=1800,
    max_groups_per_brief=2,
    max_groups_per_day=4,
    queue_expiry_hours=12,
    manual=False,
    profile_dir=PROFILE_DIR,
    db_path=None,
    browser_factory=None,
    sleep_fn=time.sleep,
    random_uniform=random.uniform,
    now=None,
    progress_callback=None,
):
    safety = validate_cards_publish_safety(cards)
    if not safety["ready"]:
        raise ValueError("Facebook group publish safety failed: " + "; ".join(safety["errors"]))
    config = validate_group_config(groups)
    if not config["ready"]:
        raise ValueError("Invalid Facebook group configuration: " + "; ".join(config["errors"]))
    image_paths = [_card_image_path(card) for card in cards]
    batch_id = build_batch_id(cards, brief_label=brief_label)
    enabled_groups = [group for group in config["groups"] if group["enabled"]]
    captions = _render_unique_group_captions(enabled_groups, cards, caption_renderer, brief_label)
    minimum = max(0, int(delay_min_seconds))
    maximum = max(minimum, int(delay_max_seconds))
    per_brief_limit = max(1, int(max_groups_per_brief))
    daily_limit = max(1, int(max_groups_per_day))
    browser_factory = browser_factory or facebook_browser_session
    storage_args = {} if db_path is None else {"db_path": db_path}
    effective_now = now or datetime.now(timezone.utc)
    expiry_time = effective_now + timedelta(hours=max(1, int(queue_expiry_hours)))

    if not _PUBLISH_LOCK.acquire(blocking=False):
        raise FacebookGroupPublisherError("Facebook Groups publisher is already running.")
    try:
        expire_facebook_group_deliveries(effective_now.isoformat(), **storage_args)
        if dry_run:
            results = []
            with browser_factory(profile_dir=profile_dir, headed=True) as browser:
                for group in enabled_groups:
                    browser.check_group(group["url"])
                    results.append(
                        _delivery_result(
                            group,
                            "dry_run",
                            batch_id,
                            caption=captions[group["id"]],
                            image_paths=image_paths,
                        )
                    )
            return _publish_result(batch_id, results, True, daily_limit, storage_args, effective_now)

        existing = {
            group["id"]: get_facebook_group_delivery(batch_id, group["id"], **storage_args)
            for group in enabled_groups
        }
        payload = {
            "brief_label": brief_label,
            "item_keys": _card_keys(cards),
            "image_paths": [str(path) for path in image_paths],
            "cards": [dict(card) for card in cards if isinstance(card, dict)],
        }
        for group in enabled_groups:
            if existing[group["id"]] is None:
                existing[group["id"]] = record_facebook_group_delivery(
                    batch_id,
                    group,
                    "queued",
                    expires_at=expiry_time.isoformat(),
                    payload={**payload, "caption": captions[group["id"]]},
                    **storage_args,
                )

        daily_used = count_facebook_group_attempts_since(_vietnam_day_start_iso(effective_now), **storage_args)
        daily_available = max(0, daily_limit - daily_used)
        delivered_in_batch = sum(
            1 for delivery in existing.values() if delivery and delivery.get("status") in DELIVERED_STATUSES
        )
        run_limit = min(1 if manual else max(0, per_brief_limit - delivered_in_batch), daily_available)
        retryable = {"queued", "failed", "needs_login"} if manual else {"queued"}
        last_deliveries = get_facebook_group_last_delivery_times(**storage_args)
        rotation_groups = sorted(
            enabled_groups,
            key=lambda group: (
                int(group.get("priority") or 100),
                last_deliveries.get(group["id"]) or "",
                group["id"],
            ),
        )
        candidates = [
            group for group in rotation_groups if existing[group["id"]] and existing[group["id"]].get("status") in retryable
        ][:run_limit]

        results_by_group = {}
        for group in enabled_groups:
            delivery = existing[group["id"]]
            if delivery and delivery.get("status") in DELIVERED_STATUSES:
                results_by_group[group["id"]] = _delivery_result(
                    group,
                    "skipped",
                    batch_id,
                    message="Already delivered",
                    post_url=delivery.get("post_url"),
                )
            elif delivery and delivery.get("status") in {"failed", "needs_login"} and not manual:
                results_by_group[group["id"]] = _delivery_result(
                    group, "skipped", batch_id, message="Manual retry is required."
                )
            elif delivery and delivery.get("status") in {"expired", "cancelled"}:
                results_by_group[group["id"]] = _delivery_result(
                    group, "skipped", batch_id, message=f"Queue item is {delivery.get('status')}."
                )

        planned_delays = [0]
        for _ in range(1, len(candidates)):
            planned_delays.append(random_uniform(minimum, maximum))
        scheduled_time = effective_now
        for index, group in enumerate(candidates):
            if index:
                scheduled_time += timedelta(seconds=planned_delays[index])
            record_facebook_group_delivery(
                batch_id,
                group,
                "queued",
                scheduled_at=scheduled_time.isoformat(),
                payload={**payload, "caption": captions[group["id"]]},
                **storage_args,
            )
        _notify_progress(progress_callback, daily_limit, storage_args, effective_now)

        if candidates:
            with browser_factory(profile_dir=profile_dir, headed=True) as browser:
                for index, group in enumerate(candidates):
                    if index:
                        sleep_fn(planned_delays[index])
                    try:
                        posted = browser.publish_group(group["url"], captions[group["id"]], image_paths, group["id"])
                        status = posted.get("status") or "failed"
                        result = _delivery_result(
                            group,
                            status,
                            batch_id,
                            message=posted.get("message", ""),
                            post_url=posted.get("post_url", ""),
                        )
                        record_facebook_group_delivery(
                            batch_id,
                            group,
                            status,
                            post_url=result.get("post_url", ""),
                            error_message=result.get("message", "") if status not in DELIVERED_STATUSES else "",
                            payload={**payload, "caption": captions[group["id"]]},
                            **storage_args,
                        )
                        _notify_progress(progress_callback, daily_limit, storage_args, effective_now)
                    except FacebookSafetyStop as exc:
                        message = _safe_error(exc)
                        status = "needs_login" if isinstance(exc, FacebookLoginRequired) else "failed"
                        result = _delivery_result(group, status, batch_id, message=message)
                        record_facebook_group_delivery(
                            batch_id,
                            group,
                            status,
                            error_message=message,
                            stop_reason=message,
                            payload={**payload, "caption": captions[group["id"]]},
                            **storage_args,
                        )
                        results_by_group[group["id"]] = result
                        remaining_queue = [
                            remaining
                            for remaining in enabled_groups
                            if remaining["id"] != group["id"] and remaining["id"] not in results_by_group
                        ]
                        for remaining in remaining_queue:
                            record_facebook_group_delivery(
                                batch_id,
                                remaining,
                                "queued",
                                stop_reason=message,
                                payload={**payload, "caption": captions[remaining["id"]]},
                                **storage_args,
                            )
                        _notify_progress(progress_callback, daily_limit, storage_args, effective_now)
                        break
                    except Exception as exc:
                        message = _safe_error(exc)
                        result = _delivery_result(group, "failed", batch_id, message=message)
                        record_facebook_group_delivery(
                            batch_id,
                            group,
                            "failed",
                            error_message=message,
                            payload={**payload, "caption": captions[group["id"]]},
                            **storage_args,
                        )
                        _notify_progress(progress_callback, daily_limit, storage_args, effective_now)
                    results_by_group[group["id"]] = result

        for group in enabled_groups:
            if group["id"] not in results_by_group:
                delivery = get_facebook_group_delivery(batch_id, group["id"], **storage_args)
                message = "Waiting for manual publish."
                if daily_available <= 0:
                    message = "Daily Facebook Groups limit reached."
                results_by_group[group["id"]] = _delivery_result(group, "queued", batch_id, message=message)
        results = [results_by_group[group["id"]] for group in enabled_groups]
        return _publish_result(batch_id, results, False, daily_limit, storage_args, effective_now)
    finally:
        _PUBLISH_LOCK.release()


def get_group_queue_status(max_groups_per_day=4, db_path=None, now=None):
    storage_args = {} if db_path is None else {"db_path": db_path}
    effective_now = now or datetime.now(timezone.utc)
    daily_limit = max(1, int(max_groups_per_day))
    expire_facebook_group_deliveries(effective_now.isoformat(), **storage_args)
    used = count_facebook_group_attempts_since(_vietnam_day_start_iso(effective_now), **storage_args)
    deliveries = list_facebook_group_deliveries(**storage_args)
    queued = [delivery for delivery in deliveries if delivery.get("status") == "queued"]
    scheduled = sorted(
        delivery["scheduled_at"] for delivery in queued if str(delivery.get("scheduled_at") or "").strip()
    )
    return {
        "daily_limit": daily_limit,
        "used_today": used,
        "remaining_today": max(0, daily_limit - used),
        "queued_total": len(queued),
        "next_scheduled_at": scheduled[0] if scheduled else None,
    }


def list_group_queue(db_path=None, include_history=False, now=None):
    storage_args = {} if db_path is None else {"db_path": db_path}
    effective_now = now or datetime.now(timezone.utc)
    expire_facebook_group_deliveries(effective_now.isoformat(), **storage_args)
    rows = list_facebook_group_deliveries(**storage_args)
    if not include_history:
        rows = [row for row in rows if row.get("status") in {"queued", "failed", "needs_login"}]
    return sorted(
        rows,
        key=lambda row: (
            int(row.get("priority") or 100),
            row.get("scheduled_at") or row.get("attempted_at") or "",
            int(row.get("id") or 0),
        ),
    )


def get_due_queue_item(db_path=None, now=None):
    effective_now = now or datetime.now(timezone.utc)
    for row in list_group_queue(db_path=db_path, now=effective_now):
        scheduled_at = str(row.get("scheduled_at") or "").strip()
        if (
            row.get("status") == "queued"
            and scheduled_at
            and scheduled_at <= effective_now.isoformat()
            and not str(row.get("stop_reason") or "").strip()
        ):
            return row
    return None


def cancel_group_queue_item(delivery_id, db_path=None):
    storage_args = {} if db_path is None else {"db_path": db_path}
    return cancel_facebook_group_delivery(delivery_id, **storage_args)


def publish_queue_item(
    delivery_id,
    max_groups_per_day=4,
    profile_dir=PROFILE_DIR,
    db_path=None,
    browser_factory=None,
    now=None,
):
    storage_args = {} if db_path is None else {"db_path": db_path}
    effective_now = now or datetime.now(timezone.utc)
    expire_facebook_group_deliveries(effective_now.isoformat(), **storage_args)
    delivery = get_facebook_group_delivery_by_id(delivery_id, **storage_args)
    if not delivery:
        raise ValueError("Facebook Groups queue item was not found.")
    if delivery.get("status") not in {"queued", "failed", "needs_login"}:
        raise ValueError(f"Queue item cannot be published from status '{delivery.get('status')}'.")
    used = count_facebook_group_attempts_since(_vietnam_day_start_iso(effective_now), **storage_args)
    if used >= max(1, int(max_groups_per_day)):
        raise FacebookGroupPublisherError("Daily Facebook Groups safety limit has been reached.")
    try:
        payload = json.loads(delivery.get("payload_json") or "{}")
    except json.JSONDecodeError as exc:
        raise FacebookGroupPublisherError("Queue item payload is invalid.") from exc
    caption = str(payload.get("caption") or "").strip()
    if not caption:
        raise FacebookGroupPublisherError("Queue item has no saved caption.")
    image_paths = [Path(path) for path in payload.get("image_paths") or []]
    if not image_paths or any(not path.is_file() for path in image_paths):
        raise FacebookGroupPublisherError("One or more queued image files are missing.")
    group = {
        "id": delivery["group_id"],
        "name": delivery.get("group_name") or delivery["group_id"],
        "url": delivery["group_url"],
        "priority": delivery.get("priority") or 100,
    }
    browser_factory = browser_factory or facebook_browser_session
    if not _PUBLISH_LOCK.acquire(blocking=False):
        raise FacebookGroupPublisherError("Facebook Groups publisher is already running.")
    try:
        with browser_factory(profile_dir=profile_dir, headed=True) as browser:
            try:
                posted = browser.publish_group(group["url"], caption, image_paths, group["id"])
                status = posted.get("status") or "failed"
                message = posted.get("message", "")
                post_url = posted.get("post_url", "")
                record_facebook_group_delivery(
                    delivery["batch_id"],
                    group,
                    status,
                    post_url=post_url,
                    error_message=message if status not in DELIVERED_STATUSES else "",
                    payload=payload,
                    **storage_args,
                )
                if status in DELIVERED_STATUSES:
                    mark_items_published(payload.get("cards") or [], **storage_args)
            except FacebookSafetyStop as exc:
                message = _safe_error(exc)
                status = "needs_login" if isinstance(exc, FacebookLoginRequired) else "failed"
                record_facebook_group_delivery(
                    delivery["batch_id"],
                    group,
                    status,
                    error_message=message,
                    stop_reason=message,
                    payload=payload,
                    **storage_args,
                )
                raise
            except Exception as exc:
                message = _safe_error(exc)
                status = "failed"
                record_facebook_group_delivery(
                    delivery["batch_id"], group, status, error_message=message, payload=payload, **storage_args
                )
    finally:
        _PUBLISH_LOCK.release()
    return {
        "delivery_id": int(delivery_id),
        "batch_id": delivery["batch_id"],
        "group_name": group["name"],
        "status": status,
        "message": message,
        "post_url": post_url if status in DELIVERED_STATUSES else "",
    }


def _render_unique_group_captions(groups, cards, caption_renderer, brief_label):
    captions = {}
    seen = {}
    errors = []
    for group in groups:
        template = str(group.get("caption_template") or "").strip()
        if not template:
            errors.append(f"{group.get('name') or group['id']}: a group-specific caption is required")
            continue
        rendered = caption_renderer(template, brief_label) if caption_renderer else template
        caption = build_group_caption(rendered, cards)
        comparison_key = re.sub(r"\s+", " ", caption).strip().casefold()
        duplicate_name = seen.get(comparison_key)
        if duplicate_name:
            errors.append(f"{group.get('name') or group['id']}: caption duplicates {duplicate_name}")
            continue
        seen[comparison_key] = group.get("name") or group["id"]
        captions[group["id"]] = caption
    if errors:
        raise ValueError("Facebook Groups caption policy failed: " + "; ".join(errors))
    return captions


def _publish_result(batch_id, results, dry_run, daily_limit, storage_args, now):
    statuses = ["published", "pending", "failed", "needs_login", "queued", "skipped", "dry_run"]
    counts = {status: sum(1 for item in results if item["status"] == status) for status in statuses}
    safety = get_group_queue_status(daily_limit, now=now, **storage_args)
    return {
        "batch_id": batch_id,
        "dry_run": bool(dry_run),
        "results": results,
        "counts": counts,
        "safety": safety,
    }


def _notify_progress(callback, daily_limit, storage_args, now):
    if callback is None:
        return
    callback(get_group_queue_status(daily_limit, now=now, **storage_args))


def _vietnam_day_start_iso(now):
    value = now
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    local = value.astimezone(VIETNAM_TIMEZONE)
    local_start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return local_start.astimezone(timezone.utc).isoformat()


def open_login_session(profile_dir=PROFILE_DIR, timeout_seconds=300, browser_factory=None):
    browser_factory = browser_factory or facebook_browser_session
    with browser_factory(profile_dir=profile_dir, headed=True) as browser:
        browser.page.goto(FACEBOOK_HOME_URL, wait_until="domcontentloaded", timeout=60000)
        deadline = time.time() + max(1, int(timeout_seconds))
        while time.time() < deadline:
            if browser.is_authenticated():
                return {"authenticated": True, "profile_dir": str(Path(profile_dir))}
            time.sleep(1)
    return {"authenticated": False, "profile_dir": str(Path(profile_dir))}


@contextmanager
def facebook_browser_session(profile_dir=PROFILE_DIR, headed=True):
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise FacebookGroupPublisherError("Playwright is not installed. Install it with `pip install playwright`.") from exc

    profile_path = Path(profile_dir)
    profile_path.mkdir(parents=True, exist_ok=True)
    playwright = sync_playwright().start()
    context = None
    try:
        try:
            context = playwright.chromium.launch_persistent_context(
                str(profile_path), channel="msedge", headless=not headed, viewport={"width": 1365, "height": 900}
            )
        except PlaywrightError:
            context = playwright.chromium.launch_persistent_context(
                str(profile_path), headless=not headed, viewport={"width": 1365, "height": 900}
            )
        page = context.pages[0] if context.pages else context.new_page()
        yield FacebookGroupBrowser(page)
    except PlaywrightError as exc:
        raise FacebookGroupPublisherError(f"Playwright browser error: {_safe_error(exc)}") from exc
    finally:
        try:
            if context is not None:
                context.close()
        finally:
            playwright.stop()


class FacebookGroupBrowser:
    COMPOSER_TRIGGERS = re.compile(r"write something|create (?:a )?public post|viết gì đó|tạo bài viết", re.IGNORECASE)
    PHOTO_BUTTONS = re.compile(r"photo/video|ảnh/video", re.IGNORECASE)
    POST_BUTTONS = re.compile(r"^(post|đăng)$", re.IGNORECASE)
    PENDING_TEXT = re.compile(r"pending|awaiting admin approval|đang chờ|chờ quản trị viên", re.IGNORECASE)
    LOGIN_PATHS = ("/login", "/checkpoint", "/two_step_verification")
    SAFETY_STOP_TEXT = re.compile(
        r"temporarily blocked|we limit how often|try again later|feature unavailable|"
        r"tạm thời bị chặn|giới hạn tần suất|hãy thử lại sau|tính năng này hiện không khả dụng|captcha",
        re.IGNORECASE,
    )

    def __init__(self, page):
        self.page = page

    def is_authenticated(self):
        url = str(self.page.url or "").lower()
        if any(part in url for part in self.LOGIN_PATHS):
            return False
        email = self.page.locator('input[name="email"]')
        return email.count() == 0

    def check_group(self, group_url):
        self.page.goto(group_url, wait_until="domcontentloaded", timeout=60000)
        self._require_authenticated()
        trigger = self._composer_trigger()
        if trigger is None:
            raise FacebookGroupPublisherError("Group composer was not found or this account cannot post in the group.")
        return True

    def publish_group(self, group_url, caption, image_paths, group_id="group"):
        self.page.goto(group_url, wait_until="domcontentloaded", timeout=60000)
        self._require_authenticated()
        trigger = self._composer_trigger()
        if trigger is None:
            raise FacebookGroupPublisherError("Group composer was not found or this account cannot post in the group.")
        try:
            trigger.click(timeout=15000)
            dialog = self.page.get_by_role("dialog").last
            dialog.wait_for(state="visible", timeout=15000)
            textbox = dialog.locator('[contenteditable="true"][role="textbox"]').last
            textbox.wait_for(state="visible", timeout=15000)
            textbox.fill(caption)

            file_input = dialog.locator('input[type="file"]').last
            if file_input.count() == 0:
                photo_button = dialog.get_by_role("button", name=self.PHOTO_BUTTONS).first
                if photo_button.count():
                    photo_button.click(timeout=10000)
                file_input = dialog.locator('input[type="file"]').last
            file_input.set_input_files([str(path) for path in image_paths], timeout=30000)

            post_button = dialog.get_by_role("button", name=self.POST_BUTTONS).last
            post_button.wait_for(state="visible", timeout=30000)
            post_button.click(timeout=30000)
            return self._wait_for_publish_result(dialog)
        except FacebookSafetyStop:
            raise
        except Exception as exc:
            self._save_diagnostic(group_id)
            raise FacebookGroupPublisherError(_safe_error(exc)) from exc

    def _composer_trigger(self):
        candidates = [
            self.page.get_by_role("button", name=self.COMPOSER_TRIGGERS),
            self.page.get_by_text(self.COMPOSER_TRIGGERS, exact=False),
        ]
        for locator in candidates:
            if locator.count():
                return locator.first
        return None

    def _require_authenticated(self):
        if not self.is_authenticated():
            raise FacebookLoginRequired("Facebook login, checkpoint, CAPTCHA, or 2FA confirmation is required.")
        try:
            page_text = self.page.locator("body").inner_text(timeout=2000)
        except Exception:
            page_text = ""
        match = self.SAFETY_STOP_TEXT.search(page_text)
        if match:
            raise FacebookSafetyStop(f"Facebook safety signal detected: {match.group(0)}. Publishing stopped.")

    def _wait_for_publish_result(self, dialog, timeout_seconds=45):
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            self._require_authenticated()
            if not dialog.is_visible():
                pending = self.page.get_by_text(self.PENDING_TEXT, exact=False)
                if pending.count() and pending.first.is_visible():
                    return {"status": "pending", "message": "Post submitted and is awaiting group approval."}
                return {"status": "published", "message": "Post composer closed after submission."}
            time.sleep(0.5)
        raise FacebookGroupPublisherError("Timed out while waiting for Facebook to confirm the group post.")

    def _save_diagnostic(self, group_id):
        url = str(self.page.url or "").lower()
        if any(part in url for part in self.LOGIN_PATHS):
            return
        DIAGNOSTIC_DIR.mkdir(parents=True, exist_ok=True)
        safe_group = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(group_id or "group"))[:60]
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        try:
            self.page.screenshot(path=str(DIAGNOSTIC_DIR / f"{timestamp}-{safe_group}.png"), full_page=False)
        except Exception:
            return


def _delivery_result(group, status, batch_id, message="", post_url="", caption="", image_paths=None):
    result = {
        "batch_id": batch_id,
        "group_id": group["id"],
        "group_name": group.get("name") or group["id"],
        "group_url": group["url"],
        "status": status,
        "message": str(message or ""),
        "post_url": str(post_url or ""),
    }
    if caption:
        result["caption"] = caption
    if image_paths is not None:
        result["image_paths"] = [str(path) for path in image_paths]
    return result


def _card_image_path(card):
    value = card.get("card_path") or card.get("image_path") or card.get("path") if isinstance(card, dict) else card
    path = Path(value or "")
    if not value or not path.is_file():
        raise FileNotFoundError(f"Facebook group image not found: {path}")
    return path


def _card_keys(cards):
    return [str(card.get("item_key") or card.get("dedupe_key") or "") for card in cards if isinstance(card, dict)]


def _safe_error(exc):
    text = str(exc or exc.__class__.__name__).strip()
    text = re.sub(r"(?i)(cookie|token|password|authorization)\s*[:=]\s*\S+", r"\1=***", text)
    return text[:1000]
