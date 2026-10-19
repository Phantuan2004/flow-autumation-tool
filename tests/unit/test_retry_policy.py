from __future__ import annotations

import math

import pytest

from flow_automation.automation.errors import (
    AuthenticationRequiredError,
    FlowStateUnknownError,
    FlowSubmissionNotSentError,
    UnsupportedOperationError,
)
from flow_automation.jobs.retry_policy import (
    RetryDisposition,
    RetryPolicy,
    RetryableJobError,
)


def test_retry_policy_classifies_auth_configuration_transient_and_unknown_errors() -> None:
    policy = RetryPolicy()
    assert (
        policy.classify(
            AuthenticationRequiredError("manual sign-in required"),
            external_submission_possible=False,
        )
        is RetryDisposition.FAIL
    )
    assert (
        policy.classify(
            FlowSubmissionNotSentError("not sent"),
            external_submission_possible=False,
        )
        is RetryDisposition.FAIL
    )
    assert (
        policy.classify(
            UnsupportedOperationError("unsupported"),
            external_submission_possible=False,
        )
        is RetryDisposition.FAIL
    )
    assert (
        policy.classify(
            RetryableJobError("temporary"),
            external_submission_possible=False,
        )
        is RetryDisposition.RETRY
    )
    assert (
        policy.classify(
            RetryableJobError("temporary"),
            external_submission_possible=True,
        )
        is RetryDisposition.RECONCILE
    )
    assert (
        policy.classify(
            FlowStateUnknownError("unknown"),
            external_submission_possible=False,
        )
        is RetryDisposition.RECONCILE
    )


def test_retry_policy_uses_capped_exponential_backoff() -> None:
    policy = RetryPolicy(base_delay_seconds=2, max_delay_seconds=10)
    assert [policy.delay_for_retry(count) for count in range(1, 5)] == [
        2,
        4,
        8,
        10,
    ]
    assert policy.delay_for_retry(10**6) == 10
    with pytest.raises(ValueError, match="at least one"):
        policy.delay_for_retry(0)


def test_retry_policy_rejects_nonfinite_delays() -> None:
    with pytest.raises(ValueError, match="finite"):
        RetryPolicy(base_delay_seconds=math.inf)
