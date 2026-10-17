# Flow Automation Tool

Ứng dụng desktop Python/PyQt6 để quản lý quy trình sản xuất video theo batch trên giao diện web Veo 3 Flow. Milestone kiểm thử đăng nhập thủ công và khám phá UI đã hoàn tất trên một lần chạy có giám sát: một ảnh Nano Banana 2.1 và một video Omni 1.1 Flash 720p/8 giây đã được tạo và tải từ Flow. Đây chưa phải workflow batch: adapter ảnh/video/tải tài sản chưa được nối end-to-end vào UI/job worker. Chi tiết và giới hạn tại [docs/FLOW_COMPATIBILITY.md](./docs/FLOW_COMPATIBILITY.md) và [docs/IMPLEMENTATION_PLAN.md](./docs/IMPLEMENTATION_PLAN.md).

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

Persistence hiện được gọi tường minh qua `Database`/`FlowRepository`, chưa được bootstrap tự động trong UI và chưa có job scheduler/worker bền vững. API schema version đã sẵn sàng cho các nâng cấp tiếp theo; hiện chỉ có schema v1, chưa có migration từ một schema ứng dụng cũ.

## Cấu trúc

- `src/flow_automation/`: mã nguồn ứng dụng, tách UI, application, domain, automation, pipelines, jobs, persistence, storage và diagnostics.
- `config/defaults.toml`: cấu hình mặc định không chứa thông tin bí mật.
- `docs/`: kiến trúc và kế hoạch milestone.
- `tests/`: kiểm thử unit, integration, acceptance và fixtures.

Test repository dùng SQLite trong thư mục tạm độc lập, không đụng database runtime:

```powershell
python -m pytest tests\unit\test_domain_state_machine.py tests\integration\test_persistence_repository.py
```
