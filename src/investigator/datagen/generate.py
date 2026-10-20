"""Deterministic generator: platform.yaml + scenario YAML -> telemetry files + ground truth.

Run `python -m investigator.datagen.generate` from the repository root to regenerate
datasets/generated/ for every scenario in datasets/scenarios/.
"""

import hashlib
import json
import random
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from investigator.datagen.platform import Platform, load_platform
from investigator.datagen.spec import MetricSignal, ScenarioSpec, load_all_scenarios, parse_offset
from investigator.domain.common import iso

_PLACEHOLDER = re.compile(r"\{(?P<name>[a-z_]+)(?::(?P<arg>[^}]*))?\}")
_MARKET_OPEN_MIN = 13 * 60 + 30
_MARKET_CLOSE_MIN = 20 * 60
_RAMP_MIN = 10.0

LogRow = tuple[datetime, str, str, str]


class GenerationError(ValueError):
    """Raised when a scenario is inconsistent with the platform or itself."""


def fill_template(template: str, rng: random.Random) -> str:
    """Replace {ms}, {n}, {id}, {choice:a|b} and {int:lo-hi} placeholders."""

    def replace(match: re.Match[str]) -> str:
        name, arg = match.group("name"), match.group("arg")
        if name == "ms":
            return str(rng.randint(5, 900))
        if name == "n":
            return str(rng.randint(1, 99999))
        if name == "id":
            return f"{rng.getrandbits(32):08x}"
        if name == "choice" and arg:
            return rng.choice(arg.split("|"))
        if name == "int" and arg:
            low, high = arg.split("-")
            return str(rng.randint(int(low), int(high)))
        raise GenerationError(f"unknown placeholder {match.group(0)!r} in template {template!r}")

    return _PLACEHOLDER.sub(replace, template)


def generate_all(scenarios_dir: Path, platform_path: Path, out_root: Path) -> list[Path]:
    platform = load_platform(platform_path)
    return [
        generate_scenario(spec, platform, out_root) for spec in load_all_scenarios(scenarios_dir)
    ]


def generate_scenario(spec: ScenarioSpec, platform: Platform, out_root: Path) -> Path:
    _validate(spec, platform)
    start = spec.t0 - parse_offset(spec.window.before)
    end = spec.t0 + parse_offset(spec.window.after)
    reported_at = spec.t0 + parse_offset(spec.reported_offset)
    deployments, config_changes, change_ids = _build_changes(spec, platform)
    root = out_root / spec.id
    telemetry = root / "telemetry"
    environment = root / "environment"
    telemetry.mkdir(parents=True, exist_ok=True)
    environment.mkdir(parents=True, exist_ok=True)
    _write_json(root / "incident.json", _incident(spec, reported_at))
    _write_jsonl(telemetry / "logs.jsonl", _build_logs(spec, platform, start, end))
    _write_json(telemetry / "metrics.json", _build_metrics(spec, platform, start, end))
    _write_json(telemetry / "deployments.json", {"deployments": deployments})
    _write_json(telemetry / "config_changes.json", {"config_changes": config_changes})
    _write_json(telemetry / "topology.json", {"services": _topology(platform)})
    _write_json(telemetry / "history.json", {"incidents": _build_history(spec, platform)})
    _write_json(environment / "faults.json", {"faults": _faults(spec)})
    _write_json(root / "ground_truth.json", _ground_truth(spec, change_ids))
    return root


# --------------------------------------------------------------------------- validation


def _validate(spec: ScenarioSpec, platform: Platform) -> None:
    names = platform.service_names()
    problems: list[str] = []

    def need_service(name: str | None, where: str) -> None:
        if name is None or name not in names:
            problems.append(f"{where}: unknown service {name!r}")

    for service in spec.incident.affected_services:
        need_service(service, "incident.affected_services")
    keys = [change.key for change in spec.changes]
    if len(keys) != len(set(keys)):
        problems.append("changes: keys must be unique")
    for change in spec.changes:
        need_service(change.service, f"change {change.key}")
        if change.type == "deploy" and (change.version is None or change.previous_version is None):
            problems.append(f"change {change.key}: deploys need version and previous_version")
        if change.type == "config" and (
            change.config_key is None or change.old is None or change.new is None
        ):
            problems.append(f"change {change.key}: config changes need config_key, old and new")
    for signal in spec.metric_signals:
        _validate_metric_signal(signal, platform, problems)
    for log in spec.log_signals:
        need_service(log.service, "log signal")
        if log.every is None and log.rate_per_min is None and log.count is None:
            problems.append(f"log signal {log.template!r}: set rate_per_min, every or count")
        if log.every is not None and parse_offset(log.every) <= timedelta(0):
            problems.append(f"log signal {log.template!r}: every must be positive")
    for skew in spec.clock_skews:
        need_service(skew.service, "clock skew")
    truth = spec.ground_truth
    if truth.expected_status == "conclusive" and (not truth.services or not truth.categories):
        problems.append("ground_truth: conclusive scenarios need services and categories")
    for service in [*truth.services, *truth.red_herring_services]:
        need_service(service, "ground_truth")
    if truth.injected_conclusion_service is not None:
        need_service(truth.injected_conclusion_service, "ground_truth.injected_conclusion_service")
    for key in [truth.trigger, *truth.red_herring_changes]:
        if key is not None and key not in keys:
            problems.append(f"ground_truth: unknown change key {key!r}")
    for evidence in truth.key_evidence:
        if evidence.kind == "change" and evidence.change not in keys:
            problems.append(f"key_evidence: unknown change key {evidence.change!r}")
        if evidence.kind == "metric_series":
            need_service(evidence.service, "key_evidence")
            if (
                evidence.service is not None
                and evidence.service in names
                and evidence.metric not in platform.metrics_for(evidence.service)
            ):
                problems.append(
                    f"key_evidence: {evidence.service} has no metric {evidence.metric!r}"
                )
        if evidence.kind == "log_pattern":
            need_service(evidence.service, "key_evidence")
            if not evidence.contains:
                problems.append("key_evidence: log_pattern needs 'contains'")
    if problems:
        raise GenerationError(f"{spec.id}: " + "; ".join(problems))


def _validate_metric_signal(signal: MetricSignal, platform: Platform, problems: list[str]) -> None:
    where = f"metric signal {signal.service}/{signal.metric}"
    if signal.service not in platform.service_names():
        problems.append(f"{where}: unknown service")
        return
    if signal.metric not in platform.metrics_for(signal.service):
        problems.append(f"{where}: unknown metric {signal.metric}")
    required: dict[str, list[str]] = {
        "step": ["to"],
        "spike": ["to", "until"],
        "ramp": ["to", "until"],
        "multiply": ["factor"],
        "sawtooth": ["to", "period"],
        "periodic_spike": ["to", "period", "duration"],
    }
    for field in required[signal.shape]:
        if getattr(signal, field) is None:
            problems.append(f"{where}: shape {signal.shape} needs {field}")


# --------------------------------------------------------------------------- changes & history


def _build_changes(
    spec: ScenarioSpec, platform: Platform
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
    rng = random.Random(f"{spec.seed}:changes")
    background = platform.background_changes
    horizon_start = spec.t0 - parse_offset(background.horizon)
    horizon_end = spec.t0 - parse_offset(background.quiet_period)
    span_minutes = int((horizon_end - horizon_start).total_seconds() // 60)
    services = sorted(s.name for s in platform.services if s.kind == "service")
    first_change: dict[str, datetime] = {}
    for change in spec.changes:
        at = spec.t0 + parse_offset(change.at)
        first_change[change.service] = min(at, first_change.get(change.service, at))

    def pick_time(service: str) -> datetime | None:
        limit = first_change.get(service)
        for _ in range(20):
            when = horizon_start + timedelta(minutes=rng.randrange(span_minutes))
            if limit is None or when < limit - timedelta(days=1):
                return when
        return None

    items: list[tuple[datetime, str, dict[str, Any], str | None]] = []
    for _ in range(background.deploys):
        service = rng.choice(services)
        when = pick_time(service)
        major, minor, patch = rng.randint(1, 6), rng.randint(0, 20), rng.randint(0, 8)
        payload: dict[str, Any] = {
            "service": service,
            "version": f"{major}.{minor}.{patch + 1}",
            "previous_version": f"{major}.{minor}.{patch}",
            "status": "succeeded",
            "author": rng.choice(background.authors),
            "summary": rng.choice(background.deploy_summaries),
        }
        if when is not None:
            items.append((when, "deploy", payload, None))
    for _ in range(background.configs):
        service = rng.choice(services)
        when = pick_time(service)
        template = rng.choice(background.config_changes)
        payload = {
            "service": service,
            "config_key": template.config_key,
            "old": template.old,
            "new": template.new,
            "source": template.source,
            "author": rng.choice(background.authors),
            "note": template.note,
        }
        if when is not None:
            items.append((when, "config", payload, None))
    for change in spec.changes:
        when = spec.t0 + parse_offset(change.at)
        if change.type == "deploy":
            payload = {
                "service": change.service,
                "version": change.version,
                "previous_version": change.previous_version,
                "status": change.status,
                "author": change.author,
                "summary": change.summary,
            }
        else:
            payload = {
                "service": change.service,
                "config_key": change.config_key,
                "old": change.old,
                "new": change.new,
                "source": change.source,
                "author": change.author,
                "note": change.note,
            }
        items.append((when, change.type, payload, change.key))

    items.sort(key=lambda item: (item[0], item[2]["service"], item[1], item[3] or ""))
    deployments: list[dict[str, Any]] = []
    config_changes: list[dict[str, Any]] = []
    change_ids: dict[str, str] = {}
    for when, kind, payload, key in items:
        if kind == "deploy":
            change_id = f"dep-{len(deployments) + 1:04d}"
            duration = timedelta(minutes=2 + len(deployments) % 3)
            deployments.append(
                {
                    "deployment_id": change_id,
                    "service": payload["service"],
                    "version": payload["version"],
                    "previous_version": payload["previous_version"],
                    "started_at": iso(when),
                    "finished_at": iso(when + duration),
                    "status": payload["status"],
                    "author": payload["author"],
                    "summary": payload["summary"],
                }
            )
        else:
            change_id = f"cfg-{len(config_changes) + 1:04d}"
            config_changes.append(
                {
                    "change_id": change_id,
                    "service": payload["service"],
                    "key": payload["config_key"],
                    "old_value": payload["old"],
                    "new_value": payload["new"],
                    "changed_at": iso(when),
                    "author": payload["author"],
                    "source": payload["source"],
                    "note": payload["note"],
                }
            )
        if key is not None:
            change_ids[key] = change_id
    return deployments, config_changes, change_ids


def _build_history(spec: ScenarioSpec, platform: Platform) -> list[dict[str, Any]]:
    entries = sorted([*platform.history, *spec.history], key=lambda entry: (entry.date, entry.key))
    return [
        {
            "incident_id": f"hist-{index:04d}",
            "date": entry.date,
            "title": entry.title,
            "services": entry.services,
            "symptoms": entry.symptoms,
            "root_cause": entry.root_cause,
            "resolution": entry.resolution,
        }
        for index, entry in enumerate(entries, start=1)
    ]


# --------------------------------------------------------------------------- logs


def _emit_rate(
    row: tuple[str, str, str],
    rate: float,
    start: datetime,
    end: datetime,
    rng: random.Random,
    out: list[LogRow],
) -> None:
    service, level, template = row
    if rate <= 0:
        return
    whole, fraction = int(rate), rate - int(rate)
    minute = start
    while minute < end:
        count = whole + (1 if rng.random() < fraction else 0)
        for _ in range(count):
            ts = minute + timedelta(seconds=rng.randint(0, 59))
            if ts < end:
                out.append((ts, service, level, fill_template(template, rng)))
        minute += timedelta(minutes=1)


def _build_logs(
    spec: ScenarioSpec, platform: Platform, start: datetime, end: datetime
) -> list[dict[str, Any]]:
    rows: list[LogRow] = []
    for service in sorted(platform.services, key=lambda s: s.name):
        for index, noise in enumerate(service.noise_logs):
            rng = random.Random(f"{spec.seed}:noise:{service.name}:{index}")
            rate = noise.rate_per_min * spec.log_noise_scale
            _emit_rate((service.name, noise.level, noise.template), rate, start, end, rng, rows)
    for index, signal in enumerate(spec.log_signals):
        rng = random.Random(f"{spec.seed}:log-signal:{index}")
        at = spec.t0 + parse_offset(signal.at)
        until = spec.t0 + parse_offset(signal.until) if signal.until else end
        if signal.every is not None:
            period = parse_offset(signal.every)
            when = at
            while when < until:
                for k in range(signal.count or 1):
                    ts = when + timedelta(minutes=k, seconds=rng.randint(0, 59))
                    if start <= ts < end:
                        rows.append(
                            (ts, signal.service, signal.level, fill_template(signal.template, rng))
                        )
                when += period
        elif signal.rate_per_min is not None:
            row = (signal.service, signal.level, signal.template)
            _emit_rate(row, signal.rate_per_min, max(at, start), min(until, end), rng, rows)
        else:
            for k in range(signal.count or 1):
                ts = at + timedelta(minutes=k, seconds=rng.randint(0, 59))
                if start <= ts < end:
                    rows.append(
                        (ts, signal.service, signal.level, fill_template(signal.template, rng))
                    )
    skews = {skew.service: timedelta(seconds=skew.offset_seconds) for skew in spec.clock_skews}
    if skews:
        rows = [
            (ts + skews.get(service, timedelta(0)), service, level, msg)
            for ts, service, level, msg in rows
        ]
    rows.sort()
    records: list[dict[str, Any]] = []
    for index, (ts, service, level, message) in enumerate(rows):  # type: ignore[assignment]
        digest = hashlib.sha1(f"{spec.id}|{index}|{service}|{iso(ts)}".encode()).hexdigest()[:10]
        records.append(
            {
                "log_id": f"log-{digest}",
                "ts": iso(ts),
                "service": service,
                "level": level,
                "message": message,
            }
        )
    return records


# --------------------------------------------------------------------------- metrics


def _traffic_factor(ts: datetime) -> float:
    """US market hours (13:30-20:00 UTC) raise traffic by 30%, with 20-minute ramps."""
    minutes = ts.hour * 60 + ts.minute
    if minutes < _MARKET_OPEN_MIN - _RAMP_MIN or minutes >= _MARKET_CLOSE_MIN + _RAMP_MIN:
        return 1.0
    if minutes < _MARKET_OPEN_MIN + _RAMP_MIN:
        return 1.0 + 0.3 * (minutes - (_MARKET_OPEN_MIN - _RAMP_MIN)) / (2 * _RAMP_MIN)
    if minutes < _MARKET_CLOSE_MIN - _RAMP_MIN:
        return 1.3
    return 1.3 - 0.3 * (minutes - (_MARKET_CLOSE_MIN - _RAMP_MIN)) / (2 * _RAMP_MIN)


def _apply_signal(
    values: list[float],
    timestamps: list[datetime],
    signal: MetricSignal,
    t0: datetime,
    baseline: float,
) -> list[float]:
    at = t0 + parse_offset(signal.at)
    until = t0 + parse_offset(signal.until) if signal.until else None
    low = signal.start_value if signal.start_value is not None else baseline
    out = list(values)
    for index, ts in enumerate(timestamps):
        if ts < at:
            continue
        active = until is None or ts < until
        if signal.shape in ("step", "spike") and active and signal.to is not None:
            out[index] = signal.to
        elif signal.shape == "multiply" and active and signal.factor is not None:
            out[index] = out[index] * signal.factor
        elif signal.shape == "ramp" and until is not None and signal.to is not None:
            if ts >= until:
                out[index] = signal.to
            else:
                out[index] = low + (signal.to - low) * ((ts - at) / (until - at))
        elif signal.shape == "sawtooth" and active and signal.period and signal.to is not None:
            period = parse_offset(signal.period)
            out[index] = low + (signal.to - low) * (((ts - at) % period) / period)
        elif signal.shape == "periodic_spike" and active and signal.period and signal.duration:
            period = parse_offset(signal.period)
            elapsed = ts - at
            cycles = elapsed // period
            if (
                cycles >= 1
                and elapsed - cycles * period < parse_offset(signal.duration)
                and signal.to is not None
            ):
                out[index] = signal.to
    return out


def _build_metrics(
    spec: ScenarioSpec, platform: Platform, start: datetime, end: datetime
) -> dict[str, Any]:
    step = timedelta(seconds=spec.metrics_step_seconds)
    count = int((end - start) / step)
    timestamps = [start + index * step for index in range(count)]
    by_series: dict[tuple[str, str], list[MetricSignal]] = {}
    for signal in spec.metric_signals:
        by_series.setdefault((signal.service, signal.metric), []).append(signal)
    series: dict[str, dict[str, Any]] = {}
    for service in sorted(platform.service_names()):
        metrics = platform.metrics_for(service)
        if not metrics:
            continue
        series[service] = {}
        for name, resolved in metrics.items():
            definition = resolved.definition
            values = [
                resolved.baseline * (_traffic_factor(ts) if definition.traffic else 1.0)
                for ts in timestamps
            ]
            for signal in by_series.get((service, name), []):
                values = _apply_signal(values, timestamps, signal, spec.t0, resolved.baseline)
            rng = random.Random(f"{spec.seed}:metric:{service}:{name}")
            noise = definition.noise_pct / 100.0
            final: list[float] = []
            for value in values:
                noisy = value * (1.0 + rng.gauss(0.0, noise)) if noise > 0 else value
                noisy = max(noisy, 0.0)
                if definition.max_value is not None:
                    noisy = min(noisy, definition.max_value)
                final.append(round(noisy, 3))
            series[service][name] = {
                "unit": definition.unit,
                "description": definition.description,
                "values": final,
            }
    return {
        "start": iso(start),
        "step_seconds": spec.metrics_step_seconds,
        "points": count,
        "series": series,
    }


# --------------------------------------------------------------------------- small documents


def _incident(spec: ScenarioSpec, reported_at: datetime) -> dict[str, Any]:
    incident = spec.incident
    return {
        "incident_id": spec.id,
        "title": incident.title,
        "severity": incident.severity,
        "reported_at": iso(reported_at),
        "reporter": incident.reporter,
        "summary": incident.summary.strip(),
        "affected_services": incident.affected_services,
        "customer_impact": incident.customer_impact,
        "initial_hypothesis": incident.initial_hypothesis,
    }


def _topology(platform: Platform) -> list[dict[str, Any]]:
    return [
        {
            "name": service.name,
            "kind": service.kind,
            "tier": service.tier,
            "team": service.team,
            "description": service.description,
            "depends_on": service.depends_on,
        }
        for service in sorted(platform.services, key=lambda s: s.name)
    ]


def _faults(spec: ScenarioSpec) -> list[dict[str, Any]]:
    faults: list[dict[str, Any]] = []
    for fault in spec.faults:
        params = dict(fault.params)
        if "drop_ranges" in params:
            params["drop_ranges"] = [
                [iso(spec.t0 + parse_offset(a)), iso(spec.t0 + parse_offset(b))]
                for a, b in params["drop_ranges"]
            ]
        if "hide_after" in params:
            params["hide_after"] = iso(spec.t0 + parse_offset(str(params["hide_after"])))
        faults.append(
            {
                "operation": fault.operation,
                "fault": fault.fault,
                "match": dict(fault.match),
                "first_n": fault.first_n,
                "params": params,
            }
        )
    return faults


def _ground_truth(spec: ScenarioSpec, change_ids: dict[str, str]) -> dict[str, Any]:
    truth = spec.ground_truth
    key_evidence: list[dict[str, Any]] = []
    for item in truth.key_evidence:
        if item.kind == "change" and item.change is not None:
            key_evidence.append({"kind": "change", "id": change_ids[item.change]})
        elif item.kind == "metric_series":
            key_evidence.append(
                {"kind": "metric_series", "service": item.service, "metric": item.metric}
            )
        else:
            key_evidence.append(
                {"kind": "log_pattern", "service": item.service, "contains": item.contains}
            )
    return {
        "incident_id": spec.id,
        "slug": spec.slug,
        "expected_status": truth.expected_status,
        "services": truth.services,
        "categories": list(truth.categories),
        "trigger_id": change_ids[truth.trigger] if truth.trigger else None,
        "key_evidence": key_evidence,
        "red_herring_ids": [change_ids[key] for key in truth.red_herring_changes],
        "red_herring_services": truth.red_herring_services,
        "injected_conclusion_service": truth.injected_conclusion_service,
        "data_gap_keywords": truth.data_gap_keywords,
        "tags": truth.tags,
        "notes": truth.notes.strip(),
    }


def _write_json(path: Path, document: Any) -> None:
    path.write_text(
        json.dumps(document, sort_keys=True, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    lines = [json.dumps(row, sort_keys=True, ensure_ascii=False) for row in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    root = Path.cwd()
    for path in generate_all(
        root / "datasets" / "scenarios",
        root / "datasets" / "platform.yaml",
        root / "datasets" / "generated",
    ):
        print(f"generated {path}")


if __name__ == "__main__":
    main()
