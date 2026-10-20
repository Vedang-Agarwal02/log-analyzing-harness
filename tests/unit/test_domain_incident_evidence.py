import pytest
from pydantic import ValidationError

from investigator.domain.evidence import Evidence, EvidenceKind, EvidenceStore, Statement
from investigator.domain.incident import IncidentReport, ServiceInfo


def _ev(evidence_id: str, kind: EvidenceKind = "deployment") -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        kind=kind,
        service="orders-service",
        summary=f"summary {evidence_id}",
        source_tool="get_deployments",
    )


def test_evidence_store_add_get_has_missing_sorted_count() -> None:
    store = EvidenceStore()
    store.add(_ev("dep-0002"))
    store.add(_ev("dep-0001"))
    assert store.has("dep-0001")
    assert not store.has("dep-9999")
    found = store.get("dep-0002")
    assert found is not None and found.summary == "summary dep-0002"
    assert store.get("nope") is None
    assert store.missing(["dep-0001", "dep-9999", "cfg-0001", "dep-9999"]) == [
        "cfg-0001",
        "dep-9999",
    ]
    assert [e.evidence_id for e in store.sorted_items()] == ["dep-0001", "dep-0002"]
    assert store.count() == 2


def test_evidence_store_merge_is_order_independent_and_round_trips_json() -> None:
    store = EvidenceStore()
    store.add(_ev("dep-0001"))
    store.add(_ev("dep-0001").model_copy(update={"summary": "updated"}))
    assert store.count() == 1
    assert store.get("dep-0001").summary == "updated"  # type: ignore[union-attr]
    first = _ev("dep-0002").model_copy(update={"source_call_id": "call-b", "summary": "B"})
    later = _ev("dep-0002").model_copy(update={"source_call_id": "call-c", "summary": "C"})
    earlier = _ev("dep-0002").model_copy(update={"source_call_id": "call-a", "summary": "A"})
    store.add(first)
    store.add(later)
    assert store.get("dep-0002").summary == "B"  # type: ignore[union-attr]
    store.add(earlier)
    assert store.get("dep-0002").summary == "A"  # type: ignore[union-attr]
    clone = EvidenceStore.model_validate_json(store.model_dump_json())
    assert clone.get("dep-0001") == store.get("dep-0001")


def test_statement_defaults_to_no_evidence() -> None:
    statement = Statement(text="Pool size was reduced", kind="assumption")
    assert statement.evidence_ids == []


def test_incident_report_parses_payload_and_defaults() -> None:
    incident = IncidentReport.model_validate(
        {
            "incident_id": "INC-X",
            "title": "Checkout failing",
            "severity": "SEV2",
            "reported_at": "2026-03-10T10:21:00Z",
            "reporter": "on-call",
            "summary": "Errors since 10:12",
        }
    )
    assert incident.affected_services == []
    assert incident.initial_hypothesis is None
    assert incident.reported_at.isoformat() == "2026-03-10T10:21:00+00:00"


def test_service_info_kind_is_validated() -> None:
    with pytest.raises(ValidationError):
        ServiceInfo.model_validate(
            {"name": "x", "kind": "banana", "tier": "core", "team": "t", "description": "d"}
        )
