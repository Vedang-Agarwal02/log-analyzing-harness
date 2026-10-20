import random
from datetime import UTC, datetime, timedelta

from investigator.backends.analysis import (
    detect_change_point,
    downsample,
    normalize_message,
    pattern_id,
    series_id,
    summarize,
    tokenize,
)

T0 = datetime(2026, 3, 10, 10, 0, tzinfo=UTC)


def _series(values: list[float]) -> list[tuple[datetime, float]]:
    return [(T0 + timedelta(minutes=i), value) for i, value in enumerate(values)]


def test_normalize_message_collapses_variable_parts() -> None:
    assert (
        normalize_message("Order a3f9c2d1 failed after 1234ms (attempt 2/3)")
        == "Order <ID> failed after <N>ms (attempt <N>/<N>)"
    )
    assert normalize_message("req 123e4567-e89b-12d3-a456-426614174000 ok") == "req <ID> ok"
    assert normalize_message("Timed out after 2.5s") == "Timed out after <N>s"


def test_ids_are_stable_and_prefixed() -> None:
    first = pattern_id("orders-service", "ERROR", "boom <N>")
    assert first == pattern_id("orders-service", "ERROR", "boom <N>")
    assert first.startswith("logpat-") and len(first) == len("logpat-") + 8
    assert first != pattern_id("orders-service", "WARN", "boom <N>")
    assert series_id("a", "m", "x", "y").startswith("met-")


def test_detect_change_point_finds_step_up_and_drop() -> None:
    rng = random.Random(3)
    up = [0.3 * (1 + rng.gauss(0, 0.1)) for _ in range(100)] + [
        30 * (1 + rng.gauss(0, 0.1)) for _ in range(40)
    ]
    point = detect_change_point(_series(up))
    assert point is not None and point.direction == "up"
    assert abs((point.at - (T0 + timedelta(minutes=100))).total_seconds()) <= 120
    down = [100.0 + rng.gauss(0, 2) for _ in range(80)] + [
        40.0 + rng.gauss(0, 2) for _ in range(80)
    ]
    dropped = detect_change_point(_series(down))
    assert dropped is not None and dropped.direction == "down"


def test_detect_change_point_ignores_noise_and_short_series() -> None:
    rng = random.Random(4)
    noise = [100.0 * (1 + rng.gauss(0, 0.04)) for _ in range(200)]
    assert detect_change_point(_series(noise)) is None
    assert detect_change_point(_series([1.0, 50.0])) is None
    assert detect_change_point(_series([0.0] * 50)) is None


def test_downsample_and_summarize() -> None:
    points = _series([float(i) for i in range(400)])
    small = downsample(points)
    assert len(small) <= 60 and small[0][0] == T0
    assert downsample(points[:10]) == [(t, round(v, 3)) for t, v in points[:10]]
    stats = summarize(points)
    assert stats["min"] == 0.0 and stats["max"] == 399.0 and stats["latest"] == 399.0
    assert stats["baseline_mean"] < stats["mean"]
    assert summarize([]) == {}


def test_tokenize_splits_and_filters() -> None:
    assert tokenize("The orders-service had a NullPointerException in 2025") == [
        "orders",
        "service",
        "had",
        "nullpointerexception",
        "2025",
    ]
