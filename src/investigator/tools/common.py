"""Helpers shared by the domain tools."""

from datetime import datetime, timedelta

from investigator.errors import InvalidInput


def validate_window(start: datetime, end: datetime, *, max_window: timedelta) -> None:
    if end <= start:
        raise InvalidInput(
            "end must be after start", hint="Use ISO-8601 UTC timestamps with start < end."
        )
    if end - start > max_window:
        raise InvalidInput(
            f"time window too large ({end - start}); the maximum is {max_window}",
            hint="Narrow the window.",
        )


def clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."
