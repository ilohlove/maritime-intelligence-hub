import json
import os
import socket
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from app.logger import logger
from app.services.combined_brief_source import evaluate_sheet_snapshot, fetch_sheet_snapshot
from app.services.pipeline import validate_sources
from app.services.storage import (
    DEFAULT_DB_PATH,
    TERMINAL_RUN_STATES,
    claim_news_run,
    claim_publish_delivery,
    heartbeat_news_run,
    heartbeat_terminal_news_run_lease,
    mark_items_published,
    mark_publish_delivery_failed,
    mark_publish_delivery_needs_review,
    mark_publish_delivery_succeeded,
    select_news_run_lane,
    update_news_run_state,
)


VIETNAM_TIMEZONE = timezone(timedelta(hours=7))
DEFAULT_ORCHESTRATION = {
    "lane_policy": "primary_then_backup",
    "primary_timeout_minutes": 30,
    "poll_interval_seconds": 60,
    "catch_up_window_minutes": 120,
    "lease_seconds": 300,
    "heartbeat_seconds": 30,
}


def build_publish_plan(
    publish=None,
    *,
    telegram_chat_ids=None,
    facebook_page_id=None,
    dry_run=False,
):
    """Freeze non-secret destinations and publish behavior for crash-safe resume."""
    source = dict(publish or {})
    if telegram_chat_ids is None:
        telegram_chat_ids = list(source.get("telegram_chat_ids") or [])
        if not telegram_chat_ids and source.get("telegram_chat_id"):
            telegram_chat_ids = [source["telegram_chat_id"]]
    safe_groups = []
    for raw_group in source.get("facebook_groups") or []:
        if not isinstance(raw_group, dict):
            continue
        safe_groups.append(
            {
                key: raw_group.get(key)
                for key in ("id", "name", "url", "priority", "enabled", "caption_template")
                if key in raw_group
            }
        )
    return {
        "version": 1,
        "dry_run": bool(dry_run),
        "send_telegram": bool(source.get("send_telegram")),
        "telegram_chat_ids": [str(value) for value in telegram_chat_ids or [] if str(value).strip()],
        "telegram_intro_text": str(source.get("telegram_intro_text") or "{date}"),
        "post_facebook": bool(source.get("post_facebook")),
        "facebook_page_id": str(
            facebook_page_id if facebook_page_id is not None else source.get("facebook_page_id") or ""
        ),
        "facebook_intro_text": str(source.get("facebook_intro_text") or ""),
        "facebook_dry_run": bool(source.get("facebook_dry_run", True)),
        "post_facebook_groups": bool(source.get("post_facebook_groups")),
        "facebook_groups": safe_groups,
        "facebook_group_delay_min_seconds": int(source.get("facebook_group_delay_min_seconds") or 900),
        "facebook_group_delay_max_seconds": int(source.get("facebook_group_delay_max_seconds") or 1800),
        "facebook_group_max_per_brief": int(source.get("facebook_group_max_per_brief") or 2),
        "facebook_group_max_per_day": int(source.get("facebook_group_max_per_day") or 4),
        "facebook_group_queue_expiry_hours": int(source.get("facebook_group_queue_expiry_hours") or 12),
        "facebook_group_dry_run": bool(source.get("facebook_group_dry_run", True)),
    }


def run_business_task():
    result, _ = validate_sources()
    if result.ok:
        return f"Source master OK: {result.row_count} sources"

    return f"Source master validation failed: {len(result.errors)} errors"


def build_news_run_id(slot, scheduled_at=None):
    safe_slot = _slot(slot)
    scheduled = _aware_datetime(scheduled_at or datetime.now(VIETNAM_TIMEZONE), VIETNAM_TIMEZONE)
    return f"{scheduled.astimezone(VIETNAM_TIMEZONE).date().isoformat()}:{safe_slot}"


def news_run_directory_name(run_id):
    """Map the database run id to a Windows-safe directory name."""
    value = str(run_id or "").strip()
    if not value:
        raise ValueError("run_id is required")
    return value.replace(":", "_")


def scheduled_datetime(slot, now=None, schedule_times=None):
    """Resolve today's configured schedule for a morning/evening slot."""
    safe_slot = _slot(slot)
    current = _aware_datetime(now or datetime.now(VIETNAM_TIMEZONE), VIETNAM_TIMEZONE).astimezone(VIETNAM_TIMEZONE)
    values = _normalized_schedule_times(schedule_times)
    candidates = []
    for value in values:
        hour, minute = [int(part) for part in value.split(":", 1)]
        label = "morning" if hour < 12 else "evening"
        if label == safe_slot:
            candidates.append(current.replace(hour=hour, minute=minute, second=0, microsecond=0))
    if candidates:
        return min(candidates)
    fallback = (7, 15) if safe_slot == "morning" else (19, 15)
    return current.replace(hour=fallback[0], minute=fallback[1], second=0, microsecond=0)


def due_scheduled_slot(now=None, schedule_times=None, catch_up_window_minutes=120):
    current = _aware_datetime(now or datetime.now(VIETNAM_TIMEZONE), VIETNAM_TIMEZONE).astimezone(VIETNAM_TIMEZONE)
    window_seconds = _bounded_int(catch_up_window_minutes, 1, 1440, 120) * 60
    due = []
    for value in _normalized_schedule_times(schedule_times):
        hour, minute = [int(part) for part in value.split(":", 1)]
        scheduled = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
        age_seconds = (current - scheduled).total_seconds()
        if 0 <= age_seconds < window_seconds:
            due.append((scheduled, "morning" if hour < 12 else "evening"))
    if not due:
        return None, None
    scheduled, slot = max(due, key=lambda item: item[0])
    return slot, scheduled


def select_scheduled_lane(
    slot,
    sheet_url,
    *,
    scheduled_at=None,
    schedule_times=None,
    orchestration=None,
    db_path=DEFAULT_DB_PATH,
    owner=None,
    snapshot_loader=fetch_sheet_snapshot,
    snapshot_evaluator=evaluate_sheet_snapshot,
    now_fn=None,
    sleep_fn=time.sleep,
    wait_callback=None,
):
    """Claim a run and latch exactly one source lane for the whole run."""
    settings = _orchestration_settings(orchestration)
    if settings["lane_policy"] != "primary_then_backup":
        raise ValueError("Only lane_policy=primary_then_backup is supported.")

    now_fn = now_fn or (lambda: datetime.now(timezone.utc))
    first_now = _aware_datetime(now_fn(), timezone.utc)
    scheduled = scheduled_at or scheduled_datetime(slot, now=first_now, schedule_times=schedule_times)
    scheduled = _aware_datetime(scheduled, VIETNAM_TIMEZONE)
    run_id = build_news_run_id(slot, scheduled)
    deadline = scheduled + timedelta(minutes=settings["primary_timeout_minutes"])
    worker = str(owner or _worker_id())

    claim = claim_news_run(
        run_id,
        worker,
        deadline,
        db_path=db_path,
        lease_seconds=settings["lease_seconds"],
        now=first_now,
    )
    log_news_event(
        "run_claim",
        run_id=run_id,
        owner=worker,
        state=claim.get("state"),
        lane=claim.get("lane"),
        acquired=claim.get("acquired"),
        reason=claim.get("claim_reason"),
    )
    if not claim.get("acquired"):
        return _decision_from_record(claim, worker, action="terminal" if claim.get("state") in TERMINAL_RUN_STATES else "busy")
    deadline = _aware_datetime(claim.get("deadline") or deadline, timezone.utc)
    if claim.get("lane"):
        return _decision_from_record(claim, worker, action="selected", reason="lane_already_latched")

    latest_snapshot = None
    latest_evaluation = None
    while True:
        current = _aware_datetime(now_fn(), timezone.utc)
        heartbeat_news_run(
            run_id,
            worker,
            db_path=db_path,
            lease_seconds=settings["lease_seconds"],
            now=current,
        )

        if not str(sheet_url or "").strip():
            latest_evaluation = {"state": "failed", "reason": "sheet_url_missing"}
        else:
            try:
                latest_snapshot = snapshot_loader(sheet_url, expected_run_id=run_id)
                latest_evaluation = latest_snapshot.get("evaluation") or snapshot_evaluator(latest_snapshot, run_id)
            except Exception as exc:
                latest_snapshot = None
                latest_evaluation = {"state": "waiting", "reason": f"sheet_unavailable:{_safe_error(exc)}"}

        evaluation_state = str((latest_evaluation or {}).get("state") or "invalid").lower()
        evaluation_reason = str((latest_evaluation or {}).get("reason") or "snapshot_invalid")
        if evaluation_state == "ready":
            completed_at = _aware_datetime(latest_snapshot.get("completed_at"), timezone.utc)
            if completed_at.astimezone(timezone.utc) > deadline.astimezone(timezone.utc):
                return _latch_lane(
                    run_id,
                    worker,
                    "backup",
                    None,
                    "primary_completed_after_deadline",
                    current,
                    settings,
                    db_path,
                )
            return _latch_lane(
                run_id,
                worker,
                "primary",
                latest_snapshot,
                evaluation_reason,
                current,
                settings,
                db_path,
            )
        if evaluation_state == "failed":
            return _latch_lane(
                run_id,
                worker,
                "backup",
                None,
                f"primary_failed:{evaluation_reason}",
                current,
                settings,
                db_path,
            )
        if current >= deadline.astimezone(timezone.utc):
            return _latch_lane(
                run_id,
                worker,
                "backup",
                None,
                f"primary_timeout:{evaluation_reason}",
                current,
                settings,
                db_path,
            )

        wait_seconds = min(
            settings["poll_interval_seconds"],
            max(0.0, (deadline.astimezone(timezone.utc) - current).total_seconds()),
        )
        wait_status = {
            "run_id": run_id,
            "state": evaluation_state,
            "reason": evaluation_reason,
            "deadline": deadline.isoformat(),
            "wait_seconds": wait_seconds,
        }
        log_news_event("primary_wait", **wait_status)
        if wait_callback:
            wait_callback(dict(wait_status))
        sleep_fn(wait_seconds)


def update_scheduled_run(
    decision,
    state,
    *,
    stats=None,
    error=None,
    release=False,
    db_path=DEFAULT_DB_PATH,
    now=None,
    required=True,
):
    if not decision or not decision.get("run_id") or not decision.get("owner"):
        return None
    updated = update_news_run_state(
        decision["run_id"],
        decision["owner"],
        state,
        db_path=db_path,
        stats=stats,
        error=error,
        release=release,
        lease_seconds=int(decision.get("lease_seconds") or DEFAULT_ORCHESTRATION["lease_seconds"]),
        now=now,
    )
    log_news_event(
        "run_state",
        run_id=decision["run_id"],
        owner=decision["owner"],
        lane=decision.get("lane"),
        state=state,
        error=_safe_error(error) if error else "",
        stats=stats or {},
    )
    if updated is None and required:
        raise RuntimeError(
            f"Lost ownership or lease for news run {decision['run_id']} while setting state {state}."
        )
    return updated


@contextmanager
def maintain_news_run_lease(decision, *, db_path=DEFAULT_DB_PATH):
    """Keep a run lease alive while rendering or publishing performs slow I/O."""
    if not decision or decision.get("action") != "selected":
        yield
        return

    stop_event = threading.Event()
    interval = max(1, int(decision.get("heartbeat_seconds") or DEFAULT_ORCHESTRATION["heartbeat_seconds"]))
    lease_seconds = max(interval + 1, int(decision.get("lease_seconds") or DEFAULT_ORCHESTRATION["lease_seconds"]))

    def heartbeat_loop():
        while not stop_event.wait(interval):
            renewed = heartbeat_news_run(
                decision["run_id"],
                decision["owner"],
                db_path=db_path,
                lease_seconds=lease_seconds,
            )
            if not renewed:
                log_news_event("lease_lost", run_id=decision["run_id"], owner=decision["owner"])
                return

    thread = threading.Thread(target=heartbeat_loop, name=f"news-run-{decision['run_id']}", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop_event.set()
        thread.join(timeout=min(2, interval))


@contextmanager
def maintain_terminal_news_run_lease(decision, *, db_path=DEFAULT_DB_PATH):
    """Keep the exclusive lease for an operator-approved terminal-run retry alive."""
    if not decision or decision.get("action") != "retry":
        yield
        return

    stop_event = threading.Event()
    interval = max(1, int(decision.get("heartbeat_seconds") or DEFAULT_ORCHESTRATION["heartbeat_seconds"]))
    lease_seconds = max(interval + 1, int(decision.get("lease_seconds") or DEFAULT_ORCHESTRATION["lease_seconds"]))

    def heartbeat_loop():
        while not stop_event.wait(interval):
            renewed = heartbeat_terminal_news_run_lease(
                decision["run_id"],
                decision["owner"],
                db_path=db_path,
                lease_seconds=lease_seconds,
            )
            if not renewed:
                log_news_event("terminal_retry_lease_lost", run_id=decision["run_id"], owner=decision["owner"])
                return

    thread = threading.Thread(
        target=heartbeat_loop,
        name=f"news-run-retry-{decision['run_id']}",
        daemon=True,
    )
    thread.start()
    try:
        yield
    finally:
        stop_event.set()
        thread.join(timeout=min(2, interval))


def log_news_event(event, **fields):
    payload = {"event": str(event), **fields}
    logger.info("news_run %s", json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))


def claim_delivery_cards(
    run_id,
    cards,
    channel,
    destination,
    owner,
    *,
    db_path=DEFAULT_DB_PATH,
    lease_seconds=300,
    fence_run=False,
    terminal_fence=False,
):
    """Claim each card before one external channel/destination call."""
    if fence_run and terminal_fence:
        raise ValueError("fence_run and terminal_fence are mutually exclusive")
    cards = list(cards or [])
    claimed = []
    skipped = []
    if (fence_run or terminal_fence) and not _renew_delivery_fence(
        run_id, owner, db_path, lease_seconds, terminal_fence
    ):
        return {
            "claimed": [],
            "skipped": [{"card": card, "reason": "run_lease_lost"} for card in cards],
        }
    for card in cards:
        item_key = _card_item_key(card)
        if not item_key:
            skipped.append({"card": card, "reason": "missing_item_key"})
            continue
        result = claim_publish_delivery(
            run_id,
            item_key,
            channel,
            destination,
            owner,
            db_path=db_path,
            lease_seconds=lease_seconds,
            payload={
                "item_key": item_key,
                "title": card.get("title") if isinstance(card, dict) else "",
                "original_url": card.get("original_url") if isinstance(card, dict) else "",
                "canonical_url": card.get("canonical_url") if isinstance(card, dict) else "",
                "title_hash": card.get("title_hash") if isinstance(card, dict) else "",
                "source_name": card.get("source_name") if isinstance(card, dict) else "",
                "source_type": card.get("source_type") if isinstance(card, dict) else "",
                "published_at": card.get("published_at") if isinstance(card, dict) else "",
            },
        )
        if result.get("acquired"):
            claimed.append(card)
        else:
            skipped.append({"card": card, "reason": result.get("claim_reason") or result.get("status")})
    if (fence_run or terminal_fence) and claimed and not _renew_delivery_fence(
        run_id, owner, db_path, lease_seconds, terminal_fence
    ):
        for card in claimed:
            item_key = _card_item_key(card)
            if item_key:
                mark_publish_delivery_failed(
                    run_id,
                    item_key,
                    channel,
                    destination,
                    owner,
                    "run_lease_lost_before_publish",
                    db_path=db_path,
                    retryable=True,
                )
            skipped.append({"card": card, "reason": "run_lease_lost"})
        claimed = []
    result = {"claimed": claimed, "skipped": skipped}
    log_news_event(
        "delivery_claim",
        run_id=run_id,
        channel=channel,
        destination=destination,
        owner=owner,
        claimed=len(claimed),
        skipped=len(skipped),
        blockers=delivery_claim_blockers(result),
    )
    return result


def _renew_delivery_fence(run_id, owner, db_path, lease_seconds, terminal_fence):
    heartbeat = heartbeat_terminal_news_run_lease if terminal_fence else heartbeat_news_run
    return heartbeat(
        run_id,
        owner,
        db_path=db_path,
        lease_seconds=lease_seconds,
    )


def delivery_claim_blockers(claims):
    """Return ledger outcomes that require attention before a run can succeed."""
    safe_reasons = {"succeeded"}
    return sorted(
        {
            str(item.get("reason") or "unknown")
            for item in (claims or {}).get("skipped", [])
            if str(item.get("reason") or "unknown") not in safe_reasons
        }
    )


def finish_delivery_cards(
    run_id,
    cards,
    channel,
    destination,
    owner,
    *,
    succeeded,
    result=None,
    error=None,
    db_path=DEFAULT_DB_PATH,
):
    """Finish claimed deliveries; uncertain failures are quarantined from auto-retry."""
    updated = 0
    for card in cards or []:
        item_key = _card_item_key(card)
        if not item_key:
            continue
        if succeeded:
            record = mark_publish_delivery_succeeded(
                run_id,
                item_key,
                channel,
                destination,
                owner,
                db_path=db_path,
                result=result or {},
            )
        else:
            record = mark_publish_delivery_needs_review(
                run_id,
                item_key,
                channel,
                destination,
                owner,
                _safe_error(error) or "external_publish_outcome_unknown",
                db_path=db_path,
                result=result or {},
            )
        updated += int(record is not None)
    log_news_event(
        "delivery_result",
        run_id=run_id,
        channel=channel,
        destination=destination,
        owner=owner,
        status="succeeded" if succeeded else "needs_review",
        updated=updated,
        error=_safe_error(error) if error else "",
        result=result or {},
    )
    return updated


def release_delivery_cards(
    run_id,
    cards,
    channel,
    destination,
    owner,
    reason,
    *,
    db_path=DEFAULT_DB_PATH,
):
    """Release claims when no external I/O occurred, allowing an intentional retry."""
    updated = 0
    for card in cards or []:
        item_key = _card_item_key(card)
        if not item_key:
            continue
        record = mark_publish_delivery_failed(
            run_id,
            item_key,
            channel,
            destination,
            owner,
            _safe_error(reason) or "publish_not_started",
            db_path=db_path,
            retryable=True,
        )
        updated += int(record is not None)
    return updated


class FacebookGroupDeliveryGuard:
    """Fence and ledger-claim a whole image batch immediately before each group post."""

    def __init__(
        self,
        run_id,
        cards,
        owner,
        *,
        db_path=DEFAULT_DB_PATH,
        lease_seconds=300,
        fence_run=False,
        terminal_fence=False,
    ):
        self.run_id = str(run_id)
        self.cards = list(cards or [])
        self.owner = str(owner)
        self.db_path = db_path
        self.lease_seconds = lease_seconds
        self.fence_run = bool(fence_run)
        self.terminal_fence = bool(terminal_fence)
        self.blockers = []
        self._claims = {}

    def before_publish(self, group, _batch_id):
        destination = str(group.get("id") or "").strip()
        claims = claim_delivery_cards(
            self.run_id,
            self.cards,
            "facebook_group",
            destination,
            self.owner,
            db_path=self.db_path,
            lease_seconds=self.lease_seconds,
            fence_run=self.fence_run,
            terminal_fence=self.terminal_fence,
        )
        selected = list(claims.get("claimed") or [])
        blockers = delivery_claim_blockers(claims)
        if blockers or (selected and len(selected) != len(self.cards)):
            reason = ", ".join(blockers or ["partial_delivery_state"])
            release_delivery_cards(
                self.run_id,
                selected,
                "facebook_group",
                destination,
                self.owner,
                reason,
                db_path=self.db_path,
            )
            self.blockers.append(f"{destination}:{reason}")
            return {"allowed": False, "message": f"delivery guard blocked: {reason}"}
        if not selected:
            return {
                "allowed": False,
                "message": "delivery ledger already succeeded",
                "terminal_status": "published",
            }
        self._claims[destination] = selected
        return {"allowed": True}

    def after_publish(self, group, _batch_id, result):
        destination = str(group.get("id") or "").strip()
        selected = self._claims.pop(destination, [])
        if not selected:
            return
        status = str((result or {}).get("status") or "failed").strip().lower()
        succeeded = status in {"published", "pending"}
        updated = finish_delivery_cards(
            self.run_id,
            selected,
            "facebook_group",
            destination,
            self.owner,
            succeeded=succeeded,
            result=result or {},
            error=None if succeeded else (result or {}).get("message") or status,
            db_path=self.db_path,
        )
        if succeeded:
            mark_items_published(selected, db_path=self.db_path)
        else:
            self.blockers.append(f"{destination}:{status}")
        if updated != len(selected):
            self.blockers.append(f"{destination}:delivery_owner_lost")


def _latch_lane(run_id, owner, lane, snapshot, reason, now, settings, db_path):
    selected = select_news_run_lane(
        run_id,
        owner,
        lane,
        db_path=db_path,
        lease_seconds=settings["lease_seconds"],
        now=now,
    )
    if not selected:
        return {"run_id": run_id, "owner": owner, "lane": None, "action": "busy", "reason": "run_missing"}
    if not selected.get("lane_selected"):
        return _decision_from_record(selected, owner, action="busy")

    stats = {
        "selection_reason": reason,
        "sheet_status": (snapshot or {}).get("status", ""),
        "sheet_row_count": (snapshot or {}).get("row_count"),
        "sheet_usable_count": (snapshot or {}).get("usable_row_count", 0),
    }
    update_news_run_state(
        run_id,
        owner,
        selected["state"],
        db_path=db_path,
        stats=stats,
        lease_seconds=settings["lease_seconds"],
        now=now,
    )
    decision = _decision_from_record(selected, owner, action="selected", reason=reason)
    decision.update(
        {
            "snapshot": snapshot if lane == "primary" else None,
            "lease_seconds": settings["lease_seconds"],
            "heartbeat_seconds": settings["heartbeat_seconds"],
        }
    )
    log_news_event("lane_selected", run_id=run_id, owner=owner, lane=lane, reason=reason, stats=stats)
    return decision


def _decision_from_record(record, owner, action, reason=None):
    return {
        "run_id": record.get("run_id"),
        "owner": owner,
        "lane": record.get("lane"),
        "state": record.get("state"),
        "action": action,
        "reason": reason or record.get("claim_reason") or record.get("selection_reason") or "",
        "deadline": record.get("deadline"),
        "snapshot": None,
        "record": dict(record),
    }


def _orchestration_settings(values):
    merged = dict(DEFAULT_ORCHESTRATION)
    merged.update(values or {})
    merged["lane_policy"] = str(merged.get("lane_policy") or "primary_then_backup").strip().lower()
    merged["primary_timeout_minutes"] = _bounded_int(merged.get("primary_timeout_minutes"), 1, 240, 30)
    merged["poll_interval_seconds"] = _bounded_int(merged.get("poll_interval_seconds"), 1, 300, 60)
    merged["catch_up_window_minutes"] = _bounded_int(merged.get("catch_up_window_minutes"), 1, 1440, 120)
    merged["lease_seconds"] = _bounded_int(merged.get("lease_seconds"), 30, 3600, 300)
    merged["heartbeat_seconds"] = _bounded_int(
        merged.get("heartbeat_seconds"), 1, max(1, merged["lease_seconds"] - 1), 30
    )
    return merged


def _normalized_schedule_times(values):
    result = []
    for value in values or ["07:15", "19:15"]:
        try:
            hour, minute = [int(part) for part in str(value).split(":", 1)]
        except (TypeError, ValueError):
            continue
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            result.append(f"{hour:02d}:{minute:02d}")
    return result or ["07:15", "19:15"]


def _worker_id():
    host = socket.gethostname() or "host"
    return f"{host}:{os.getpid()}:{uuid.uuid4().hex[:12]}"


def _card_item_key(card):
    if not isinstance(card, dict):
        return ""
    return str(card.get("item_key") or card.get("dedupe_key") or "").strip()


def _slot(value):
    result = str(value or "").strip().lower()
    if result not in {"morning", "evening"}:
        raise ValueError("slot must be morning or evening")
    return result


def _aware_datetime(value, default_timezone):
    if isinstance(value, datetime):
        result = value
    else:
        raw = str(value or "").strip()
        if raw.endswith("Z"):
            raw = f"{raw[:-1]}+00:00"
        result = datetime.fromisoformat(raw)
    if result.tzinfo is None:
        result = result.replace(tzinfo=default_timezone)
    return result


def _bounded_int(value, minimum, maximum, default):
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def _safe_error(error):
    return " ".join(str(error or "").split())[:500]
