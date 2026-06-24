# Changelog

## v1.0.10 - 2026-06-24

- Chỉ render Sheet mode khi ô L1 khớp đúng khung sáng/tối hiện tại.
- Không render lại tin cũ: Sheet mode bỏ qua các item đã có trong published ledger.
- Nếu Sheet đúng khung giờ nhưng chưa có tin mới, GUI sẽ chờ 60 giây rồi kiểm tra lại thay vì tạo ảnh cũ.
- Cập nhật lịch mặc định sang 07:30 và 19:30.

## v1.0.9 - 2026-06-23

- Sửa lỗi lấy ảnh khi Source URL từ Google Sheet hoặc brief đang ở dạng Markdown/HTML link.
- Sửa RSS autodiscovery của Safety4Sea để không chọn nhầm iCal/event feed và chỉ nhận RSS/Atom hợp lệ.
- Cập nhật caption Facebook mặc định bằng tiếng Việt thân thiện hơn và tự chuyển caption mặc định cũ sang mẫu mới.

## v1.0.8 - 2026-06-21

- Add Facebook Page publishing with dry-run preview, Page checks, multi-photo posting, and publish ledger metadata.
- Replace the default Facebook caption with the Vietnamese Maritime Brief morning/evening format and hashtags.
- Improve Facebook preview/post diagnostics so card generation, token, permission, and publish safety errors are shown without Python tracebacks.

## v1.0.7 - 2026-06-18

- Wait for Google Sheet L1 to match the current Vietnam morning or evening brief before generating Sheet-mode cards.
- Make Sheet mode include every valid Google Sheet item and ignore image-card limits, published filters, and duplicate removal.
- Add Vietnamese morning/evening brief labels to Telegram intro text and improve article image extraction diagnostics.

## v1.0.6 - 2026-06-15

- Make loop mode image generation honor the selected visual source, including Google Sheets.
- Send Telegram cards from the selected source instead of always using the latest app brief.
- Rename send-card actions to make selected-source behavior clear.

## v1.0.5 - 2026-06-14

- Make the visual card generator use the selected source mode so Sheet mode reads from the configured Google Sheet link.
- Show Google Sheet source URL, CSV export URL, and loaded row count in combined source output.
- Add Sheet mode validation for empty or unusable Google Sheet links.

## v1.0.4 - 2026-06-14

- Improve App mode empty-article diagnostics with explicit database, AI summary, freshness, published, and duplicate filter messages.
- Show App mode database path and candidate counts in combined source output.
- Use the active App mode database consistently when checking already-published items.

## v1.0.0 - 2026-06-13

- Initialize Maritime Intelligence Hub as a real project from the desktop app template.
- Define planning-first scope for source import, crawler pipeline, AI processing, and brief generation.
- Defer complex GUI work until the core pipeline is validated.
- Add CLI source validation, P1 fetch planning, readiness brief generation, and source master self-tests.
- Add SQLite-backed MVP pipeline with RSS live fetch, HTML dry-run, scoring, mock AI summaries, and Markdown/JSON brief outputs.
- Add approved HTML metadata fetch, configurable AI provider with mock fallback, trend-aware hotness scoring, Google Trends CSV/RSS ingestion, and sectioned hot maritime brief output.
- Add visual brief cards, Telegram publishing controls, scheduled GUI operation, timezone selection, and bundled Playwright Chromium build support.
