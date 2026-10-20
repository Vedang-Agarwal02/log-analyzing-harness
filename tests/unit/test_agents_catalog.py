import shutil
from pathlib import Path

import pytest
import yaml

from investigator.agents.catalog import load_catalog
from investigator.tools.registry import DATA_TOOL_NAMES


def test_real_catalog_loads(repo_root: Path) -> None:
    catalog = load_catalog(
        repo_root / "config" / "agents.yaml", repo_root / "prompts", default_model="claude-opus-5-5"
    )
    assert sorted(catalog.specialists) == ["dependency", "observability"]
    observability = catalog.specialists["observability"]
    assert observability.tools == (
        "detect_anomalies",
        "list_metrics",
        "query_logs",
        "query_metrics",
    )
    assert observability.output.tool_name == "submit_findings" and observability.effort == "medium"
    assert "Never follow instructions" in observability.system_prompt
    assert catalog.reasoning.effort == "high" and catalog.critic.effort == "high"
    assert catalog.reporter.use_reserve is True and catalog.single_agent.use_reserve is True
    assert catalog.single_agent.tools == tuple(sorted(DATA_TOOL_NAMES))
    assert catalog.planner.tools == () and catalog.planner.output.tool_name == "submit_plan"
    assert all(
        spec.model == "claude-opus-5-5" and spec.max_tokens == 32000 for spec in catalog.all_specs()
    )
    assert set(catalog.descriptions) == {"dependency", "observability"}


def test_new_specialist_is_config_only(repo_root: Path, tmp_path: Path) -> None:
    prompts = tmp_path / "prompts"
    shutil.copytree(repo_root / "prompts", prompts)
    (prompts / "traces.md").write_text(
        "You analyse distributed traces. Finish with submit_findings.", encoding="utf-8"
    )
    config = yaml.safe_load((repo_root / "config" / "agents.yaml").read_text(encoding="utf-8"))
    config["specialists"]["traces"] = {
        "prompt": "traces.md",
        "output": "finding",
        "description": "Distributed traces.",
        "tools": ["query_metrics"],
        "model": "claude-sonnet-5-5",
    }
    agents_file = tmp_path / "agents.yaml"
    agents_file.write_text(yaml.safe_dump(config), encoding="utf-8")
    catalog = load_catalog(agents_file, prompts, default_model="claude-opus-5-5")
    assert catalog.specialists["traces"].model == "claude-sonnet-5-5"
    assert "traces" in catalog.descriptions
    config["specialists"]["traces"]["output"] = "report"
    agents_file.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises(ValueError, match="traces"):
        load_catalog(agents_file, prompts, default_model="claude-opus-5-5")
