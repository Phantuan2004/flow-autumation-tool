# Kế hoạch triển khai

Các milestone độc lập theo lát cắt có thể kiểm thử. Chỉ milestone khởi tạo được thực hiện trong phiên này.

## M0 — Khung ứng dụng và môi trường (hiện tại)

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

## M3 — Job queue và nền tảng browser adapter

- Thực thi job ngoài UI thread với progress, cancellation/shutdown có trật tự, retry/backoff và trạng thái bền vững.
- Cô lập toàn bộ Playwright/selector trong Flow adapter; dùng Chromium persistent context trong runtime.
- Hỗ trợ người dùng đăng nhập thủ công và phát hiện trạng thái trang có thể quan sát được.
- Xác minh hành vi bằng kiểm thử adapter có kiểm soát và acceptance trên tài khoản/môi trường được phép; không dựa vào API không chính thức.

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

