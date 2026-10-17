# Kế hoạch triển khai

## Milestone người dùng 2 — Manual Browser Authentication và kiểm chứng Flow UI (hoàn tất)

- Mở Chrome stable bằng profile riêng của ứng dụng qua CDP loopback; người dùng tự đăng nhập. Không dùng profile Chrome cá nhân, sao chép session hoặc tự động nhập thông tin xác thực.
- Xác minh truy cập dashboard và project editor bằng tín hiệu UI quan sát được; các trạng thái không đủ bằng chứng vẫn fail closed.
- Kiểm thử trực tiếp một ảnh Nano Banana 2.1 x1 16:9, tải lựa chọn 1K/kích thước gốc; file JPEG thực đo 1376×768.
- Kiểm thử trực tiếp một video từ ảnh đó với Omni 1.1 Flash, x1, 720p, 8 giây; tải lựa chọn 720p/kích thước gốc. `ffprobe` xác nhận H.264, 1280×720, 24 fps, thời lượng 8 giây.
- Kết quả xác minh là một lần chạy UI cho mỗi loại media trên môi trường Windows/tài khoản hiện tại; không phải bảo đảm mọi model/tài khoản, chưa phải kiểm thử batch hoặc adapter end-to-end.
- Các giới hạn còn lại (khó khăn cho production, không chặn việc đóng milestone khám phá này): chưa nối adapter tạo/chờ/thu thập/tải media vào UI hoặc job worker; chưa có queue bền vững, retry/cancel an toàn hoặc phục hồi; chưa kiểm thử đa nền tảng. Theo dõi trong M3–M6 bên dưới.

Các milestone roadmap bên dưới mô tả các phần sản phẩm độc lập; nhãn M2 cho import scene/assets trong roadmap không phải Milestone người dùng 2 ở trên.

Các milestone độc lập theo lát cắt có thể kiểm thử. M0 đã hoàn tất; Milestone người dùng 2 (xác thực thủ công và kiểm chứng Flow UI một cảnh) đã hoàn tất. Các phần production trong M3–M6 vẫn cần triển khai và kiểm thử riêng.

## M0 — Khung ứng dụng và môi trường (hoàn tất)

- Tạo package Python, cấu hình build/dependencies, `.gitignore`, README và cấu hình mặc định.
- Ghi lại kiến trúc modular monolith và kế hoạch triển khai.
- Tạo cửa sổ PyQt6 tối thiểu, CLI entry point và smoke test.
- Hoàn tất khi cài được project trong venv, import/entry point hợp lệ và smoke test chạy thành công.

## M1 — Domain và lưu trữ bền vững

- Chốt entity project, scene, reference asset, image variant, approval, video output và job.
- Định nghĩa state machine và kiểm tra chuyển trạng thái bất hợp lệ.
- Thiết kế schema SQLite/SQLAlchemy, repository và migration strategy.
- Kiểm thử unit state transitions và integration repository; xác minh khôi phục sau restart.

## M2 — Import scene và quản lý assets

- Validate CSV/JSON, encoding, cột bắt buộc, scene ID trùng và prompt lỗi; báo lỗi theo dòng, không bỏ qua âm thầm.
- Quản lý ảnh tham chiếu nhân vật/bối cảnh/phong cách, metadata và thumbnail.
- Tạo project/scene cơ bản từ UI; kiểm thử import và storage.

## M3 — Job queue và browser adapter (nền tảng có; production integration còn lại)

- **Đã hoàn tất:** Chrome stable riêng với profile ứng dụng và CDP loopback; profile lock; giao diện Open Browser / Check Connection; người dùng tự đăng nhập; trạng thái `AUTHENTICATED`, `UNAUTHENTICATED`, `AUTH_REQUIRED`, `UNKNOWN` và `BROWSER_UNAVAILABLE`.
- **Đã kiểm thử:** Chrome stable/CDP trên Windows với fixture HTTP cục bộ; khởi động, attach, khóa profile và shutdown; unit test trạng thái và lỗi. Chưa có kiểm thử Google sign-in tự động.
- **Đã quan sát trực tiếp:** người dùng đăng nhập thủ công bằng `open-flow`, vào dashboard và mở project; dashboard và project-editor markers được kiểm tra. Một ảnh Nano Banana 2.1 x1 16:9 đã được tạo, hiển thị và tải JPEG 1K (1376×768). Sau đó một video Omni 1.1 Flash x1, 720p, 8 giây từ ảnh này được tạo và tải; `ffprobe` xác nhận H.264 1280×720, 24 fps, 8 giây.
- **Đã triển khai, unit-test nhưng chưa chạy live bằng adapter:** submit một ảnh, chờ tile ảnh tải xong, thu thập tham chiếu ảnh; adapter dừng an toàn và chặn gửi lại khi submit trước chưa đối soát.
- **Còn lại cho production:** adapter tạo/chờ/thu thập ảnh và tạo video chưa được nối end-to-end vào thao tác người dùng trong UI/job worker. Download qua UI đã được kiểm thử trực tiếp một lần cho cả ảnh và video, nhưng `FlowAdapter.download_asset()` chưa được triển khai. Không chạy batch cho đến khi adapter/UI và lưu file được kiểm thử end-to-end.
- Queue bền vững, retry/cancel và phục hồi job vẫn chưa triển khai; không dùng API Flow không chính thức.

## M4 — Tạo ảnh, gallery và duyệt

- Tạo image-generation job theo batch qua adapter; giới hạn concurrency phù hợp và lưu output/metadata.
- Gallery variants theo scene, chọn/duyệt ảnh rõ ràng và bền vững.
- Thử nghiệm retry, lỗi một phần của batch và khôi phục ứng dụng.

## M5 — Tạo video và tải output

- Chỉ tạo video từ ảnh đã duyệt và prompt gắn với scene.
- Tải file qua browser adapter, xác minh output, đặt tên ổn định và lưu đúng project/scene.
- Thể hiện tiến độ, lỗi, retry và khả năng tiếp tục sau gián đoạn.

## M6 — Hoàn thiện vận hành và phát hành

- Bổ sung logging/diagnostics có cấu trúc, export log an toàn và thông báo lỗi dễ hiểu.
- Acceptance test workflow batch 50–60 cảnh, restart recovery, dữ liệu lớn và failure modes.
- Rà soát quyền riêng tư của profile/log/media, tài liệu người dùng, đóng gói desktop và quy trình cập nhật.
