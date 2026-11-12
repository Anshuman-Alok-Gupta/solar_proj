"""Hosting / provider compatibility: hosted DATABASE_URL forms and Gemini thought signatures."""

from types import SimpleNamespace

import pytest
from openai.types.chat import ChatCompletionMessage

from app.ai.providers import OpenAICompatibleProvider
from app.core.config import Settings


@pytest.mark.parametrize(
    "raw",
    ["postgres://u:p@h:5432/db", "postgresql://u:p@h:5432/db", "postgresql+psycopg://u:p@h:5432/db"],
)
def test_database_url_normalised_to_psycopg(raw):
    assert Settings(database_url=raw).database_url == "postgresql+psycopg://u:p@h:5432/db"


def test_tool_round_echoes_gemini_thought_signature():
    msg = ChatCompletionMessage.model_validate(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "get_generation_summary", "arguments": "{}"},
                    "extra_content": {"google": {"thought_signature": "sig-abc"}},
                }
            ],
        }
    )
    provider = OpenAICompatibleProvider.__new__(OpenAICompatibleProvider)
    out = provider.tool_result_messages(msg, [{"id": "call_1", "content": "{}"}])
    tc = out[0]["tool_calls"][0]
    assert tc["extra_content"] == {"google": {"thought_signature": "sig-abc"}}
    assert tc["function"] == {"name": "get_generation_summary", "arguments": "{}"}
    assert out[1] == {"role": "tool", "tool_call_id": "call_1", "content": "{}"}


def test_tool_round_plain_openai_message_unchanged():
    tc = SimpleNamespace(id="c", function=SimpleNamespace(name="n", arguments=None))
    d = OpenAICompatibleProvider._tool_call_dict(tc)
    assert d == {"id": "c", "type": "function", "function": {"name": "n", "arguments": "{}"}}


class _FakeCompletions:
    """Scripted chat.completions: model -> exception to raise, or a response object."""

    def __init__(self, script):
        self.script, self.calls = script, []

    def create(self, model, **_):
        self.calls.append(model)
        out = self.script[model]
        if isinstance(out, Exception):
            raise out
        return out


def _status_error(code):
    import httpx
    from openai import APIStatusError

    req = httpx.Request("POST", "https://example.invalid/chat/completions")
    return APIStatusError(f"error {code}", response=httpx.Response(code, request=req), body=None)


def _provider(models, script):
    p = OpenAICompatibleProvider.__new__(OpenAICompatibleProvider)
    p.models = list(models)
    p.client = SimpleNamespace(chat=SimpleNamespace(completions=_FakeCompletions(script)))
    return p


def test_fallback_on_overload_and_sticky_model():
    ok = SimpleNamespace(name="ok")
    p = _provider(["a", "b", "c"], {"a": _status_error(503), "b": ok, "c": ok})
    assert p._create_with_fallback({}) is ok
    assert p.models[0] == "b"  # sticky for the rest of the turn
    assert p._create_with_fallback({}) is ok
    assert p.client.chat.completions.calls == ["a", "b", "b"]


def test_client_errors_do_not_fall_back():
    p = _provider(["a", "b"], {"a": _status_error(400), "b": SimpleNamespace()})
    with pytest.raises(Exception, match="400"):
        p._create_with_fallback({})
    assert p.client.chat.completions.calls == ["a"]


def test_all_models_down_raises_last_error():
    p = _provider(["a", "b"], {"a": _status_error(503), "b": _status_error(502)})
    with pytest.raises(Exception, match="502"):
        p._create_with_fallback({})
