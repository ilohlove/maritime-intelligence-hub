# Quy trình Google Sheets-first

Google Sheets là nguồn chính cho mỗi bản tin theo giờ Việt Nam. Agent bên ngoài cập nhật dữ liệu bài viết tại `A:K`; app chỉ dùng nguồn thu thập/AI dự phòng khi snapshot Sheet không hoàn tất hoặc không thể xác minh trước deadline.

## Khung giờ

- Buổi sáng: bắt đầu theo dõi lúc `07:15`, mục tiêu `07:30`.
- Buổi tối: bắt đầu theo dõi lúc `19:15`, mục tiêu `19:30`.
- Nếu app đã quan sát đúng lượt đang chạy (`L1` đúng và `M1` trống), hard deadline được gia hạn thêm 5 phút, tới `07:35` hoặc `19:35`.
- Khi một lane đã được chọn, app không đổi lane và không trộn dữ liệu Sheet với dữ liệu dự phòng trong cùng lượt.

## Marker bắt buộc

| Ô | Ý nghĩa |
| --- | --- |
| `L1` | Thời điểm bắt đầu; dùng ISO-8601 có múi giờ hoặc `HH:MM` |
| `M1` | Thời điểm hoàn tất, cùng định dạng với `L1`; phải để trống khi đang chạy |
| `N1:Q1` | Chỉ dùng chẩn đoán; thiếu hoặc sai không chặn snapshot hợp lệ |

## Thứ tự ghi bắt buộc

1. Trước khi thu thập, ghi `L1` của đúng lượt và xóa `M1`.
2. Thu thập, dịch và kiểm tra toàn bộ tập bài ở ngoài Sheet.
3. Thay toàn bộ dữ liệu cũ trong `A2:K`; không nối thêm vào lượt trước.
4. Mỗi dòng phải có tiêu đề, tóm tắt, tác động bằng tiếng Việt, tên nguồn và URL tuyệt đối `http://` hoặc `https://`.
5. Có thể cập nhật `N1:Q1` để phục vụ chẩn đoán.
6. Chỉ sau khi toàn bộ `A:K` đã hoàn tất, ghi `M1` ở thao tác cuối cùng.

Không được giữ `M1` cũ khi bắt đầu lượt mới và không được ghi `M1` trước khi hoàn tất mọi dòng.

## Cách app xác minh

- App chỉ chọn primary sau hai lần đọc giống nhau cách nhau 10 giây và toàn bộ dòng đều hợp lệ.
- App lưu riêng `content_hash(A:K)` để chống phát lại nội dung cũ và `snapshot_hash(A:K+L:M)` để kiểm tra độ ổn định.
- Với marker `HH:MM`, nội dung trùng `content_hash` lượt trước bị từ chối dù `M1` đã đổi. Nếu app không quan sát được giai đoạn `M1` trống, snapshot còn phải có content hash mới và ít nhất một bài thuộc cửa sổ tin kể từ slot trước.
- Snapshot có `M1` nằm trong deadline được phép dùng đúng một cửa sổ 10 giây sau deadline để hoàn tất lần đọc xác minh; cửa sổ này không cho phép `M1` hoàn tất muộn.
- Snapshot rỗng, thiếu trường, sai giờ, thay đổi giữa hai lần đọc hoặc hoàn tất sau hard deadline làm toàn bộ primary thất bại; app chuyển sang lane dự phòng.
- Primary giữ nguyên thứ tự và nội dung Sheet, không lọc nguồn Việt Nam, không giới hạn số bài, không AI viết lại, không xếp hạng backup và không semantic-dedupe.
- App chỉ bỏ bài đã đăng trùng chính xác hoặc URL lặp trong cùng Sheet; diagnostics ghi row index và lý do.

## Ví dụ

ISO đầy đủ:

| Ô | Giá trị |
| --- | --- |
| `L1` | `2026-08-03T07:15:00+07:00` |
| `M1` | `2026-08-03T07:22:00+07:00` |

Tương thích `HH:MM`:

| Ô | Giá trị |
| --- | --- |
| `L1` | `07:15` |
| `M1` | `07:22` |

ISO đầy đủ vẫn là lựa chọn an toàn hơn vì `HH:MM` không tự chứng minh được ngày tuyệt đối.
