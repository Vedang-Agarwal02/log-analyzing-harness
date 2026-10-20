"""Primitive types shared by every domain model."""

from datetime import UTC, datetime
from typing import Annotated, Literal, get_args

from pydantic import AfterValidator


def ensure_utc(value: datetime) -> datetime:
    """Return `value` as an aware UTC datetime. Naive values are assumed to be UTC already."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def iso(value: datetime) -> str:
    """Format a datetime as second-precision ISO-8601 UTC ending in 'Z'."""
    return ensure_utc(value).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(text: str) -> datetime:
    """Parse an ISO-8601 string (a trailing 'Z' is allowed) into an aware UTC datetime."""
    return ensure_utc(datetime.fromisoformat(text))


UtcDatetime = Annotated[datetime, AfterValidator(ensure_utc)]

RootCauseCategory = Literal[
    "bad_deployment",
    "config_change",
    "dependency_failure",
    "resource_exhaustion",
    "traffic_surge",
    "infrastructure",
    "data_issue",
    "unknown",
]
ROOT_CAUSE_CATEGORIES: tuple[str, ...] = get_args(RootCauseCategory)

StatementKind = Literal["observation", "inference", "assumption"]
Severity = Literal["SEV1", "SEV2", "SEV3"]
LogLevel = Literal["DEBUG", "INFO", "WARN", "ERROR"]
LOG_LEVELS: tuple[str, ...] = get_args(LogLevel)
