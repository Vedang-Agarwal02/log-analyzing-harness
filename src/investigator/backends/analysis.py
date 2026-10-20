"""Pure analysis helpers shared by tools: log patterns, change points, downsampling, keywords."""

import hashlib
import math
import re
from dataclasses import dataclass
from datetime import datetime
from statistics import fmean

_UUID = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE
)
_HEX = re.compile(r"\b[0-9a-f]{8,}\b", re.IGNORECASE)
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_WORD = re.compile(r"[a-z0-9]+")
_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "that",
    "this",
    "was",
    "were",
    "are",
    "after",
    "into",
    "its",
    "due",
    "but",
    "not",
    "has",
    "have",
    "been",
    "than",
    "then",
    "when",
    "while",
}

Point = tuple[datetime, float]


def normalize_message(message: str) -> str:
    """Collapse variable parts (UUIDs, hex ids, numbers) so similar lines share one pattern."""
    text = _UUID.sub("<ID>", message)
    text = _HEX.sub("<ID>", text)
    return _NUMBER.sub("<N>", text)


def pattern_id(service: str, level: str, template: str) -> str:
    digest = hashlib.sha1(f"{service}|{level}|{template}".encode()).hexdigest()[:8]
    return f"logpat-{digest}"


def series_id(service: str, metric: str, start: str, end: str) -> str:
    digest = hashlib.sha1(f"{service}|{metric}|{start}|{end}".encode()).hexdigest()[:8]
    return f"met-{digest}"


@dataclass(frozen=True)
class ChangePoint:
    at: datetime
    before_mean: float
    after_mean: float
    severity: float
    direction: str


def detect_change_point(
    points: list[Point], *, min_severity: float = 4.0, min_relative_change: float = 0.3
) -> ChangePoint | None:
    """Best single mean-shift split. Severity = |after - before| / stdev(before)."""
    count = len(points)
    min_segment = max(3, count // 20)
    if count < 2 * min_segment:
        return None
    values = [value for _, value in points]
    prefix = [0.0]
    prefix_sq = [0.0]
    for value in values:
        prefix.append(prefix[-1] + value)
        prefix_sq.append(prefix_sq[-1] + value * value)
    best: tuple[float, int, float, float] | None = None
    for index in range(min_segment, count - min_segment + 1):
        before_mean = prefix[index] / index
        after_mean = (prefix[count] - prefix[index]) / (count - index)
        variance = max(prefix_sq[index] / index - before_mean**2, 0.0)
        scale = max(math.sqrt(variance), abs(before_mean) * 0.05, 1e-9)
        severity = abs(after_mean - before_mean) / scale
        if best is None or severity > best[0]:
            best = (severity, index, before_mean, after_mean)
    if best is None:
        return None
    severity, index, before_mean, after_mean = best
    relative = abs(after_mean - before_mean) / max(abs(before_mean), 1e-9)
    if severity < min_severity or relative < min_relative_change:
        return None
    return ChangePoint(
        at=points[index][0],
        before_mean=round(before_mean, 3),
        after_mean=round(after_mean, 3),
        severity=round(min(severity, 999.0), 2),
        direction="up" if after_mean > before_mean else "down",
    )


def downsample(points: list[Point], max_points: int = 60) -> list[Point]:
    if len(points) <= max_points:
        return [(ts, round(value, 3)) for ts, value in points]
    bucket = math.ceil(len(points) / max_points)
    out: list[Point] = []
    for start in range(0, len(points), bucket):
        chunk = points[start : start + bucket]
        out.append((chunk[0][0], round(fmean(value for _, value in chunk), 3)))
    return out


def summarize(points: list[Point]) -> dict[str, float]:
    if not points:
        return {}
    values = [value for _, value in points]
    ordered = sorted(values)
    p95 = ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))]
    head = values[: max(1, len(values) // 5)]
    return {
        "min": round(min(values), 3),
        "max": round(max(values), 3),
        "mean": round(fmean(values), 3),
        "p95": round(p95, 3),
        "baseline_mean": round(fmean(head), 3),
        "latest": round(values[-1], 3),
    }


def tokenize(text: str) -> list[str]:
    return [
        word for word in _WORD.findall(text.lower()) if len(word) >= 3 and word not in _STOPWORDS
    ]
