# Kiến trúc Flow Automation Tool

## Phạm vi hiện tại

Đây là modular monolith chạy trên desktop. Milestone khởi tạo cung cấp khung package, cấu hình, tài liệu và cửa sổ PyQt6 tối thiểu. Chưa có chức năng tạo ảnh/video, điều khiển Flow, database schema hoặc job processing; UI không hiển thị dữ liệu giả để mô phỏng các chức năng đó.

## Các lớp và ranh giới

- **UI (`ui/`)**: widget, view model/presenter và điều phối tương tác. Không import hoặc gọi Playwright. Tác vụ dài được gửi qua application service và chạy ngoài UI thread.
- **Application (`application/`)**: use case như quản lý project, import cảnh, duyệt ảnh và điều phối quy trình. Chỉ phụ thuộc domain và các interface.
- **Core/domain (`core/`)**: entity/value model, quy tắc nghiệp vụ và state machine; không phụ thuộc Qt, Playwright hoặc ORM.
- **Jobs (`jobs/`)**: hàng đợi bền vững, scheduler, retry có giới hạn và khôi phục trạng thái. Worker phát tiến độ/kết quả về UI bằng cơ chế signal phù hợp của Qt.
- **Automation (`automation/`)**: một Flow browser adapter duy nhất sở hữu Playwright, locator, selector, thao tác trang và tải tệp. Adapter dùng Chromium persistent context để duy trì phiên đăng nhập; lần đăng nhập đầu do người dùng thực hiện. Không giả định hoặc gọi API Flow không được công bố/hỗ trợ chính thức.
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

