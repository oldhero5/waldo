"""Operational labeling failures eligible for the bounded Celery retry policy."""

from minio.error import S3Error, ServerError
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from urllib3.exceptions import ConnectTimeoutError, MaxRetryError, NewConnectionError, ProtocolError, ReadTimeoutError


class RetryableLabelingError(RuntimeError):
    """A temporary service failure; media/model contract failures stay terminal."""


def is_retryable(error: Exception) -> bool:
    if isinstance(error, RetryableLabelingError):
        return True
    if isinstance(error, FileNotFoundError):
        return False
    if isinstance(
        error,
        (
            OSError,
            OperationalError,
            InterfaceError,
            ConnectTimeoutError,
            MaxRetryError,
            NewConnectionError,
            ProtocolError,
            ReadTimeoutError,
        ),
    ):
        return True
    if isinstance(error, DBAPIError):
        return error.connection_invalidated
    if isinstance(error, ServerError):
        return error.status_code >= 500
    if isinstance(error, S3Error):
        return error.code in {"InternalError", "ServiceUnavailable", "SlowDown", "RequestTimeout"}
    return False
