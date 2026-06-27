# Changelog

## v1.0.15 - 2026-06-27

- Đưa link nguồn vào mô tả của từng ảnh Facebook thay vì tạo comment riêng.
- Preview và CLI hiển thị số mô tả ảnh có link nguồn dự kiến.
- Gỡ luồng comment link nguồn để giảm lỗi quyền comment khi đăng Facebook.

## v1.0.14 - 2026-06-25

- Nút Post Facebook thủ công luôn đăng thật và tạo comment link nguồn cho từng ảnh.
- Preview Facebook tiếp tục chỉ mô phỏng, không tạo bài đăng hoặc comment thật.
- Làm rõ checkbox dry-run chỉ áp dụng cho auto publish.

## v1.0.13 - 2026-06-25

- Các nút gửi Telegram, Preview Facebook và Post Facebook dùng lại bộ ảnh render gần nhất.
- Không render lại, không đọc Google Sheet và không fetch tin khi test publish thủ công.
- Thêm quy tắc version rollover: sau `1.0.15` sẽ lên `1.1.0`, rồi tiếp tục `1.1.1`.

## v1.0.12 - 2026-06-25

- Tự comment `Link nguồn: <URL gốc>` vào từng ảnh Facebook sau khi đăng thành công.
- Dry-run hiển thị danh sách comment link nguồn dự kiến mà không gọi API comment.
- Nếu comment link nguồn lỗi, app vẫn giữ bài đã đăng, cập nhật publish ledger và báo cảnh báo.

## v1.0.11 - 2026-06-25

- Khôi phục caption Facebook mặc định về mẫu cũ có tiêu đề buổi sáng/tối và hashtag.
- Giữ cơ chế render `{facebook_title}`, `{date}`, `{datetime}`, `{brief_label}` cho caption tùy chỉnh.
- Tự chuyển caption mặc định ngắn mới về mẫu caption cũ khi load runtime settings.

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
