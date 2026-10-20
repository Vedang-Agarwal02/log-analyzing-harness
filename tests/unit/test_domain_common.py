from datetime import UTC, datetime, timedelta, timezone

from pydantic import BaseModel

from investigator.domain.common import (
    LOG_LEVELS,
    ROOT_CAUSE_CATEGORIES,
    UtcDatetime,
    iso,
    parse_iso,
)


class _Model(BaseModel):
    at: UtcDatetime


def test_utc_datetime_normalizes_naive_and_offset_values() -> None:
    naive = _Model.model_validate({"at": "2026-03-10T10:12:00"})
    offset = _Model.model_validate({"at": "2026-03-10T12:12:00+02:00"})
    zulu = _Model.model_validate({"at": "2026-03-10T10:12:00Z"})
    expected = datetime(2026, 3, 10, 10, 12, tzinfo=UTC)
    assert naive.at == expected
    assert offset.at == expected
    assert zulu.at == expected
    assert offset.at.utcoffset() == timedelta(0)


def test_iso_formats_second_precision_with_trailing_z() -> None:
    assert iso(datetime(2026, 3, 10, 10, 12, 5, 999, tzinfo=UTC)) == "2026-03-10T10:12:05Z"
    plus_two = datetime(2026, 3, 10, 12, 12, tzinfo=timezone(timedelta(hours=2)))
    assert iso(plus_two) == "2026-03-10T10:12:00Z"


def test_parse_iso_accepts_z_suffix() -> None:
    assert parse_iso("2026-03-10T10:12:00Z") == datetime(2026, 3, 10, 10, 12, tzinfo=UTC)


def test_category_and_level_vocabularies_are_fixed() -> None:
    assert ROOT_CAUSE_CATEGORIES == (
        "bad_deployment",
        "config_change",
        "dependency_failure",
        "resource_exhaustion",
        "traffic_surge",
        "infrastructure",
        "data_issue",
        "unknown",
    )
    assert LOG_LEVELS == ("DEBUG", "INFO", "WARN", "ERROR")
