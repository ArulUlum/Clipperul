import json
from abc import ABC, abstractmethod

from app.core.config import settings


class LLMProvider(ABC):
    """Chat-completion abstraction so the LLM vendor can be swapped via config only."""

    @abstractmethod
    def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        """Send a prompt and parse the response as JSON. Raises ValueError on malformed output."""
        raise NotImplementedError


def _parse_json_response(raw_text: str) -> dict:
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM did not return valid JSON: {exc}") from exc


class OpenAIProvider(LLMProvider):
    def __init__(self) -> None:
        self._client = None

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(
                api_key=settings.OPENAI_API_KEY,
                base_url=settings.OPENAI_BASE_URL or "https://api.openai.com/v1",
                timeout=60.0
            )
        return self._client

    def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        client = self._get_client()
        response = client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
        )
        content = response.choices[0].message.content or "{}"
        return _parse_json_response(content)


class AnthropicProvider(LLMProvider):
    def __init__(self) -> None:
        self._client = None

    def _get_client(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY, timeout=60.0)
        return self._client

    def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        client = self._get_client()
        response = client.messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=4096,
            system=system_prompt + "\nRespond with JSON only, no prose, no markdown fences.",
            messages=[{"role": "user", "content": user_prompt}],
        )
        content = "".join(block.text for block in response.content if hasattr(block, "text"))
        return _parse_json_response(content)


class OllamaProvider(LLMProvider):
    """Local models via Ollama's OpenAI-compatible endpoint — no API key, no external calls.
    Requires Ollama running and the configured OLLAMA_MODEL already pulled (`ollama pull <model>`)."""

    def __init__(self) -> None:
        self._client = None

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI

            # Ollama ignores the key but the SDK requires a non-empty string. Local inference
            # can be genuinely slow on the first call (CPU-bound model load into memory), so
            # this gets a much more generous timeout than the cloud providers — bounded to
            # 5 minutes rather than the SDK's 600s+retries default, which could hang far longer.
            self._client = OpenAI(base_url=f"{settings.OLLAMA_BASE_URL}/v1", api_key="ollama", timeout=300.0)
        return self._client

    def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        client = self._get_client()
        response = client.chat.completions.create(
            model=settings.OLLAMA_MODEL,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt + "\nRespond with JSON only, no prose."},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
        )
        content = response.choices[0].message.content or "{}"
        return _parse_json_response(content)


class GeminiProvider(LLMProvider):
    """Google Gemini via its OpenAI-compatible endpoint — reuses the openai SDK instead of
    adding the google-genai dependency just for this."""

    def __init__(self) -> None:
        self._client = None

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
                timeout=60.0,
                max_retries=6,
            )
        return self._client

    def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        client = self._get_client()
        response = client.chat.completions.create(
            model=settings.GEMINI_MODEL,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
        )
        content = response.choices[0].message.content or "{}"
        return _parse_json_response(content)


def get_llm_provider() -> LLMProvider:
    if settings.AI_LLM_PROVIDER == "anthropic":
        return AnthropicProvider()
    if settings.AI_LLM_PROVIDER == "ollama":
        return OllamaProvider()
    if settings.AI_LLM_PROVIDER == "gemini":
        return GeminiProvider()
    return OpenAIProvider()
