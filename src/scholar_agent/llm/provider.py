"""OpenAI-compatible LLM provider utilities."""

from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


PROVIDER_ENV = "SCHOLAR_AGENT_LLM_PROVIDER"
BASE_URL_ENV = "SCHOLAR_AGENT_LLM_BASE_URL"
API_KEY_ENV = "SCHOLAR_AGENT_LLM_API_KEY"
MODEL_ENV = "SCHOLAR_AGENT_LLM_MODEL"
TIMEOUT_ENV = "SCHOLAR_AGENT_LLM_TIMEOUT_SECONDS"
MAX_TOKENS_ENV = "SCHOLAR_AGENT_LLM_MAX_TOKENS"
NVIDIA_THINKING_ENV = "SCHOLAR_AGENT_LLM_NVIDIA_THINKING"

SUPPORTED_PROVIDER = "openai_compatible"
DISABLED_PROVIDER = "disabled"
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_TOKENS = 1024


class LLMProviderError(RuntimeError):
    """Base error for LLM provider failures."""


class LLMConfigurationError(LLMProviderError):
    """Raised when the configured provider is invalid or incomplete."""


class LLMTimeoutError(LLMProviderError):
    """Raised when an LLM request times out."""


class LLMResponseError(LLMProviderError):
    """Raised when an LLM response cannot be parsed as a JSON object."""


@dataclass(frozen=True)
class LLMRuntimeConfig:
    provider: str
    model: str | None
    available: bool
    base_url_host: str | None = None
    reason: str | None = None

    def model_dump(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "available": self.available,
            "base_url_host": self.base_url_host,
            "reason": self.reason,
        }


def is_llm_enabled() -> bool:
    return get_llm_runtime_config().available


def get_llm_runtime_config() -> LLMRuntimeConfig:
    provider = _provider_name()
    if provider == DISABLED_PROVIDER:
        return LLMRuntimeConfig(
            provider=DISABLED_PROVIDER,
            model=os.getenv(MODEL_ENV),
            available=False,
            base_url_host=_base_url_host(os.getenv(BASE_URL_ENV)),
            reason="provider_disabled",
        )

    if provider != SUPPORTED_PROVIDER:
        return LLMRuntimeConfig(
            provider=provider,
            model=os.getenv(MODEL_ENV),
            available=False,
            base_url_host=_base_url_host(os.getenv(BASE_URL_ENV)),
            reason="unsupported_provider",
        )

    base_url = os.getenv(BASE_URL_ENV, "").strip()
    api_key = os.getenv(API_KEY_ENV, "").strip()
    model = os.getenv(MODEL_ENV, "").strip()
    missing = []
    if not base_url:
        missing.append(BASE_URL_ENV)
    if not api_key:
        missing.append(API_KEY_ENV)
    if not model:
        missing.append(MODEL_ENV)
    if missing:
        return LLMRuntimeConfig(
            provider=SUPPORTED_PROVIDER,
            model=model or None,
            available=False,
            base_url_host=_base_url_host(base_url),
            reason="missing_env:" + ",".join(missing),
        )

    return LLMRuntimeConfig(
        provider=SUPPORTED_PROVIDER,
        model=model,
        available=True,
        base_url_host=_base_url_host(base_url),
    )


def chat_json(
    messages: list[dict[str, str]],
    *,
    temperature: float = 0,
    timeout: float | None = None,
) -> dict[str, Any]:
    return OpenAICompatibleLLMClient.from_env().chat_json(
        messages,
        temperature=temperature,
        timeout=timeout,
    )


@dataclass
class OpenAICompatibleLLMClient:
    base_url: str
    api_key: str
    model: str
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    @classmethod
    def from_env(cls) -> "OpenAICompatibleLLMClient":
        config = get_llm_runtime_config()
        if not config.available:
            raise LLMConfigurationError(config.reason or "llm_disabled")
        return cls(
            base_url=os.environ[BASE_URL_ENV].strip(),
            api_key=os.environ[API_KEY_ENV].strip(),
            model=os.environ[MODEL_ENV].strip(),
            timeout_seconds=_timeout_from_env(),
        )

    def chat_json(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": _max_tokens_from_env(),
            "response_format": {"type": "json_object"},
        }
        if not _nvidia_thinking_from_env():
            payload["extra_body"] = {
                "chat_template_kwargs": {
                    "thinking": False,
                }
            }
        request = Request(
            _chat_completions_url(self.base_url),
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(  # noqa: S310 - URL is configured by trusted backend env.
                request,
                timeout=timeout if timeout is not None else self.timeout_seconds,
            ) as response:
                response_body = response.read().decode("utf-8")
        except socket.timeout as exc:
            raise LLMTimeoutError("llm_request_timeout") from exc
        except HTTPError as exc:
            message = f"llm_http_error:{exc.code}"
            try:
                error_body = exc.read().decode("utf-8")
                parsed = json.loads(error_body)
                detail = parsed.get("error", {}).get("message")
                if detail:
                    message = f"{message}:{_sanitize_error_message(str(detail))}"
            except Exception:
                pass
            raise LLMProviderError(message) from exc
        except URLError as exc:
            raise LLMProviderError(
                f"llm_url_error:{_sanitize_error_message(str(exc.reason))}"
            ) from exc

        try:
            parsed_response = json.loads(response_body)
            content = parsed_response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise LLMResponseError("llm_malformed_chat_response") from exc

        try:
            parsed_content = json.loads(content)
        except json.JSONDecodeError as exc:
            raise LLMResponseError("llm_invalid_json_content") from exc
        if not isinstance(parsed_content, dict):
            raise LLMResponseError("llm_json_content_not_object")
        return parsed_content


def _provider_name() -> str:
    raw_provider = os.getenv(PROVIDER_ENV)
    if raw_provider is None or not raw_provider.strip():
        return DISABLED_PROVIDER
    return raw_provider.strip().lower()


def _timeout_from_env() -> float:
    raw_value = os.getenv(TIMEOUT_ENV)
    if raw_value is None:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        timeout = float(raw_value)
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS
    return timeout if timeout > 0 else DEFAULT_TIMEOUT_SECONDS


def _max_tokens_from_env() -> int:
    raw_value = os.getenv(MAX_TOKENS_ENV)
    if raw_value is None:
        return DEFAULT_MAX_TOKENS
    try:
        max_tokens = int(raw_value)
    except ValueError:
        return DEFAULT_MAX_TOKENS
    return max_tokens if max_tokens >= 1 else DEFAULT_MAX_TOKENS


def _nvidia_thinking_from_env() -> bool:
    raw_value = os.getenv(NVIDIA_THINKING_ENV)
    if raw_value is None:
        return False
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _chat_completions_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/chat/completions"):
        return normalized
    return f"{normalized}/chat/completions"


def _base_url_host(base_url: str | None) -> str | None:
    if not base_url:
        return None
    parsed = urlparse(base_url)
    return parsed.netloc or None


def _sanitize_error_message(message: str) -> str:
    api_key = os.getenv(API_KEY_ENV, "")
    sanitized = message.replace("\n", " ").strip()
    if api_key:
        sanitized = sanitized.replace(api_key, "[redacted]")
    return sanitized[:240]
