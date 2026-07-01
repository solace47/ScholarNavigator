from __future__ import annotations

import os

from scholar_agent.core.env_loader import load_env_file


ENV_KEYS = (
    "SCHOLAR_AGENT_LLM_PROVIDER",
    "SCHOLAR_AGENT_LLM_MODEL",
    "SCHOLAR_AGENT_LLM_API_KEY",
    "SCHOLAR_AGENT_ENABLE_LLM_JUDGEMENT",
    "SCHOLAR_AGENT_LLM_BASE_URL",
    "OPENALEX_MAILTO",
)


def test_load_env_file_sets_values_without_overriding_existing_env(
    tmp_path,
    monkeypatch,
) -> None:
    original_env = {key: os.environ.get(key) for key in ENV_KEYS}
    for key in ENV_KEYS:
        os.environ.pop(key, None)
    try:
        env_file = tmp_path / ".env"
        env_file.write_text(
            "\n".join(
                [
                    "SCHOLAR_AGENT_LLM_PROVIDER=openai_compatible",
                    "SCHOLAR_AGENT_LLM_MODEL=gpt-4.1-mini",
                    "SCHOLAR_AGENT_LLM_API_KEY=file-key",
                    "SCHOLAR_AGENT_ENABLE_LLM_JUDGEMENT=1 # enable judgement",
                    'SCHOLAR_AGENT_LLM_BASE_URL="https://api.example.test/v1"',
                    "export OPENALEX_MAILTO=team@example.test",
                ]
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("SCHOLAR_AGENT_LLM_API_KEY", "shell-key")

        loaded = load_env_file(env_file)

        assert loaded is True
        assert os.environ["SCHOLAR_AGENT_LLM_PROVIDER"] == "openai_compatible"
        assert os.environ["SCHOLAR_AGENT_LLM_MODEL"] == "gpt-4.1-mini"
        assert os.environ["SCHOLAR_AGENT_LLM_API_KEY"] == "shell-key"
        assert os.environ["SCHOLAR_AGENT_ENABLE_LLM_JUDGEMENT"] == "1"
        assert os.environ["SCHOLAR_AGENT_LLM_BASE_URL"] == (
            "https://api.example.test/v1"
        )
        assert os.environ["OPENALEX_MAILTO"] == "team@example.test"
    finally:
        for key in ENV_KEYS:
            os.environ.pop(key, None)
            if original_env[key] is not None:
                os.environ[key] = original_env[key]


def test_load_env_file_can_override_when_requested(tmp_path, monkeypatch) -> None:
    original_api_key = os.environ.get("SCHOLAR_AGENT_LLM_API_KEY")
    try:
        env_file = tmp_path / ".env"
        env_file.write_text("SCHOLAR_AGENT_LLM_API_KEY=file-key\n", encoding="utf-8")
        monkeypatch.setenv("SCHOLAR_AGENT_LLM_API_KEY", "shell-key")

        load_env_file(env_file, override=True)

        assert os.environ["SCHOLAR_AGENT_LLM_API_KEY"] == "file-key"
    finally:
        os.environ.pop("SCHOLAR_AGENT_LLM_API_KEY", None)
        if original_api_key is not None:
            os.environ["SCHOLAR_AGENT_LLM_API_KEY"] = original_api_key


def test_load_env_file_ignores_missing_file(tmp_path) -> None:
    assert load_env_file(tmp_path / "missing.env") is False
