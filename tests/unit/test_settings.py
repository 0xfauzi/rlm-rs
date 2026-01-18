from __future__ import annotations

from rlm_rs.settings import Settings


def test_settings_env_parsing(monkeypatch) -> None:
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("DEFAULT_BUDGETS_JSON", '{"max_turns": 5}')
    monkeypatch.setenv("ENABLE_SEARCH_DEFAULT", "true")

    settings = Settings()

    assert settings.aws_region == "us-east-1"
    assert settings.default_budgets_json == {"max_turns": 5}
    assert settings.enable_search is True
