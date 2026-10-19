from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite

from flow_automation.automation.errors import (
    AuthenticationRequiredError,
    FlowStateUnknownError,
    FlowSubmissionNotSentError,
    UnsupportedOperationError,
)


class RetryDisposition(StrEnum):
    RETRY = "RETRY"
    FAIL = "FAIL"
    RECONCILE = "RECONCILE"


class RetryableJobError(RuntimeError):
    """A known transient failure that occurred before an uncertain submission."""


class JobCancellationUncertain(RuntimeError):
    """Cancellation was requested after an external operation may have started."""


class JobCancelledBeforeSubmission(RuntimeError):
    """Cancellation was requested at a safe checkpoint before submission."""


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 60.0

    def __post_init__(self) -> None:
        if not isfinite(self.base_delay_seconds) or not isfinite(self.max_delay_seconds):
            raise ValueError("Retry delays must be finite numbers.")
        if self.base_delay_seconds < 0 or self.max_delay_seconds < 0:
            raise ValueError("Retry delays must not be negative.")
        if self.base_delay_seconds > self.max_delay_seconds:
            raise ValueError("Base retry delay cannot exceed the maximum delay.")

    def classify(
        self,
        error: Exception,
        *,
        external_submission_possible: bool,
    ) -> RetryDisposition:
        if isinstance(error, (FlowStateUnknownError, JobCancellationUncertain)):
            return RetryDisposition.RECONCILE
        if external_submission_possible:
            return RetryDisposition.RECONCILE
        if isinstance(
            error,
            (
                AuthenticationRequiredError,
                FlowSubmissionNotSentError,
                UnsupportedOperationError,
                ValueError,
                TypeError,
            ),
        ):
            return RetryDisposition.FAIL
        if isinstance(error, (RetryableJobError, TimeoutError, ConnectionError)):
            return RetryDisposition.RETRY
        return RetryDisposition.FAIL

    def delay_for_retry(self, retry_count: int) -> float:
        if retry_count < 1:
            raise ValueError("Retry count must be at least one.")
        if self.base_delay_seconds == 0 or self.max_delay_seconds == 0:
            return 0
        delay = self.base_delay_seconds
        for _ in range(retry_count - 1):
            if delay >= self.max_delay_seconds:
                return self.max_delay_seconds
            delay = min(delay * 2, self.max_delay_seconds)
        return delay
