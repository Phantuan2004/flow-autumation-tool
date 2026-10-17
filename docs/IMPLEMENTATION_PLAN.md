# Kế hoạch triển khai

## Milestone người dùng 2 — Manual Browser Authentication và kiểm chứng Flow UI (hoàn tất)

- Mở Chrome stable bằng profile riêng của ứng dụng qua CDP loopback; người dùng tự đăng nhập. Không dùng profile Chrome cá nhân, sao chép session hoặc tự động nhập thông tin xác thực.
- Xác minh truy cập dashboard và project editor bằng tín hiệu UI quan sát được; các trạng thái không đủ bằng chứng vẫn fail closed.
- Kiểm thử trực tiếp một ảnh Nano Banana 2.1 x1 16:9, tải lựa chọn 1K/kích thước gốc; file JPEG thực đo 1376×768.
- Kiểm thử trực tiếp một video từ ảnh đó với Omni 1.1 Flash, x1, 720p, 8 giây; tải lựa chọn 720p/kích thước gốc. `ffprobe` xác nhận H.264, 1280×720, 24 fps, thời lượng 8 giây.
- Kết quả xác minh là một lần chạy UI cho mỗi loại media trên môi trường Windows/tài khoản hiện tại; không phải bảo đảm mọi model/tài khoản, chưa phải kiểm thử batch hoặc adapter end-to-end.
- Các giới hạn còn lại (khó khăn cho production, không chặn việc đóng milestone khám phá này): chưa nối adapter tạo/chờ/thu thập/tải media vào UI hoặc job worker; chưa có scheduler/worker để thực thi job bền vững, retry/cancel có điều phối hoặc phục hồi tự động; chưa kiểm thử đa nền tảng. Theo dõi trong M3–M6 bên dưới.

Các milestone roadmap bên dưới mô tả các phần sản phẩm độc lập; nhãn M2 cho import scene/assets trong roadmap không phải Milestone người dùng 2 ở trên.

Các milestone độc lập theo lát cắt có thể kiểm thử. M0 đã hoàn tất; Milestone người dùng 2 (xác thực thủ công và kiểm chứng Flow UI một cảnh) đã hoàn tất. Các phần production trong M3–M6 vẫn cần triển khai và kiểm thử riêng.

## M0 — Khung ứng dụng và môi trường (hoàn tất)

- Tạo package Python, cấu hình build/dependencies, `.gitignore`, README và cấu hình mặc định.
- Ghi lại kiến trúc modular monolith và kế hoạch triển khai.
- Tạo cửa sổ PyQt6 tối thiểu, CLI entry point và smoke test.
- Hoàn tất khi cài được project trong venv, import/entry point hợp lệ và smoke test chạy thành công.

## M1 — Domain và lưu trữ bền vững (core hoàn tất)

- **Đã triển khai:** framework-independent dataclass/enums cho project, scene, reference, asset, generation job và job event; trạng thái job/scene tập trung trong core state machine.
- **Đã triển khai:** SQLite/SQLAlchemy 2.x schema v1, SQLite foreign keys/busy timeout, `PRAGMA user_version`, transaction scope và repository cho project, scene, reference, asset, job, event, chọn ảnh, trạng thái và tiến độ.
- **Đã kiểm soát:** job chỉ thành công khi caller xác nhận kết quả verified; ảnh chỉ được duyệt/chọn khi asset ảnh của scene đã verified; retry bị giới hạn; job gián đoạn/đối soát không được gửi lại an toàn nếu chưa xác nhận.
- **Đã kiểm thử:** unit state machine và integration trên database tạm cho quan hệ, rollback transaction, nhiều scene/job/ảnh, chọn ảnh duy nhất, fail/retry, đối soát, mở lại database và version tương lai.
- **Còn lại ngoài core milestone này:** bootstrap/UI chưa khởi tạo repository; chưa có migration v1→v2 vì chưa tồn tại schema cũ; chưa có scheduler/worker/resume tự động. `result_verified` là xác nhận từ caller, không đồng nghĩa với việc repository tự xác minh file.

## M2 — Import scene và quản lý assets

- Validate CSV/JSON, encoding, cột bắt buộc, scene ID trùng và prompt lỗi; báo lỗi theo dòng, không bỏ qua âm thầm.
- Quản lý ảnh tham chiếu nhân vật/bối cảnh/phong cách, metadata và thumbnail.
- Tạo project/scene cơ bản từ UI; kiểm thử import và storage.

## M3 — Tích hợp tối thiểu và Flow image PoC (phạm vi PoC hoàn tất có giám sát)

- **Đã hoàn tất:** Chrome stable riêng với profile ứng dụng và CDP loopback; profile lock; giao diện Open Browser / Check Connection; người dùng tự đăng nhập; trạng thái `AUTHENTICATED`, `UNAUTHENTICATED`, `AUTH_REQUIRED`, `UNKNOWN` và `BROWSER_UNAVAILABLE`.
- **Đã kiểm thử:** Chrome stable/CDP trên Windows với fixture HTTP cục bộ; khởi động, attach, khóa profile và shutdown; unit test trạng thái và lỗi. Chưa có kiểm thử Google sign-in tự động.
- **Tiêu chí PoC ban đầu:** gửi đúng một yêu cầu ảnh được cho phép, theo dõi kết quả, tải file, xác minh nội dung và lưu trạng thái tối thiểu đã được chứng minh trên Flow thật. Chỉ một generation được gửi; không có lần gửi lại.
- **Bằng chứng live:** `FlowAdapter` xác minh phiên, đặt Nano Banana 2.1/image/x1, gửi một prompt, chờ tile mới và thu thập ảnh. File JPEG 1K được Pillow xác minh ở 1376×768, 228,746 byte; hash, MIME, kích thước và trạng thái file khớp metadata trong SQLite.
- **Lỗi và đối soát:** lần tải đầu không hoàn tất nên job chuyển `RECONCILIATION_REQUIRED`. Tile hiện hữu được kiểm tra và chỉ tiếp tục thao tác tải, không gửi lại generation. Job event trong DB ghi `PENDING → RUNNING → RECONCILIATION_REQUIRED → SUCCEEDED`; trạng thái cuối chỉ được ghi sau khi file được xác minh. Việc đối soát là thủ công, không phải phục hồi tự động.
- **Tích hợp tối thiểu:** `FlowImagePocService` điều phối adapter/verifier và dùng repository được tạo bởi `bootstrap.create_flow_repository()`; test service thành công/lỗi dùng adapter giả và SQLite tạm. Luồng PoC chưa có nút UI: prompt/đồng ý chi phí và mở project vẫn được kiểm soát thủ công. UI trigger hoặc worker không phải tiêu chí của PoC ảnh ban đầu và được để lại cho milestone sản phẩm sau.
- **Giới hạn xác minh:** đã chạy một lần tạo ảnh thật; không tuyên bố adapter tạo video đã xác minh. Video trước đây được thử trực tiếp trong Milestone người dùng 2, nhưng `FlowAdapter.create_video()` vẫn unsupported. Đây là kết quả PoC có giám sát, không phải workflow tự phục hồi hoặc sản xuất.
- **Kiểm thử tự động:** unit/integration kiểm tra service, verifier, trạng thái lỗi và download UI trên Chrome fixture cục bộ; không thay cho lần chạy Flow thật. Không chạy batch và không dùng API Flow không chính thức.

Các tính năng sản phẩm còn lại như UI quản lý scene/gallery, batch, video adapter, scheduler/worker, retry/cancel điều phối và phục hồi tự động thuộc các lát cắt tiếp theo; không phải điều kiện để chứng minh image PoC đơn này.

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
