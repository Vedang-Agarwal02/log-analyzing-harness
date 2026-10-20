from pathlib import Path

from investigator.settings import Settings, load_settings, project_root


def test_default_yaml_loads(repo_root: Path) -> None:
    settings = load_settings(repo_root / "config" / "default.yaml")
    assert settings.llm.model == "claude-opus-5-5"
    assert settings.investigation.max_rounds == 3
    assert settings.budget.limits().reserve_tokens == 150_000
    config = settings.tools.executor_config()
    assert config.retry.max_attempts == 3 and config.default_timeout_s == 8.0
    assert settings.llm.anthropic_settings().refusal_fallback is True
    assert settings.llm.gemini_settings().tool_mode == "AUTO"


def test_overrides_deep_merge(repo_root: Path) -> None:
    settings = load_settings(
        repo_root / "config" / "default.yaml",
        {"llm": {"model": "claude-haiku-5-5"}, "budget": {"max_llm_calls": 5}},
    )
    assert settings.llm.model == "claude-haiku-5-5"
    assert settings.llm.max_tokens == 32000
    assert settings.budget.max_llm_calls == 5 and settings.budget.max_tool_calls == 120


def test_paths_resolve_against_project_root(repo_root: Path) -> None:
    assert project_root() == repo_root
    settings = Settings()
    assert settings.path(settings.agents_file) == repo_root / "config" / "agents.yaml"
    assert settings.path("/tmp/x") == Path("/tmp/x")
