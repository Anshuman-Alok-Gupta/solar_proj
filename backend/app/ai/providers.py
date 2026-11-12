"""LLM provider adapters behind one small interface.

* OpenAICompatibleProvider - any endpoint speaking the OpenAI chat-completions protocol
  (Groq, OpenRouter, Google Gemini's OpenAI endpoint, Together, Mistral, Ollama, LM Studio...).
  Uses the ``openai`` SDK with a custom ``base_url``. Tool calling via ``tools`` /
  ``tool_calls`` when the model supports it.
* AnthropicProvider - optional, via the official ``anthropic`` SDK.

Both return a normalised ``LLMResponse`` so the analyst loop is provider-independent.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from app.core.config import Settings

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    """User-presentable provider failure."""


class LLMToolsUnsupported(LLMError):
    """The model/endpoint rejected function calling; caller should fall back to context mode."""


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(slots=True)
class LLMResponse:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    raw_assistant_message: Any = None  # provider-native message to append to history


class BaseProvider:
    name = "base"
    supports_tools = True

    def chat(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        max_tokens: int,
    ) -> LLMResponse:
        raise NotImplementedError

    def tool_result_messages(self, assistant_msg: Any, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Messages to append after a tool round: the assistant turn + tool results."""
        raise NotImplementedError


# --------------------------------------------------------------------------- OpenAI-compatible


class OpenAICompatibleProvider(BaseProvider):
    name = "openai_compatible"

    def __init__(self, settings: Settings):
        from openai import OpenAI

        self.model = settings.llm_model or ""
        if not self.model:
            raise LLMError("LLM_MODEL is not set")
        fallbacks = [m.strip() for m in (settings.llm_fallback_models or "").split(",") if m.strip()]
        self.models = [self.model, *[m for m in fallbacks if m != self.model]]
        self.client = OpenAI(
            api_key=settings.llm_api_key or "not-needed",
            base_url=settings.llm_base_url or None,
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        )

    @staticmethod
    def _to_openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["input_schema"],
                },
            }
            for t in tools
        ]

    def chat(self, *, system, messages, tools, max_tokens) -> LLMResponse:
        from openai import (
            APIConnectionError,
            APIStatusError,
            APITimeoutError,
            AuthenticationError,
            BadRequestError,
            RateLimitError,
        )

        payload: dict[str, Any] = {
            "messages": [{"role": "system", "content": system}, *messages],
            "max_tokens": max_tokens,
            "temperature": 0.2,
        }
        if tools:
            payload["tools"] = self._to_openai_tools(tools)
            payload["tool_choice"] = "auto"
        try:
            resp = self._create_with_fallback(payload)
        except AuthenticationError as exc:
            raise LLMError("The LLM API key was rejected (check LLM_API_KEY)") from exc
        except RateLimitError as exc:
            raise LLMError("The LLM provider rate limit was reached; try again shortly") from exc
        except BadRequestError as exc:
            msg = str(exc)
            if tools and ("tool" in msg.lower() or "function" in msg.lower()):
                raise LLMToolsUnsupported(msg) from exc
            raise LLMError(f"The LLM provider rejected the request: {msg[:300]}") from exc
        except APITimeoutError as exc:
            raise LLMError("The LLM provider did not answer in time; try again shortly") from exc
        except APIConnectionError as exc:
            raise LLMError("Could not reach the LLM provider (check LLM_BASE_URL / network)") from exc
        except APIStatusError as exc:
            if exc.status_code >= 500:
                raise LLMError(
                    f"The LLM provider is overloaded (error {exc.status_code}); try again shortly"
                ) from exc
            raise LLMError(f"LLM provider error {exc.status_code}") from exc
        choice = resp.choices[0]
        msg = choice.message
        calls: list[ToolCall] = []
        for tc in msg.tool_calls or []:
            fn = getattr(tc, "function", None)
            if fn is None:
                continue
            try:
                args = json.loads(fn.arguments or "{}")
            except json.JSONDecodeError:
                args = {"_raw": fn.arguments}
            calls.append(ToolCall(id=tc.id, name=fn.name, arguments=args if isinstance(args, dict) else {}))
        usage = {}
        if resp.usage is not None:
            usage = {"input_tokens": resp.usage.prompt_tokens, "output_tokens": resp.usage.completion_tokens}
        return LLMResponse(
            text=(msg.content or "").strip(),
            tool_calls=calls,
            finish_reason=choice.finish_reason,
            usage=usage,
            raw_assistant_message=msg,
        )

    def _create_with_fallback(self, payload: dict[str, Any]):
        """Try each configured model in turn on transient provider failures.

        A provider instance serves one analyst turn; once a model answers it becomes sticky
        (moved to the front) so a tool loop never mixes models - Gemini thought signatures
        are only valid for the model that produced them.
        """
        from openai import APIStatusError, APITimeoutError, RateLimitError

        last_exc: Exception | None = None
        for model in list(self.models):
            try:
                resp = self.client.chat.completions.create(model=model, **payload)
            except (RateLimitError, APITimeoutError) as exc:
                last_exc = exc
            except APIStatusError as exc:
                if exc.status_code < 500:
                    raise
                last_exc = exc
            else:
                if model != self.models[0]:
                    log.warning("LLM model %s unavailable; answered by fallback %s", self.models[0], model)
                    self.models.remove(model)
                    self.models.insert(0, model)
                return resp
            log.info("LLM model %s failed (%s); trying next", model, type(last_exc).__name__)
        assert last_exc is not None
        raise last_exc

    @staticmethod
    def _tool_call_dict(tc: Any) -> dict[str, Any]:
        # Keep provider-specific extras (e.g. Gemini's extra_content.google.thought_signature,
        # which must be echoed back or the next request is rejected).
        d: dict[str, Any] = tc.model_dump(exclude_none=True) if hasattr(tc, "model_dump") else {}
        d["id"] = tc.id
        d["type"] = "function"
        d["function"] = {"name": tc.function.name, "arguments": tc.function.arguments or "{}"}
        return d

    def tool_result_messages(self, assistant_msg, results):
        out = [
            {
                "role": "assistant",
                "content": assistant_msg.content or None,
                "tool_calls": [self._tool_call_dict(tc) for tc in (assistant_msg.tool_calls or [])],
            }
        ]
        for r in results:
            out.append({"role": "tool", "tool_call_id": r["id"], "content": r["content"]})
        return out


# --------------------------------------------------------------------------- Anthropic


class AnthropicProvider(BaseProvider):
    name = "anthropic"

    def __init__(self, settings: Settings):
        import anthropic

        if not settings.anthropic_api_key:
            raise LLMError("ANTHROPIC_API_KEY is not set")
        self.model = settings.anthropic_model
        self.client = anthropic.Anthropic(
            api_key=settings.anthropic_api_key, timeout=settings.llm_timeout_seconds, max_retries=2
        )

    def chat(self, *, system, messages, tools, max_tokens) -> LLMResponse:
        import anthropic

        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = [{**t, "strict": True} for t in tools]
        try:
            resp = self.client.messages.create(**kwargs)
        except anthropic.AuthenticationError as exc:
            raise LLMError("Anthropic API key was rejected") from exc
        except anthropic.RateLimitError as exc:
            raise LLMError("Anthropic rate limit reached; try again shortly") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError("Could not reach the Anthropic API") from exc
        text = "\n".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
        calls = [
            ToolCall(id=b.id, name=b.name, arguments=b.input if isinstance(b.input, dict) else {})
            for b in resp.content
            if getattr(b, "type", None) == "tool_use"
        ]
        usage = {
            k: getattr(resp.usage, k, None)
            for k in ("input_tokens", "output_tokens", "cache_read_input_tokens")
        }
        finish = resp.stop_reason
        if finish == "refusal":
            text = text or "I can't help with that request."
        return LLMResponse(
            text=text, tool_calls=calls, finish_reason=finish, usage=usage, raw_assistant_message=resp.content
        )

    def tool_result_messages(self, assistant_msg, results):
        return [
            {"role": "assistant", "content": assistant_msg},
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": r["id"],
                        "content": r["content"],
                        **({"is_error": True} if r.get("is_error") else {}),
                    }
                    for r in results
                ],
            },
        ]


def make_provider(settings: Settings) -> BaseProvider:
    if settings.llm_provider == "anthropic":
        return AnthropicProvider(settings)
    if settings.llm_provider == "openai_compatible":
        return OpenAICompatibleProvider(settings)
    raise LLMError("LLM_PROVIDER is 'none' - the AI analyst is disabled")
