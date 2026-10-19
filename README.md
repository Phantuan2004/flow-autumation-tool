# Flow Automation Tool

Ứng dụng desktop Python/PyQt6 để quản lý quy trình sản xuất video theo batch trên giao diện web Veo 3 Flow. Image PoC Milestone 3 đã gửi một yêu cầu live Nano Banana 2.1 x1, nhận diện kết quả, tải JPEG 1K, xác minh 1376×768 và lưu job/asset trong SQLite. Lần tải đầu cần đối soát thủ công; chỉ tiếp tục tải từ kết quả hiện có, không gửi lại generation. PoC không có nút UI/worker, không phải workflow batch hoặc phục hồi tự động; video adapter chưa triển khai. Chi tiết và giới hạn tại [docs/FLOW_COMPATIBILITY.md](./docs/FLOW_COMPATIBILITY.md) và [docs/IMPLEMENTATION_PLAN.md](./docs/IMPLEMENTATION_PLAN.md).

## Yêu cầu

- Python 3.12 trở lên (khuyến nghị Python 3.13).
- Windows, macOS hoặc Linux có môi trường đồ họa Qt để chạy ứng dụng.
- Google Chrome stable đã cài đặt. Khả năng browser lifecycle hiện chỉ được kiểm thử trên Windows.

## Cài đặt môi trường phát triển

Từ thư mục gốc dự án:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Nếu PowerShell không cho phép kích hoạt script, có thể gọi trực tiếp:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Cài Chromium cho integration test dùng browser fixture:

```powershell
python -m playwright install chromium
```

## Chạy ứng dụng

```powershell
flow-automation
```

Hoặc:

```powershell
python -m flow_automation
```

Để xác nhận entry point CLI mà không mở cửa sổ:

```powershell
python -m flow_automation.main --version
```

Mở ứng dụng, chọn **Open Browser**, đăng nhập Google thủ công trong Chrome riêng của ứng dụng rồi quay lại chọn **Check Connection**. CLI tương đương để chỉ mở/đóng browser:

```powershell
flow-automation --open-flow
```

Ứng dụng mở một tiến trình Google Chrome stable riêng với profile tại `runtime/browser-profile-manual-chrome` và kết nối qua CDP chỉ bind vào `127.0.0.1`. Đây không phải profile Chrome cá nhân và không chia sẻ phiên đăng nhập với Chrome thông thường. Không chạy đồng thời tiến trình khác dùng profile này. Ứng dụng không nhận hoặc lưu thông tin đăng nhập; hãy bảo vệ thư mục profile và không chia sẻ/commit nó. Profile được giữ lại khi khởi động lại. URL Flow được cấu hình tại `[flow].url` trong `config/defaults.toml`.

`Check Connection` phân biệt `AUTHENTICATED`, `UNAUTHENTICATED`, `AUTH_REQUIRED`, `UNKNOWN` và `BROWSER_UNAVAILABLE`. Truy cập dashboard được xác minh bằng nút **Dự án mới**; trong project editor, các tín hiệu prompt, nút bắt đầu và cài đặt ảnh cũng đã được xác minh. Một lần tạo ảnh trực tiếp bằng UI đã cho kết quả hiển thị 1376×768 với Nano Banana 2.1, x1, 16:9. Đây không phải xác minh quyền dùng mọi tính năng. Xem [docs/FLOW_COMPATIBILITY.md](./docs/FLOW_COMPATIBILITY.md) để phân biệt kiểm thử trực tiếp và phần adapter chưa kiểm chứng live.

Người dùng đã xác nhận đăng nhập thủ công bằng `open-flow` và vào được dashboard trong profile riêng của ứng dụng. Nếu Google hiển thị lại cảnh báo bảo mật, dừng thử; không chuyển profile/cookie hoặc cố né cơ chế bảo vệ. Phiên này tách biệt với Chrome thông thường.

## Chạy kiểm thử

```powershell
python -m pytest
```

Smoke test chạy Qt ở chế độ offscreen khi tạo cửa sổ kiểm thử, vì vậy không cần desktop hiển thị cho bước kiểm tra này. Kiểm tra tương thích Flow và trạng thái xác minh được ghi trong [docs/FLOW_COMPATIBILITY.md](./docs/FLOW_COMPATIBILITY.md). Hướng dẫn đăng nhập và giới hạn bảo mật nằm trong [docs/USER_GUIDE.md](./docs/USER_GUIDE.md), [docs/GOOGLE_LOGIN_DIAGNOSTICS.md](./docs/GOOGLE_LOGIN_DIAGNOSTICS.md) và [docs/TROUBLESHOOTING.md](./docs/TROUBLESHOOTING.md).

## Dữ liệu cục bộ

Đường dẫn mặc định nằm trong `runtime/` và được loại khỏi Git: SQLite, Chrome profile riêng, media/assets, diagnostics và log. Không lưu mật khẩu, cookie hoặc token trong cấu hình hay mã nguồn. Đăng nhập thủ công bằng `Open Browser` hoặc `flow-automation --open-flow`; Chrome giữ phiên trong profile cục bộ.

## Domain và SQLite persistence

Package domain (`flow_automation.core`) định nghĩa các model project, scene, job, asset, reference và event cùng enum trạng thái; package này không phụ thuộc PyQt6 hoặc Playwright. `flow_automation.persistence` cung cấp SQLAlchemy 2.x repositories trên SQLite. Khởi tạo database rõ ràng như sau:

```python
from pathlib import Path

from flow_automation.persistence import Database, FlowRepository

with Database.from_path(Path("runtime/flow_automation.sqlite3")) as database:
    repository = FlowRepository(database)
    project = repository.create_project("My project", "projects/my-project")
    print(project.id)
```

Database tự tạo thư mục cha, bật SQLite foreign keys và timeout khóa, rồi nâng schema bằng `PRAGMA user_version` (schema hiện tại: 1). Mỗi thao tác repository chạy trong transaction riêng; lỗi sẽ rollback toàn bộ thao tác. Job và scene dùng state machine có kiểm soát; job chỉ thành công khi caller xác nhận kết quả đã kiểm tra, và ảnh chỉ được duyệt khi asset ảnh đã verified. Profile/session browser không được lưu trong schema.

`bootstrap.create_flow_repository()` khởi tạo database và cấp repository cho application service; UI không truy cập database trực tiếp. `FlowImagePocService` là PoC một ảnh đã có, còn luồng batch bên dưới dùng `FlowImageJobProcessor` và scheduler trên cùng worker sở hữu browser. Đối soát sau lỗi tải live trước đây được thực hiện thủ công; scheduler khi restart chuyển job còn `RUNNING` sang `RECONCILIATION_REQUIRED` và không gửi lại. API schema version hiện ở v1; chưa có migration v1→v2 vì không đổi schema.

`MediaVerifier.verify_image()` dùng Pillow để kiểm tra bytes/format/dimensions và tính SHA-256. Service chỉ đặt job `SUCCEEDED` và asset `VERIFIED` sau khi file tải xuống thực sự vượt qua bước này. PoC chỉ dùng ảnh; tạo video vẫn chưa được triển khai trong adapter.

## Tạo ảnh theo batch và duyệt

Trong ứng dụng:

1. Chọn **Open Browser**, đăng nhập thủ công và mở một project/editor Flow.
2. Tạo project của tool, thêm scene và prompt bằng các điều khiển ở cửa sổ chính. Import CSV/JSON chưa được cung cấp.
3. Trong Flow, chọn **Nano Banana 2.1**, image mode và **x1**. Bấm **Queue Images** để tạo job; thao tác này chưa gửi prompt.
4. Bấm **Start Image Batch**, đọc và xác nhận cảnh báo credit. Worker kiểm tra workspace/model/UI trước khi chạy. Browser-bound jobs được tuần tự hóa ở concurrency 1.
5. Kết quả chỉ chuyển `SUCCEEDED` sau khi tải file và `MediaVerifier` xác nhận ảnh; ảnh verified xuất hiện trong gallery theo scene. Chọn ảnh rồi bấm **Approve Selected Image** để lưu lựa chọn trong SQLite.

**Pause** chặn job/attempt mới và để thao tác hiện hành kết thúc an toàn. **Cancel Pending** hủy job chưa bắt đầu và dừng dispatch; không tuyên bố đã hủy task ở Flow. **Retry Failed** yêu cầu xác nhận credit và chỉ retry job lỗi đã biết; kết quả không chắc chắn cần đối soát thủ công. Khi mở lại ứng dụng, job từng `RUNNING` được chuyển sang `RECONCILIATION_REQUIRED`, không tự gửi lại.

`jobs/` cung cấp scheduler asyncio bền vững, pause/resume/cancel, retry hữu hạn, checkpoint/recovery và push progress events. Batch processor và gallery đã tích hợp vào Qt worker; bài test 60 scene và kiểm thử image processor dùng fake adapter/SQLite tạm, không gửi tác vụ tới Flow. Một image PoC riêng đã được chạy live trước đây, nhưng **batch từ UI chưa được xác minh live**; mỗi generation có thể tiêu tốn credit. Xem chính sách vận hành tại [docs/JOB_SCHEDULER.md](./docs/JOB_SCHEDULER.md) và trạng thái Flow tại [docs/FLOW_COMPATIBILITY.md](./docs/FLOW_COMPATIBILITY.md).

Retry chỉ tự động áp dụng lỗi tạm thời đã biết trước khi có thể gửi thao tác ngoài. Lỗi xác thực không tự retry; bất kỳ lỗi nào sau khi Flow có thể đã nhận prompt đều yêu cầu đối soát thay vì gửi lại.

## Cấu trúc

- `src/flow_automation/`: mã nguồn ứng dụng, tách UI, application, domain, automation, pipelines, jobs, persistence, storage và diagnostics.
- `config/defaults.toml`: cấu hình mặc định không chứa thông tin bí mật.
- `docs/`: kiến trúc và kế hoạch milestone.
- `tests/`: kiểm thử unit, integration, acceptance và fixtures.

Test repository dùng SQLite trong thư mục tạm độc lập, không đụng database runtime:

```powershell
python -m pytest tests\unit\test_domain_state_machine.py tests\integration\test_persistence_repository.py
```
