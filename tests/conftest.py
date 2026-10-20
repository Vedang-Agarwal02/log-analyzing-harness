"""Shared pytest fixtures.

Fixtures import project modules inside their bodies so that this file works
before those modules exist (they are created in later tasks).
"""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def generated_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Generate every scenario once per test session into a temporary folder."""
    from investigator.datagen.generate import generate_all

    out = tmp_path_factory.mktemp("generated")
    generate_all(
        REPO_ROOT / "datasets" / "scenarios",
        REPO_ROOT / "datasets" / "platform.yaml",
        out,
    )
    return out


@pytest.fixture
def test_settings(generated_root: Path):
    """Settings for offline tests: generated data, no simulated latency, no backoff sleeps."""
    from investigator.settings import Settings

    return Settings.model_validate(
        {
            "llm": {"model": "scripted"},
            "datasets_dir": str(generated_root),
            "simulation": {"latency_ms_min": 0, "latency_ms_max": 0},
            "tools": {"backoff_base_s": 0.0, "backoff_max_s": 0.0, "max_rate_limit_wait_s": 0.0},
        }
    )
