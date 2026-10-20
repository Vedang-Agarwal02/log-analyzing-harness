from collections import Counter
from pathlib import Path

import pytest
import yaml

from investigator.datagen.platform import Platform, load_platform


def test_platform_loads_and_is_consistent(repo_root: Path) -> None:
    platform = load_platform(repo_root / "datasets" / "platform.yaml")
    assert len(platform.services) == 21
    kinds = Counter(service.kind for service in platform.services)
    assert kinds == {"service": 10, "datastore": 7, "infrastructure": 1, "external": 3}
    orders = platform.metrics_for("orders-service")
    assert orders["request_rate_rps"].baseline == 120
    assert orders["db_pool_wait_ms"].definition.unit == "ms"
    assert list(orders) == sorted(orders)
    assert platform.metrics_for("bank-gateway") == {}
    assert len(platform.history) == 16
    assert len({entry.key for entry in platform.history}) == 16
    assert platform.background_changes.deploys == 8


def test_platform_rejects_unknown_dependency(repo_root: Path) -> None:
    data = yaml.safe_load((repo_root / "datasets" / "platform.yaml").read_text(encoding="utf-8"))
    data["services"][0]["depends_on"] = ["ghost-service"]
    platform = Platform.model_validate(data)
    with pytest.raises(ValueError, match="ghost-service"):
        platform.validate_references()


def test_platform_rejects_missing_baseline(repo_root: Path) -> None:
    data = yaml.safe_load((repo_root / "datasets" / "platform.yaml").read_text(encoding="utf-8"))
    del data["services"][0]["baselines"]["cpu_pct"]
    platform = Platform.model_validate(data)
    with pytest.raises(ValueError, match="cpu_pct"):
        platform.validate_references()
