"""LLM integration: JSON parsing, retry policy, structured-output fallback, timeouts, log hygiene."""
import logging
from types import SimpleNamespace

import httpx
import openai
import pytest

from app.core.config import settings
from app.services.ai import llm
from app.services.ai.llm import (
    AnthropicProvider, GeminiProvider, LLMProvider, OllamaProvider, OpenAIProvider, _parse_json_response,
    get_llm_provider,
)

SECRET = "sk-super-secret-key-1234567890"


# --------------------------------------------------------------------------- parsing

def test_plain_json_object():
    assert _parse_json_response('{"highlights": []}') == {"highlights": []}


def test_markdown_fenced_json_with_and_without_a_language_tag():
    assert _parse_json_response('```json\n{"a": 1}\n```') == {"a": 1}
    assert _parse_json_response('```\n{"a": 1}\n```') == {"a": 1}
    assert _parse_json_response('  ```JSON\n{"a": {"b": [1, 2]}}\n```  ') == {"a": {"b": [1, 2]}}


def test_json_surrounded_by_prose_is_still_found():
    assert _parse_json_response('Sure! Here you go:\n{"highlights": [{"score": 1}]}\nHope that helps.') == {
        "highlights": [{"score": 1}]
    }


@pytest.mark.parametrize("raw", [None, "", "   \n  ", "```json\n```"])
def test_an_empty_reply_is_an_error(raw):
    with pytest.raises(ValueError):
        _parse_json_response(raw)


@pytest.mark.parametrize("raw", ["not json at all", '{"a": 1', "{'a': 1}", "```json\n{broken\n```", "<html>502</html>"])
def test_malformed_json_is_an_error(raw):
    with pytest.raises(ValueError, match="valid JSON"):
        _parse_json_response(raw)


@pytest.mark.parametrize("raw,kind", [("[1, 2]", "list"), ('"text"', "str"), ("42", "int"), ("null", "NoneType")])
def test_json_that_is_not_an_object_is_an_error(raw, kind):
    with pytest.raises(ValueError, match="not an object"):
        _parse_json_response(raw)


# --------------------------------------------------------------------------- fakes

def _reply(content):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


class _FakeCompletions:
    """chat.completions: each call takes the next outcome — an exception to raise or text to return."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return _reply(outcome)


def _provider(*outcomes, cls=OpenAIProvider):
    provider = cls()
    completions = _FakeCompletions(*outcomes)
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return provider, completions


def _http_error(cls, status, message="boom"):
    request = httpx.Request("POST", "http://router.test/v1/chat/completions")
    response = httpx.Response(status, request=request)
    return cls(message, response=response, body=None)


# --------------------------------------------------------------------------- calls

def test_a_valid_reply_is_parsed_and_the_configured_model_is_used(mocker):
    mocker.patch.object(settings, "OPENAI_MODEL", "whatever-route/claude-primary")
    provider, completions = _provider('{"highlights": []}')

    assert provider.complete_json("sys", "user") == {"highlights": []}

    call = completions.calls[0]
    assert call["model"] == "whatever-route/claude-primary"  # never hardcoded, never swapped
    assert call["response_format"] == {"type": "json_object"}
    assert [m["role"] for m in call["messages"]] == ["system", "user"]


def test_a_fenced_reply_works_end_to_end():
    provider, _ = _provider('```json\n{"ok": true}\n```')
    assert provider.complete_json("s", "u") == {"ok": True}


def test_an_empty_reply_is_asked_for_again_once(mocker):
    mocker.patch.object(settings, "LLM_JSON_RETRIES", 1)
    provider, completions = _provider("", '{"ok": true}')

    assert provider.complete_json("s", "u") == {"ok": True}
    assert len(completions.calls) == 2


def test_a_reply_that_stays_unusable_fails_after_the_configured_attempts(mocker):
    mocker.patch.object(settings, "LLM_JSON_RETRIES", 2)
    provider, completions = _provider("nope", "still nope", "{broken")

    with pytest.raises(ValueError):
        provider.complete_json("s", "u")
    assert len(completions.calls) == 3


def test_json_retries_can_be_switched_off(mocker):
    mocker.patch.object(settings, "LLM_JSON_RETRIES", 0)
    provider, completions = _provider("nope", '{"ok": true}')
    with pytest.raises(ValueError):
        provider.complete_json("s", "u")
    assert len(completions.calls) == 1


def test_a_response_with_no_choices_is_treated_as_empty():
    provider = OpenAIProvider()
    completions = SimpleNamespace(create=lambda **kw: SimpleNamespace(choices=[]))
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    with pytest.raises(ValueError, match="empty"):
        provider.complete_json("s", "u")


# --------------------------------------------------------------------------- structured output

def test_a_route_without_json_mode_is_retried_without_it_on_the_same_model(mocker):
    mocker.patch.object(settings, "OPENAI_MODEL", "route-x")
    unsupported = _http_error(openai.BadRequestError, 400, "Unsupported parameter: response_format is not supported")
    provider, completions = _provider(unsupported, '{"ok": true}', '{"ok": 2}')

    assert provider.complete_json("sys", "u") == {"ok": True}
    first, second = completions.calls
    assert "response_format" in first and "response_format" not in second
    assert first["model"] == second["model"] == "route-x"  # no silent model switch
    assert "JSON only" in second["messages"][0]["content"]  # nothing forces JSON now, so the prompt asks

    # and it remembers: the next call doesn't try json mode again
    assert provider.complete_json("sys", "u") == {"ok": 2}
    assert "response_format" not in completions.calls[2]


def test_an_unrelated_bad_request_is_not_mistaken_for_missing_json_mode():
    bad_model = _http_error(openai.BadRequestError, 400, "The model `nope` does not exist")
    provider, completions = _provider(bad_model, '{"ok": true}')

    with pytest.raises(openai.BadRequestError):
        provider.complete_json("s", "u")
    assert len(completions.calls) == 1  # retrying couldn't help


# --------------------------------------------------------------------------- errors, retries, logging

def test_a_non_retryable_error_is_raised_at_once_without_another_call():
    provider, completions = _provider(_http_error(openai.AuthenticationError, 401, f"Incorrect API key {SECRET}"))
    with pytest.raises(openai.AuthenticationError):
        provider.complete_json("s", "u")
    assert len(completions.calls) == 1


def test_a_timeout_propagates_for_the_caller_to_fall_back_and_is_logged_without_secrets(caplog):
    timeout = openai.APITimeoutError(request=httpx.Request("POST", "http://router.test"))
    provider, completions = _provider(timeout)

    with caplog.at_level(logging.WARNING, logger="app.services.ai.llm"):
        with pytest.raises(openai.APITimeoutError):
            provider.complete_json("system prompt text", "private transcript text")

    # the SDK already retried transient failures inside the call; we don't stack a second retry on top
    assert len(completions.calls) == 1
    assert "llm_request_failed" in caplog.text and "APITimeoutError" in caplog.text
    assert "private transcript text" not in caplog.text and "system prompt text" not in caplog.text


def test_error_messages_that_echo_credentials_never_reach_the_log(caplog):
    provider, _ = _provider(_http_error(openai.AuthenticationError, 401, f"Incorrect API key provided: {SECRET}"))
    with caplog.at_level(logging.DEBUG, logger="app.services.ai.llm"):
        with pytest.raises(openai.AuthenticationError):
            provider.complete_json("s", "u")
    assert SECRET not in caplog.text
    assert "status=401" in caplog.text and "AuthenticationError" in caplog.text


def test_unusable_replies_are_logged_with_attempt_numbers(caplog):
    provider, _ = _provider("", "also bad")
    with caplog.at_level(logging.WARNING, logger="app.services.ai.llm"):
        with pytest.raises(ValueError):
            provider.complete_json("s", "u")
    assert "llm_unusable_response" in caplog.text and "attempt=1/2" in caplog.text and "attempt=2/2" in caplog.text


# --------------------------------------------------------------------------- client configuration

def test_the_client_gets_the_configured_timeout_retries_and_router_url(mocker):
    created = {}

    def fake_openai(**kwargs):
        created.update(kwargs)
        return SimpleNamespace()

    mocker.patch("openai.OpenAI", side_effect=fake_openai)
    mocker.patch.object(settings, "OPENAI_BASE_URL", "http://host.docker.internal:20128/v1")
    mocker.patch.object(settings, "OPENAI_API_KEY", SECRET)
    mocker.patch.object(settings, "LLM_TIMEOUT_SECONDS", 12.5)
    mocker.patch.object(settings, "LLM_MAX_RETRIES", 4)

    OpenAIProvider()._get_client()

    assert created["base_url"] == "http://host.docker.internal:20128/v1"
    assert created["timeout"] == 12.5 and created["max_retries"] == 4


def test_without_a_base_url_the_official_endpoint_is_used(mocker):
    created = {}
    mocker.patch("openai.OpenAI", side_effect=lambda **kw: created.update(kw) or SimpleNamespace())
    mocker.patch.object(settings, "OPENAI_BASE_URL", None)
    OpenAIProvider()._get_client()
    assert created["base_url"] == "https://api.openai.com/v1"


def test_provider_selection_is_unchanged(mocker):
    for name, cls in [("openai", OpenAIProvider), ("anthropic", AnthropicProvider), ("ollama", OllamaProvider), ("gemini", GeminiProvider)]:
        mocker.patch.object(settings, "AI_LLM_PROVIDER", name)
        assert type(get_llm_provider()) is cls


def test_no_provider_claims_vision_support_and_the_flag_is_off_by_default():
    assert settings.LLM_VISION_ENABLED is False
    for provider in (OpenAIProvider(), AnthropicProvider(), OllamaProvider(), GeminiProvider()):
        assert provider.supports_vision is False


def test_a_custom_provider_inherits_the_text_only_default():
    class Custom(LLMProvider):
        def complete_json(self, system_prompt, user_prompt):
            return {}

    assert Custom().supports_vision is False


# --------------------------------------------------------------------------- the other providers share the same behaviour

def test_ollama_and_gemini_use_their_own_models_and_the_shared_retry_and_fallback(mocker):
    mocker.patch.object(settings, "OLLAMA_MODEL", "llama-local")
    mocker.patch.object(settings, "GEMINI_MODEL", "gemini-x")
    for cls, model in [(OllamaProvider, "llama-local"), (GeminiProvider, "gemini-x")]:
        provider, completions = _provider("", '{"ok": true}', cls=cls)
        assert provider.complete_json("s", "u") == {"ok": True}
        assert completions.calls[0]["model"] == model and len(completions.calls) == 2


def test_anthropic_parses_fenced_json_retries_empty_and_logs_failures(mocker, caplog):
    mocker.patch.object(settings, "ANTHROPIC_MODEL", "claude-configured")
    replies = [
        SimpleNamespace(content=[SimpleNamespace(text="")]),
        SimpleNamespace(content=[SimpleNamespace(text='```json\n{"ok": true}\n```')]),
    ]
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return replies.pop(0)

    provider = AnthropicProvider()
    provider._client = SimpleNamespace(messages=SimpleNamespace(create=create))

    assert provider.complete_json("sys", "user") == {"ok": True}
    assert len(calls) == 2 and calls[0]["model"] == "claude-configured"

    failing = AnthropicProvider()
    failing._client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: (_ for _ in ()).throw(RuntimeError(SECRET))))
    with caplog.at_level(logging.WARNING, logger="app.services.ai.llm"):
        with pytest.raises(RuntimeError):
            failing.complete_json("s", "u")
    assert SECRET not in caplog.text and "llm_request_failed" in caplog.text


def test_module_exposes_no_hardcoded_model_identifier():
    source = open(llm.__file__, encoding="utf-8").read().lower()
    assert "claude-" not in source and "gpt-" not in source and "gemini-" not in source
