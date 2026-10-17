class FlowAutomationError(RuntimeError):
    """Base error for browser and Flow adapter failures."""


class BrowserAlreadyRunningError(FlowAutomationError):
    """Raised when another process owns the configured Chromium profile."""


class BrowserLifecycleError(FlowAutomationError):
    """Raised when Chromium cannot be started or managed safely."""


class FlowStateUnknownError(FlowAutomationError):
    """Raised when the current Flow or authentication state is ambiguous."""


class AuthenticationRequiredError(FlowAutomationError):
    """Raised when the observed UI indicates that manual sign-in is required."""


class UnsupportedOperationError(FlowAutomationError):
    """Raised when an operation has not been verified against the Flow UI."""
