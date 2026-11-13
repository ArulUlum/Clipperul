import json
import logging
import re
from abc import ABC, abstractmethod

from app.core.config import settings

logger = logging.getLogger(__name__)


class LLMProvider(ABC):
    """Chat-completion abstraction so the LLM vendor can be swapped via config only."""

    @abstractmethod
    def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        """Send a prompt and parse the response as a JSON object. Raises ValueError when the reply is
        empty, isn't JSON, or isn't a JSON object; API/transport errors propagate as the SDK raised them."""
        raise NotImplementedError

    @property
    def supports_vision(self) -> bool:
        """Whether this provider is confirmed to accept image input. No provider is today, and nothing
        in the pipeline sends images — this is the switch a future visual pass would check first."""
        return False


# --------------------------------------------------------------------------- parsing

_FENCE = re.compile(r"^```[A-Za-z0-9_-]*[ \t]*\n?(.*?)\n?```[ \t]*$", re.DOTALL)


def _first_json_object(text: str) -> dict | None:
    """The first JSON object embedded in surrounding prose, if there is one."""
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            value, _ = decoder.raw_decode(text[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _parse_json_response(raw_text: str | None) -> dict:
    """The reply as a dict. Accepts a bare object, one wrapped in a Markdown fence, or one surrounded
    by prose; anything else raises ValueError."""
    if raw_text is None or not raw_text.strip():
        raise ValueError("LLM returned an empty response")
    text = raw_text.strip()
    fenced = _FENCE.match(text)
    if fenced:
        text = fenced.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        data = _first_json_object(text)
        if data is None:
            raise ValueError(f"LLM did not return valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"LLM returned JSON that is not an object (got {type(data).__name__})")
    return data


def _complete_with_json_retries(provider: str, model: str, request) -> dict:
    """Runs `request()` (returns the reply text) until the reply parses, at most 1 + LLM_JSON_RETRIES
    times. Only an unusable REPLY is retried here; request failures propagate at once (the SDK has
    already retried the transient ones, and the rest can't be fixed by asking again)."""
    attempts = 1 + max(0, settings.LLM_JSON_RETRIES)
    for attempt in range(1, attempts + 1):
        try:
            return _parse_json_response(request())
        except ValueError as exc:
            logger.warning(
                "llm_unusable_response provider=%s model=%s attempt=%d/%d reason=%s",
                provider, model, attempt, attempts, str(exc)[:120],
            )
            if attempt == attempts:
                raise
    raise AssertionError("unreachable")  # pragma: no cover


def _log_request_failure(provider: str, model: str, exc: Exception) -> None:
    # Type and HTTP status only: an error message can echo request details, never log those.
    logger.warning(
        "llm_request_failed provider=%s model=%s error_type=%s status=%s",
        provider, model, type(exc).__name__, getattr(exc, "status_code", None),
    )


# --------------------------------------------------------------------------- providers

class _OpenAICompatibleProvider(LLMProvider):
    """Shared chat-completions flow for OpenAI and anything that speaks its protocol (9router and other
    gateways, Ollama, Gemini's compatibility endpoint)."""

    name = "openai"

    def __init__(self) -> None:
        self._client = None
        # Not every route accepts response_format={"type": "json_object"}. Start by sending it, and stop
        # for good on this provider the first time the route says it can't (see _is_unsupported_json_mode).
        self._json_mode = True

    def _get_client(self):  # pragma: no cover - overridden
        raise NotImplementedError

    def _model(self) -> str:  # pragma: no cover - overridden
        raise NotImplementedError

    def _system(self, system_prompt: str) -> str:
        # Without response_format nothing forces JSON, so ask for it in the prompt.
        return system_prompt if self._json_mode else system_prompt + "\nRespond with JSON only, no prose."

    @staticmethod
    def _is_unsupported_json_mode(exc: Exception) -> bool:
        if getattr(exc, "status_code", None) not in (400, 422):
            return False
        message = str(exc).lower()
        return "response_format" in message or "json" in message

    def _request(self, system_prompt: str, user_prompt: str) -> str:
        client = self._get_client()
        model = self._model()

        def create():
            kwargs = {
                "model": model,
                "messages": [
                    {"role": "system", "content": self._system(system_prompt)},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.3,
            }
            if self._json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            return client.chat.completions.create(**kwargs)

        try:
            response = create()
        except Exception as exc:
            if self._json_mode and self._is_unsupported_json_mode(exc):
                logger.warning(
                    "llm_json_mode_unsupported provider=%s model=%s: continuing without response_format",
                    self.name, model,
                )
                self._json_mode = False
                response = create()  # the same model, only without the unsupported parameter
            else:
                _log_request_failure(self.name, model, exc)
                raise
        choices = getattr(response, "choices", None) or []
        return (choices[0].message.content if choices else None) or ""

    def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        return _complete_with_json_retries(self.name, self._model(), lambda: self._request(system_prompt, user_prompt))


class OpenAIProvider(_OpenAICompatibleProvider):
    name = "openai"

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(
                api_key=settings.OPENAI_API_KEY,
                base_url=settings.OPENAI_BASE_URL or "https://api.openai.com/v1",
                timeout=settings.LLM_TIMEOUT_SECONDS,
                max_retries=settings.LLM_MAX_RETRIES,
            )
        return self._client

    def _model(self) -> str:
        return settings.OPENAI_MODEL


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self) -> None:
        self._client = None

    def _get_client(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic(
                api_key=settings.ANTHROPIC_API_KEY,
                timeout=settings.LLM_TIMEOUT_SECONDS,
                max_retries=settings.LLM_MAX_RETRIES,
            )
        return self._client

    def _request(self, system_prompt: str, user_prompt: str) -> str:
        client = self._get_client()
        try:
            response = client.messages.create(
                model=settings.ANTHROPIC_MODEL,
                max_tokens=4096,
                system=system_prompt + "\nRespond with JSON only, no prose, no markdown fences.",
                messages=[{"role": "user", "content": user_prompt}],
            )
        except Exception as exc:
            _log_request_failure(self.name, settings.ANTHROPIC_MODEL, exc)
            raise
        return "".join(block.text for block in response.content if hasattr(block, "text"))

    def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        return _complete_with_json_retries(
            self.name, settings.ANTHROPIC_MODEL, lambda: self._request(system_prompt, user_prompt)
        )


class OllamaProvider(_OpenAICompatibleProvider):
    """Local models via Ollama's OpenAI-compatible endpoint — no API key, no external calls.
    Requires Ollama running and the configured OLLAMA_MODEL already pulled (`ollama pull <model>`)."""

    name = "ollama"

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI

            # Ollama ignores the key but the SDK requires a non-empty string. Local inference
            # can be genuinely slow on the first call (CPU-bound model load into memory), so
            # this gets a much more generous timeout than the cloud providers — bounded to
            # 5 minutes rather than the SDK's 600s+retries default, which could hang far longer.
            self._client = OpenAI(base_url=f"{settings.OLLAMA_BASE_URL}/v1", api_key="ollama", timeout=300.0)
        return self._client

    def _model(self) -> str:
        return settings.OLLAMA_MODEL

    def _system(self, system_prompt: str) -> str:
        return system_prompt + "\nRespond with JSON only, no prose."


class GeminiProvider(_OpenAICompatibleProvider):
    """Google Gemini via its OpenAI-compatible endpoint — reuses the openai SDK instead of
    adding the google-genai dependency just for this."""

    name = "gemini"

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI

            # Free-tier Gemini keys hit per-minute rate limits (429) easily — each video makes
            # 2 calls (category detection + highlight scoring), and the SDK's default 2 retries
            # often aren't enough to outlast a 429 before the per-minute window resets, silently
            # dropping the whole video to heuristic-only scoring. More retries (the SDK already
            # does exponential backoff and honors Retry-After) meaningfully cuts how often that
            # fallback triggers instead of failing fast.
            self._client = OpenAI(
                base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                api_key=settings.GEMINI_API_KEY,
                timeout=settings.LLM_TIMEOUT_SECONDS,
                max_retries=6,
            )
        return self._client

    def _model(self) -> str:
        return settings.GEMINI_MODEL


def get_llm_provider() -> LLMProvider:
    if settings.AI_LLM_PROVIDER == "anthropic":
        return AnthropicProvider()
    if settings.AI_LLM_PROVIDER == "ollama":
        return OllamaProvider()
    if settings.AI_LLM_PROVIDER == "gemini":
        return GeminiProvider()
    return OpenAIProvider()
