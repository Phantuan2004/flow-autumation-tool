# Job scheduler: vận hành và giới hạn

## Trạng thái hiện tại

`BatchScheduler`, `JobRunner`, `RetryPolicy` và `CheckpointStore` quản lý job SQLite trên một event loop asyncio. `JobControlService` là interface ứng dụng cho lệnh và sự kiện đẩy tới subscriber. Scheduler được nối với `FlowImageJobProcessor` và Qt `BrowserWorker`; kiểm thử batch 60 scene cùng processor/gallery dùng fake adapter, không gửi request Flow. Batch live từ UI chưa được xác minh.

Không cần migration: checkpoint được ghi thành `JobEventType.NOTE`, trong `technical_details` chỉ gồm tên checkpoint allowlist và cờ cho biết thao tác ngoài có thể đã bắt đầu.

## Quy tắc điều phối

- Gọi `recover_after_restart()` trước khi nhận lệnh chạy sau khi ứng dụng khởi động. Job còn `RUNNING` chuyển sang `RECONCILIATION_REQUIRED`, kèm checkpoint cuối; không tự gửi lại.
- `PENDING` và `RETRY_WAIT` có thể được scheduler chạy. `RUNNING` được lưu trước khi processor thực thi.
- Chỉ processor trả `JobExecutionResult(result_verified=True)` mới cho phép `SUCCEEDED`. Processor production phải xác minh output trước khi trả kết quả này.
- Lỗi tạm thời có kiểu `RetryableJobError` được retry có exponential backoff theo `retry_limit`, trên cùng job ID. Hết giới hạn thì `FAILED`.
- `AuthenticationRequiredError`, cấu hình/operation không hỗ trợ và lỗi pre-submit không được tự retry.
- `FlowStateUnknownError`, checkpoint xác nhận thao tác ngoài có thể đã bắt đầu hoặc kết quả không verified dẫn tới `RECONCILIATION_REQUIRED`.
- Lỗi chưa phân loại trước thao tác ngoài được đánh `FAILED`; không gửi lại tự động.
- Browser-bound processor buộc dùng concurrency 1. Chỉ processor không chia sẻ một browser/page mới được phép khai báo `browser_bound=False` và concurrency cao hơn.

## Pause, stop và cancel

- `pause()` ngăn job hoặc attempt retry mới bắt đầu. Job đang chạy được phép hoàn tất; pause không ngắt giữa một thao tác ngoài. Checkpoint dùng để ghi nhận tiến trình và xử lý cancel/recovery, không phải cơ chế tạm dừng một tác vụ Flow đã gửi.
- `resume()` mở gate điều phối.
- `stop_dispatch()` dừng lấy job mới, giữ các job chưa bắt đầu trong queue và để job hiện hành kết thúc.
- `cancel(job_id)` chuyển job đang chờ sang `CANCELLED`. Với job đang chạy, nó chỉ đặt yêu cầu cancel cục bộ; processor phải gọi `context.checkpoint()` ở các ranh giới an toàn. Nếu submission có thể đã bắt đầu, trạng thái cuối là `RECONCILIATION_REQUIRED`, không phải xác nhận Flow đã hủy.
- `cancel_pending()` dừng dispatch, hủy các job chưa bắt đầu và yêu cầu job đang chạy dừng tại checkpoint; việc hủy remote không được hỗ trợ.
- `retry(job_id)` chỉ nhận job `FAILED` đã biết kết quả, và vẫn chịu `retry_limit`. Không cho retry trực tiếp trạng thái chưa đối soát.

## Lệnh và tiến độ qua application service

Bootstrap tạo interface cho lớp ứng dụng; processor được inject nên UI không cần biết repository hoặc Playwright:

```python
from pathlib import Path

from flow_automation.bootstrap import (
    create_flow_repository,
    create_job_control_service,
)
from flow_automation.core.models import JobType
from flow_automation.jobs.job_runner import JobExecutionResult


class DryRunProcessor:
    async def execute(self, job, context):
        await context.checkpoint("before_external_submission")
        return JobExecutionResult(
            result_verified=True,
            technical_details={"processor": "dry-run"},
        )


database, repository = create_flow_repository(
    Path("runtime/flow_automation.sqlite3")
)
controls = create_job_control_service(repository, DryRunProcessor())
controls.subscribe(handle_scheduler_event)
controls.recover_after_restart()
await controls.queue_project(project_id, JobType.IMAGE)
await controls.start(project_id)
```

Ví dụ dry-run chỉ trình bày interface và **không tạo media**. Trong ứng dụng desktop, gọi coroutine qua event loop/worker riêng; dùng Qt signal với queued connection để chuyển `SchedulerEvent` về UI thread. Không chờ `start()` đồng bộ trong handler UI và không cập nhật widget từ callback scheduler.

`SchedulerEvent` đẩy loại sự kiện, job/project ID, trạng thái và bộ đếm completed/total. Qt worker chuyển sự kiện qua signal, không cập nhật widget trong callback. Subscriber nên xử lý nhanh; lỗi subscriber được log và không dừng các job khác.

Trong desktop app, **Queue Images** chỉ tạo job. **Start Image Batch** yêu cầu xác nhận credit, kiểm tra workspace Flow/model x1 đang mở rồi chạy trên browser loop. **Retry Failed** chỉ xét job FAILED có kết quả đã biết và cũng cần xác nhận credit; job `RECONCILIATION_REQUIRED` không được retry. Pause chặn các job mới nhưng không dừng task Flow hiện hành. `Cancel Pending` dừng dispatch và hủy job chưa bắt đầu; thao tác đã gửi có thể vẫn tiếp tục trên Flow.

## Kiểm thử

```powershell
python -m pytest tests\integration\test_job_scheduler.py tests\integration\test_persistence_repository.py tests\unit\test_domain_state_machine.py tests\unit\test_retry_policy.py
```

Test chạy trên SQLite tạm và fake processor/adapter, không kết nối Flow, không dùng tài khoản/credit. Batch 60 scene trong test xác minh scheduler/concurrency/progress; image processor tests xác minh download, file verification, gallery approval, file tampering và reconciliation. Những test này không phải kiểm chứng năng lực batch của Flow.
