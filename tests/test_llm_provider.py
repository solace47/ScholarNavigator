from __future__ import annotations

import json
import socket
from urllib.error import HTTPError, URLError

import pytest

from scholar_agent.llm import provider


class FakeResponse:
    def __init__(self, body: dict[str, object]) -> None:
        self._body = json.dumps(body).encode("utf-8")

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def test_llm_disabled_when_provider_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_llm_env(monkeypatch)

    runtime = provider.get_llm_runtime_config()

    assert provider.is_llm_enabled() is False
    assert runtime.provider == "disabled"
    assert runtime.available is False
    assert runtime.reason == "provider_disabled"


def test_openai_compatible_missing_api_key_is_not_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv(provider.PROVIDER_ENV, provider.SUPPORTED_PROVIDER)
    monkeypatch.setenv(provider.BASE_URL_ENV, "https://api.example.test/v1")
    monkeypatch.setenv(provider.MODEL_ENV, "gpt-test")

    runtime = provider.get_llm_runtime_config().model_dump()

    assert runtime["available"] is False
    assert runtime["provider"] == "openai_compatible"
    assert runtime["model"] == "gpt-test"
    assert runtime["base_url_host"] == "api.example.test"
    assert provider.API_KEY_ENV in runtime["reason"]
    assert "secret" not in json.dumps(runtime)


def test_runtime_config_does_not_expose_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_enabled_env(monkeypatch)

    runtime = provider.get_llm_runtime_config().model_dump()

    assert runtime["available"] is True
    assert runtime["provider"] == "openai_compatible"
    assert runtime["base_url_host"] == "api.example.test"
    assert "sk-test-secret" not in json.dumps(runtime)


def test_chat_json_parses_openai_compatible_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_enabled_env(monkeypatch)
    captured: dict[str, object] = {}

    def fake_urlopen(request, timeout: float):  # noqa: ANN001
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["authorization"] = request.headers["Authorization"]
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "language": "en",
                                    "intent": "recent_progress",
                                    "domain": "machine_learning",
                                }
                            )
                        }
                    }
                ]
            }
        )

    monkeypatch.setattr(provider, "urlopen", fake_urlopen)

    result = provider.chat_json(
        [{"role": "user", "content": "Analyze LLM reranking."}],
        timeout=3,
    )

    assert result["intent"] == "recent_progress"
    assert captured["url"] == "https://api.example.test/v1/chat/completions"
    assert captured["timeout"] == 3
    assert captured["authorization"] == "Bearer sk-test-secret"
    assert captured["payload"]["max_tokens"] == provider.DEFAULT_MAX_TOKENS
    assert captured["payload"]["response_format"] == {"type": "json_object"}
    assert captured["payload"]["extra_body"] == {
        "chat_template_kwargs": {"thinking": False}
    }


def test_chat_json_uses_configured_max_tokens_and_thinking_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_enabled_env(monkeypatch)
    monkeypatch.setenv(provider.MAX_TOKENS_ENV, "256")
    monkeypatch.setenv(provider.NVIDIA_THINKING_ENV, "false")
    captured: dict[str, object] = {}

    def fake_urlopen(request, timeout: float):  # noqa: ANN001
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse(
            {
                "choices": [
                    {"message": {"content": json.dumps({"ok": True})}},
                ]
            }
        )

    monkeypatch.setattr(provider, "urlopen", fake_urlopen)

    result = provider.chat_json([{"role": "user", "content": "query"}])

    assert result == {"ok": True}
    assert captured["payload"]["max_tokens"] == 256
    assert captured["payload"]["extra_body"] == {
        "chat_template_kwargs": {"thinking": False}
    }


@pytest.mark.parametrize("raw_value", ["0", "-1", "not-an-int"])
def test_chat_json_invalid_max_tokens_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
    raw_value: str,
) -> None:
    _set_enabled_env(monkeypatch)
    monkeypatch.setenv(provider.MAX_TOKENS_ENV, raw_value)
    captured: dict[str, object] = {}

    def fake_urlopen(request, timeout: float):  # noqa: ANN001
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse(
            {
                "choices": [
                    {"message": {"content": json.dumps({"ok": True})}},
                ]
            }
        )

    monkeypatch.setattr(provider, "urlopen", fake_urlopen)

    assert provider.chat_json([{"role": "user", "content": "query"}]) == {"ok": True}
    assert captured["payload"]["max_tokens"] == provider.DEFAULT_MAX_TOKENS


def test_chat_json_timeout_raises_sanitized_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_enabled_env(monkeypatch)

    def fake_urlopen(*args, **kwargs):  # noqa: ANN002, ANN003
        raise socket.timeout("timed out with sk-test-secret")

    monkeypatch.setattr(provider, "urlopen", fake_urlopen)

    with pytest.raises(provider.LLMTimeoutError, match="llm_request_timeout"):
        provider.chat_json([{"role": "user", "content": "query"}])


def test_chat_json_http_error_redacts_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_enabled_env(monkeypatch)

    def fake_urlopen(*args, **kwargs):  # noqa: ANN002, ANN003
        raise HTTPError(
            url="https://api.example.test/v1/chat/completions",
            code=503,
            msg="Service Unavailable",
            hdrs=None,
            fp=FakeErrorBody("upstream sk-test-secret failed"),
        )

    monkeypatch.setattr(provider, "urlopen", fake_urlopen)

    with pytest.raises(provider.LLMProviderError) as exc_info:
        provider.chat_json([{"role": "user", "content": "query"}])

    message = str(exc_info.value)
    assert "llm_http_error:503" in message
    assert "sk-test-secret" not in message


def test_chat_json_url_error_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_enabled_env(monkeypatch)

    def fake_urlopen(*args, **kwargs):  # noqa: ANN002, ANN003
        raise URLError("network down")

    monkeypatch.setattr(provider, "urlopen", fake_urlopen)

    with pytest.raises(provider.LLMProviderError, match="llm_url_error:network down"):
        provider.chat_json([{"role": "user", "content": "query"}])


def test_chat_json_rejects_non_json_content(monkeypatch: pytest.MonkeyPatch) -> None:
    client = provider.OpenAICompatibleLLMClient(
        base_url="https://api.example.test/v1",
        api_key="sk-test-secret",
        model="gpt-test",
    )

    def fake_urlopen(*args, **kwargs):  # noqa: ANN002, ANN003
        return FakeResponse(
            {"choices": [{"message": {"content": "not json"}}]},
        )

    monkeypatch.setattr(provider, "urlopen", fake_urlopen)

    with pytest.raises(provider.LLMResponseError, match="llm_invalid_json_content"):
        client.chat_json([{"role": "user", "content": "query"}])


class FakeErrorBody:
    def __init__(self, message: str) -> None:
        self._body = json.dumps({"error": {"message": message}}).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def close(self) -> None:
        return None


def _clear_llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for env_name in (
        provider.PROVIDER_ENV,
        provider.BASE_URL_ENV,
        provider.API_KEY_ENV,
        provider.MODEL_ENV,
        provider.TIMEOUT_ENV,
        provider.MAX_TOKENS_ENV,
        provider.NVIDIA_THINKING_ENV,
    ):
        monkeypatch.delenv(env_name, raising=False)


def _set_enabled_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv(provider.PROVIDER_ENV, provider.SUPPORTED_PROVIDER)
    monkeypatch.setenv(provider.BASE_URL_ENV, "https://api.example.test/v1")
    monkeypatch.setenv(provider.API_KEY_ENV, "sk-test-secret")
    monkeypatch.setenv(provider.MODEL_ENV, "gpt-test")
