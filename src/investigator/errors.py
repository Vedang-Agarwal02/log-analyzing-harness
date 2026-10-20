"""Errors shared by backends and tools. The tool executor turns them into structured results."""


class OperationError(Exception):
    """An operation failed in a way the calling agent should be told about."""

    error_type = "internal"
    retryable = False

    def __init__(
        self, message: str, *, hint: str | None = None, retry_after_s: float | None = None
    ) -> None:
        super().__init__(message)
        self.hint = hint
        self.retry_after_s = retry_after_s


class OperationTimeout(OperationError):
    error_type = "timeout"
    retryable = True


class ServiceUnavailable(OperationError):
    error_type = "unavailable"
    retryable = True


class RateLimited(OperationError):
    error_type = "rate_limited"
    retryable = True


class NotFound(OperationError):
    error_type = "not_found"
    retryable = False


class InvalidInput(OperationError):
    error_type = "invalid_arguments"
    retryable = False
