# Kiến trúc Flow Automation Tool

## Phạm vi hiện tại

Đây là modular monolith chạy trên desktop. Ứng dụng có cửa sổ PyQt6, Chrome stable với profile riêng và CDP loopback, cùng luồng đăng nhập thủ công. Session checker nhận diện các marker Flow đã quan sát ở dashboard và project editor; nếu tín hiệu đăng nhập xung đột hoặc chưa đủ bằng chứng, trạng thái không được nâng thành authenticated.

Trong một lần kiểm thử UI có người dùng, đã tạo và tải một ảnh Nano Banana 2.1 x1 16:9; tiếp đó đã tạo và tải một video Omni 1.1 Flash x1 720p, 8 giây từ ảnh đó. Video đã được xác minh H.264 1280×720, 24 fps, 8 giây. Đây là kiểm chứng thao tác UI đơn lẻ, không phải hỗ trợ tổng quát hay batch. `FlowAdapter` hiện có mã tạo/chờ/thu thập ảnh với unit test, nhưng chưa chạy live end-to-end; adapter tạo video và tải asset chưa tích hợp. Job queue bền vững, database schema/repositories và phục hồi job chưa triển khai.

## Các lớp và ranh giới

- **UI (`ui/`)**: widget, view model/presenter và điều phối tương tác. Không import hoặc gọi Playwright. Tác vụ dài được gửi qua application service và chạy ngoài UI thread.
- **Application (`application/`)**: use case như quản lý project, import cảnh, duyệt ảnh và điều phối quy trình. Chỉ phụ thuộc domain và các interface.
- **Core/domain (`core/`)**: entity/value model, quy tắc nghiệp vụ và state machine; không phụ thuộc Qt, Playwright hoặc ORM.
- **Jobs (`jobs/`)**: hàng đợi bền vững, scheduler, retry có giới hạn và khôi phục trạng thái. Worker phát tiến độ/kết quả về UI bằng cơ chế signal phù hợp của Qt.
- **Automation (`automation/`)**: Flow browser adapter sở hữu Playwright, locator, selector, thao tác trang và tải tệp. `BrowserProvider` mở Chrome stable riêng với app-specific profile, loopback CDP và OS-level profile lock; người dùng tự xác thực. Browser connectivity không đồng nghĩa với workspace authentication. Chỉ một marker workspace đã xác minh mới cho phép `AUTHENTICATED`; thao tác UI chưa xác minh fail closed. Không giả định hoặc gọi API Flow không được công bố/hỗ trợ chính thức.
- **Pipelines (`pipelines/`)**: xử lý ảnh/video và quy tắc đầu vào/đầu ra; gọi adapter qua interface, không chứa locator.
- **Persistence (`persistence/`)**: SQLAlchemy 2.x, SQLite, session/unit-of-work và repository implementations. Application/domain không thao tác trực tiếp với session ORM.
- **Storage (`storage/`)**: lưu asset/media trên filesystem, metadata/thumbnails và quy tắc đặt tên; database giữ tham chiếu và trạng thái, không thay thế nội dung tệp.
- **Diagnostics (`diagnostics/`)**: logging chuẩn Python, log có ngữ cảnh job/project, chẩn đoán và thông tin phiên bản; không ghi credential, cookie hoặc token.

Các dependency hướng vào domain/application interfaces. Bootstrap sẽ cấu hình logging, persistence, services và UI. Giữ một tiến trình ứng dụng; không tách microservice.

## Mô hình workflow dự kiến

Project chứa các scene được import từ CSV/JSON, assets tham chiếu và các job. Một scene có nhiều image variants; người dùng chọn một variant được duyệt trước khi tạo video. Mỗi job/scene có trạng thái tường minh và chuyển trạng thái được kiểm tra tại domain boundary. Các trạng thái chi tiết và quy tắc chuyển tiếp sẽ được chốt cùng milestone domain, trước khi persistence phụ thuộc vào chúng.

Job được lưu bền vững trước khi chạy. Worker có thể retry lỗi tạm thời với giới hạn/backoff; lỗi đầu vào hoặc lỗi không thể retry phải surfaced cho người dùng và diagnostics. Khi ứng dụng khởi động lại, scheduler tìm job chưa kết thúc, kiểm tra trạng thái thực tế của asset/output rồi mới tiếp tục; không coi việc phát sinh lại một thao tác web là an toàn nếu adapter chưa xác nhận idempotency.

## Tác vụ nền và giao tiếp UI

UI thread chỉ phục vụ event loop và cập nhật widget. Application service gửi công việc tới worker/queue; worker không truy cập trực tiếp widget. Tiến độ, log hiển thị, kết quả và lỗi được truyền qua signal hoặc message có kiểu. Shutdown có trật tự để ghi trạng thái job trước khi thoát.

## Dữ liệu và runtime

SQLite, profile Chromium, media/assets và log được đặt dưới `runtime/` theo mặc định và bị `.gitignore` loại trừ. Cấu hình mặc định nằm ở `config/defaults.toml`; giá trị môi trường hoặc cấu hình người dùng có thể override về sau mà không nhúng bí mật vào repository. Thư mục runtime không được commit.

## Kiểm thử

- **Unit**: domain transitions, validators, retry policy và logic pipeline thuần.
- **Integration**: repository SQLite, asset storage và application service với dependency được kiểm soát.
- **Acceptance**: workflow UI/browser end-to-end trên môi trường được phép, dữ liệu và tài khoản kiểm thử riêng.
- **Smoke**: package import, CLI entry point và khả năng khởi tạo cửa sổ offscreen.

Kiểm thử thông thường không kết nối Flow thật và không phụ thuộc cookie/profile người dùng.
