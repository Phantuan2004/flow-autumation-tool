# Flow Automation Tool

Ứng dụng desktop Python/PyQt6 để quản lý quy trình sản xuất video theo batch trên giao diện web Veo 3 Flow. Giai đoạn khởi tạo này chỉ cung cấp khung ứng dụng và cấu hình; chưa thực hiện tự động hóa trình duyệt, xử lý media hay quản lý job.

## Yêu cầu

- Python 3.12 trở lên (khuyến nghị Python 3.13).
- Windows, macOS hoặc Linux có môi trường đồ họa Qt để chạy ứng dụng.

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

Playwright Chromium chưa cần cài trong milestone khởi tạo. Khi milestone browser automation được triển khai, cần cài browser tương ứng bằng `python -m playwright install chromium`.

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

## Chạy kiểm thử

```powershell
python -m pytest
```

Smoke test chạy Qt ở chế độ offscreen khi tạo cửa sổ kiểm thử, vì vậy không cần desktop hiển thị cho bước kiểm tra này.

## Dữ liệu cục bộ

Đường dẫn mặc định nằm trong `runtime/` và được loại khỏi Git: SQLite, Chromium persistent profile, media/assets và log. Không lưu mật khẩu, cookie hoặc token trong cấu hình hay mã nguồn. Người dùng đăng nhập Flow thủ công trong Chromium khi chức năng đó được triển khai.

## Cấu trúc

- `src/flow_automation/`: mã nguồn ứng dụng, tách UI, application, domain, automation, pipelines, jobs, persistence, storage và diagnostics.
- `config/defaults.toml`: cấu hình mặc định không chứa thông tin bí mật.
- `docs/`: kiến trúc và kế hoạch milestone.
- `tests/`: kiểm thử unit, integration, acceptance và fixtures.

