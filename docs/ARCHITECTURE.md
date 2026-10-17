# Kiến trúc Flow Automation Tool

## Phạm vi hiện tại

Đây là modular monolith chạy trên desktop. Ứng dụng có cửa sổ PyQt6, Chrome stable với profile riêng và CDP loopback, cùng luồng đăng nhập thủ công. Session checker nhận diện các marker Flow đã quan sát ở dashboard và project editor; nếu tín hiệu đăng nhập xung đột hoặc chưa đủ bằng chứng, trạng thái không được nâng thành authenticated.

Trong một lần kiểm thử UI có người dùng trước đó, đã tạo và tải một ảnh Nano Banana 2.1 x1 16:9 và một video Omni 1.1 Flash x1 720p/8 giây. Image PoC của Milestone 3 đã kiểm chứng thêm một ảnh Nano Banana 2.1 x1 qua `FlowAdapter`: submit một lần, quan sát tile mới, thu thập ảnh, tải qua menu 1K và xác minh JPEG 1376×768. Job/asset cuối được ghi vào SQLite sau xác minh; hash và đường dẫn asset khớp file hiện có. Lần tải đầu không hoàn tất nên job chuyển `RECONCILIATION_REQUIRED`; tile được đối chiếu và chỉ thao tác tải được tiếp tục, không gửi lại generation. Đây là PoC có giám sát với đối soát thủ công, không phải đảm bảo batch hoặc tự phục hồi.

Domain models và enum trong `core/` không phụ thuộc framework. `persistence/` dùng SQLAlchemy 2.x, transaction ngắn theo repository call, SQLite foreign-key enforcement và schema version 1 qua `PRAGMA user_version`. State machine kiểm tra chuyển trạng thái job/scene, yêu cầu kết quả verified trước khi job thành công, và repository ghi JobEvent cùng cập nhật scene trong một transaction. `FlowImagePocService` được cấp repository qua `bootstrap.create_flow_repository()`; UI không gọi database. Đây là service PoC, chưa phải queue worker hoặc scheduler.

## Các lớp và ranh giới

- **UI (`ui/`)**: widget, view model/presenter và điều phối tương tác. Không import hoặc gọi Playwright. Tác vụ dài được gửi qua application service và chạy ngoài UI thread.
- **Application (`application/`)**: use case như quản lý project, import cảnh, duyệt ảnh và điều phối quy trình. Chỉ phụ thuộc domain và các interface.
- **Core/domain (`core/`)**: framework-independent dataclass models cho project, scene, generation job, asset, reference và job event; enum trạng thái và state machine. Chỉ module này sở hữu định nghĩa trạng thái dùng chung.
- **Jobs (`jobs/`)**: hàng đợi bền vững, scheduler, retry có giới hạn và khôi phục trạng thái. Worker phát tiến độ/kết quả về UI bằng cơ chế signal phù hợp của Qt.
- **Automation (`automation/`)**: Flow browser adapter sở hữu Playwright, locator, selector, thao tác trang và tải tệp. `BrowserProvider` mở Chrome stable riêng với app-specific profile, loopback CDP và OS-level profile lock; người dùng tự xác thực. Browser connectivity không đồng nghĩa với workspace authentication. Chỉ một marker workspace đã xác minh mới cho phép `AUTHENTICATED`; thao tác UI chưa xác minh fail closed. Adapter hỗ trợ PoC ảnh x1 bằng semantic controls và đã được chạy live một lần; video, project navigation tự động và batch vẫn unsupported. Không giả định hoặc gọi API Flow không được công bố/hỗ trợ chính thức.
- **Pipelines (`pipelines/`)**: xử lý ảnh/video và quy tắc đầu vào/đầu ra; gọi adapter qua interface, không chứa locator.
- **Persistence (`persistence/`)**: SQLAlchemy 2.x, SQLite, versioned schema, foreign-key enforcement, transaction-scoped session scope và `FlowRepository`. Domain/application không thao tác trực tiếp với session ORM. Runtime DB mặc định ở `runtime/flow_automation.sqlite3`; schema version 1 được khởi tạo từ version 0, và database có version mới hơn bị từ chối thay vì downgrade.
- **Storage (`storage/`)**: lưu asset/media trên filesystem, metadata/thumbnails và quy tắc đặt tên; database giữ tham chiếu và trạng thái, không thay thế nội dung tệp.
- **Diagnostics (`diagnostics/`)**: logging chuẩn Python, log có ngữ cảnh job/project, chẩn đoán và thông tin phiên bản; không ghi credential, cookie hoặc token.

Các dependency hướng vào domain/application interfaces. Bootstrap sẽ cấu hình logging, persistence, services và UI. Giữ một tiến trình ứng dụng; không tách microservice.

## Mô hình workflow dự kiến

Project chứa các scene được import từ CSV/JSON, assets tham chiếu và các job. Repository hiện hỗ trợ CRUD project/reference/scene/job/asset, gắn reference với scene, chọn một ảnh verified cho mỗi scene, ghi event cùng transition, truy vấn job resumable/reconciliation và thống kê tiến độ. Unique constraints bảo vệ scene number theo project, đường dẫn asset theo project và tối đa một lựa chọn ảnh trên scene. Repository kiểm tra sự nhất quán project/scene/job trước khi ghi. PoC image service lưu local project/scene/job trước khi gửi yêu cầu Flow, sau đó tạo asset verified khi file đã tải và được xác nhận bởi `MediaVerifier`.

Job có trạng thái tường minh và event history. `PENDING`/`RETRY_WAIT` có thể được truy vấn để tiếp tục; `INTERRUPTED`/`RECONCILIATION_REQUIRED` được truy vấn riêng và không được requeue nếu thiếu xác nhận `safe_to_retry`. `SUCCEEDED` yêu cầu `result_verified=True`; đây là xác nhận của caller/domain boundary, không tự kiểm tra file media. Retry count có giới hạn; lỗi transition hoặc lỗi ghi event rollback cả cập nhật job/scene. Scheduler/worker chưa thực hiện resume tự động sau restart; caller cần đối soát kết quả bên ngoài trước khi tiếp tục. Trong phạm vi image PoC, `FlowImagePocService` và bootstrap/repository đủ để ghi trạng thái tối thiểu; không có nút UI hoặc worker để tránh mở rộng PoC thành workflow vận hành.

## Tác vụ nền và giao tiếp UI

UI thread chỉ phục vụ event loop và cập nhật widget. Application service gửi công việc tới worker/queue; worker không truy cập trực tiếp widget. Tiến độ, log hiển thị, kết quả và lỗi được truyền qua signal hoặc message có kiểu. Shutdown có trật tự để ghi trạng thái job trước khi thoát.

## Dữ liệu và runtime

SQLite, profile Chromium, media/assets và log được đặt dưới `runtime/` theo mặc định và bị `.gitignore` loại trừ. Cấu hình mặc định nằm ở `config/defaults.toml`; giá trị môi trường hoặc cấu hình người dùng có thể override về sau mà không nhúng bí mật vào repository. Thư mục runtime không được commit.

## Kiểm thử

- **Unit**: domain transitions, validators, retry policy và logic pipeline thuần.
- **Integration**: repository SQLite, asset storage và application service với dependency được kiểm soát.
- **Acceptance**: workflow UI/browser end-to-end trên môi trường được phép, dữ liệu và tài khoản kiểm thử riêng.
- **Smoke**: package import, CLI entry point và khả năng khởi tạo cửa sổ offscreen.

Kiểm thử domain/repository thông thường không kết nối Flow thật, dùng SQLite tạm và không phụ thuộc cookie/profile người dùng. Khởi tạo thủ công bằng `Database.from_path(Path(...))` rồi `initialize()` hoặc context manager; xem README để biết ví dụ và lệnh test tập trung.
