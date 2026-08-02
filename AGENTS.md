# AGENTS.md

Read these files first:

1. PROJECT_SPEC.md
2. PROJECT_BRIEF.md
3. FILE_MAP.md

When preparing a release, read `RELEASE_WORKFLOW.md` before changing files or running commands.

Do not skip the security check before any commit, push, tag, or release.

Do not scan the whole project unless the task requires it.

<!-- CODEX_PROJECT_LOG_START -->

## Project log

Sử dụng file `PROJECT_LOG.md` để ghi nhận tiến độ tổng hợp của dự án.

Chỉ thêm một dòng log khi hoàn thành một hạng mục công việc có ý nghĩa, đạt một mốc phát triển hoặc kết thúc một phiên làm việc.

Định dạng:

- [YYYY-MM-DD HH:mm] Nội dung cập nhật ngắn gọn.

Yêu cầu:

- Mỗi cập nhật chỉ viết trên một dòng.
- Mỗi dòng tối đa 25 từ.
- Tổng hợp các thay đổi nhỏ có cùng mục tiêu thành một nội dung duy nhất.
- Chỉ ghi kết quả hoặc chức năng có ý nghĩa đối với tiến độ dự án.
- Không ghi riêng từng file đã sửa, từng hàm đã thay đổi hoặc từng lỗi nhỏ đã xử lý.
- Không ghi các thao tác kỹ thuật thông thường như đổi tên biến, sửa định dạng, chỉnh import, sửa chính tả hoặc thay đổi CSS nhỏ.
- Không ghi lại quá trình thử nghiệm, phân tích hoặc các bước trung gian.
- Không ghi log liên tục trong khi đang sửa code.
- Chỉ ghi sau khi hoàn thành một nhiệm vụ, một nhóm nhiệm vụ hoặc kết thúc phiên làm việc.
- Nếu nhiều thay đổi nhỏ cùng phục vụ một chức năng, phải gộp thành một dòng tổng hợp.
- Không ghi chi tiết mã nguồn.
- Không ghi API key, token, mật khẩu hoặc thông tin nhạy cảm.
- Không sửa hoặc xóa các dòng log cũ.
- Không tạo nội dung trùng với các log trước đó.

Ví dụ nên ghi:

- [2026-07-27 16:30] Hoàn thiện chức năng quản lý dự án và tự động tạo file log.
- [2026-07-27 18:10] Hoàn thành giao diện nhập, xem trước và gửi báo cáo Discord.
- [2026-07-27 20:45] Cải thiện độ ổn định của lịch gửi báo cáo tự động.

Ví dụ không nên ghi:

- Sửa màu nút gửi.
- Đổi tên biến project_name.
- Thêm một hàm kiểm tra đường dẫn.
- Sửa lỗi import.
- Chỉnh khoảng cách giao diện.
- Cập nhật một câu thông báo.
- Thử nghiệm Discord Webhook.

Trước khi thêm log mới, hãy tự kiểm tra:

1. Nội dung này có phản ánh một kết quả hoặc mốc tiến độ đáng báo cáo không?
2. Có thể gộp nội dung này với các thay đổi khác trong cùng nhiệm vụ không?
3. Người quản lý dự án có cần biết chi tiết này không?

Nếu nội dung không phản ánh một mốc tiến độ đáng báo cáo thì không được ghi log.

<!-- CODEX_PROJECT_LOG_END -->
