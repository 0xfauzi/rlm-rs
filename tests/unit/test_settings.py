from __future__ import annotations

from rlm_rs.settings import Settings


def test_settings_env_parsing(monkeypatch) -> None:
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("DEFAULT_BUDGETS_JSON", '{"max_turns": 5}')
    monkeypatch.setenv("ENABLE_SEARCH_DEFAULT", "true")
    monkeypatch.setenv("SANDBOX_RUNNER", "lambda")
    monkeypatch.setenv("SANDBOX_LAMBDA_FUNCTION_NAME", "rlm-sandbox-step")
    monkeypatch.setenv("SANDBOX_LAMBDA_TIMEOUT_SECONDS", "12.5")
    monkeypatch.setenv("ENABLE_ROOT_STATE_SUMMARY", "true")
    monkeypatch.setenv("TOOL_RESOLUTION_MAX_CONCURRENCY", "3")

    settings = Settings()

    assert settings.aws_region == "us-east-1"
    assert settings.default_budgets_json == {"max_turns": 5}
    assert settings.enable_search is True
    assert settings.sandbox_runner == "lambda"
    assert settings.sandbox_lambda_function_name == "rlm-sandbox-step"
    assert settings.sandbox_lambda_timeout_seconds == 12.5
    assert settings.enable_root_state_summary is True
    assert settings.tool_resolution_max_concurrency == 3
